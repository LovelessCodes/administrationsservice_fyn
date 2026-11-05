"""Data update coordinator for Administrationsservice Fyn."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    ASFApiClient,
    ASFApiError,
    ASFAuthError,
    as_float,
    clean_refresh_token,
    reading_value,
)
from .const import (
    API_UNIT_GJ,
    API_UNIT_KWH,
    API_UNIT_M3,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN_EXPIRES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PRODUCT_EL,
    PRODUCT_GAS,
    PRODUCT_HEAT,
    PRODUCT_WATER,
    SENSOR_ELECTRICITY,
    SENSOR_GAS,
    SENSOR_HEAT,
    SENSOR_HOT_WATER,
    SENSOR_WATER,
    UNIT_GJ,
    UNIT_KWH,
    UNIT_M3,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SensorSpec:
    """Description of a sensor to create for a product."""

    key: str
    unit: str
    api_unit: str
    device_class: SensorDeviceClass | None
    icon: str
    reading: float | None = None
    reading_date: str | None = None


def _product_type(product: dict[str, Any]) -> str:
    """Return the normalised product type."""
    return str(
        product.get("type")
        or product.get("category")
        or product.get("productType")
        or ""
    ).lower()


def _product_specs(product: dict[str, Any]) -> list[SensorSpec]:
    """Return the sensors that belong to a product."""
    product_type = _product_type(product)
    m3_value, m3_date = reading_value(product, "latestReadingsM3")
    gj_value, gj_date = reading_value(product, "latestReadingsGJ")
    has_m3 = "latestReadingsM3" in product

    if product_type == PRODUCT_EL:
        return [
            SensorSpec(
                SENSOR_ELECTRICITY,
                UNIT_KWH,
                API_UNIT_KWH,
                SensorDeviceClass.ENERGY,
                "mdi:flash",
            )
        ]

    if product_type == PRODUCT_HEAT:
        specs = [
            SensorSpec(
                SENSOR_HEAT,
                UNIT_GJ,
                API_UNIT_GJ,
                SensorDeviceClass.ENERGY,
                "mdi:radiator",
                gj_value,
                gj_date,
            )
        ]
        if has_m3:
            specs.append(
                SensorSpec(
                    SENSOR_HOT_WATER,
                    UNIT_M3,
                    API_UNIT_M3,
                    SensorDeviceClass.WATER,
                    "mdi:water-boiler",
                    m3_value,
                    m3_date,
                )
            )
        return specs

    if product_type == PRODUCT_WATER:
        return [
            SensorSpec(
                SENSOR_WATER,
                UNIT_M3,
                API_UNIT_M3,
                SensorDeviceClass.WATER,
                "mdi:water",
                m3_value,
                m3_date,
            )
        ]

    if product_type == PRODUCT_GAS:
        return [
            SensorSpec(
                SENSOR_GAS,
                UNIT_M3,
                API_UNIT_M3,
                SensorDeviceClass.GAS,
                "mdi:fire",
                m3_value,
                m3_date,
            )
        ]

    return []


def _is_active_product(product: dict[str, Any]) -> bool:
    """Return True if a product is still active."""
    state = str(product.get("subscriptionState") or "").strip().lower()
    if state and state != "active":
        return False

    today = dt_util.now().date().isoformat()
    for key in ("toDate", "endDate"):
        value = product.get(key)
        if not value:
            continue
        text = str(value)
        if text[:4] in ("0000", "0001"):
            continue
        if text[:10] < today:
            return False

    return True


def _estate_name(estate: dict[str, Any]) -> str:
    """Return a readable estate name."""
    return str(
        estate.get("name")
        or estate.get("address")
        or f"Estate {estate.get('id', 'unknown')}"
    )


def _parse_reading_date(value: str | None) -> date | None:
    """Parse the latest physical reading date from the API."""
    if not value:
        return None
    text = str(value)
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _months_after(last: date, current: date) -> list[tuple[int, int]]:
    """Return the full months after ``last`` up to and including ``current``."""
    months: list[tuple[int, int]] = []
    year, month = last.year, last.month
    while (year, month) < (current.year, current.month):
        month += 1
        if month > 12:
            month = 1
            year += 1
        months.append((year, month))
    return months


class ASFDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that fetches customers, estates, products and readings."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        scan_interval = int(
            entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=scan_interval),
        )
        self.entry = entry
        self.api = ASFApiClient(
            async_get_clientsession(hass),
            clean_refresh_token(entry.data[CONF_REFRESH_TOKEN]),
            entry.data.get(CONF_ACCESS_TOKEN),
            entry.data.get(CONF_TOKEN_EXPIRES),
            self._persist_tokens,
        )

    def _persist_tokens(
        self, refresh_token: str, access_token: str, expires_at: float
    ) -> None:
        """Persist rotated tokens to the config entry."""
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={
                **self.entry.data,
                CONF_REFRESH_TOKEN: refresh_token,
                CONF_ACCESS_TOKEN: access_token,
                CONF_TOKEN_EXPIRES: expires_at,
            },
        )

    async def _optional(self, awaitable: Any) -> Any:
        """Run an optional API request and ignore non-auth failures."""
        try:
            return await awaitable
        except ASFAuthError:
            raise
        except ASFApiError as err:
            _LOGGER.debug("Optional API request failed: %s", err)
            return None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data and convert authentication failures to reauth."""
        try:
            return await self._async_fetch_data()
        except ASFAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err

    async def _async_fetch_data(self) -> dict[str, Any]:
        """Fetch data for every customer, estate and product."""
        try:
            customers = await self.api.async_get_customers()
        except ASFAuthError:
            raise
        except ASFApiError as err:
            raise UpdateFailed(str(err)) from err

        if not customers:
            raise UpdateFailed(
                "No customers were found for this account. Check the ASF login."
            )

        data_date = dt_util.now().date()
        sensors: dict[str, dict[str, Any]] = {}
        profiles: list[dict[str, Any]] = []
        estate_count = 0

        for customer in customers:
            customer_number = str(customer.get("customerNumber") or "")
            if not customer_number:
                continue

            profile: dict[str, Any] = {
                "customer_number": customer_number,
                "estates": [],
            }
            profiles.append(profile)

            try:
                estates = await self.api.async_get_estates(customer_number)
            except ASFAuthError:
                raise
            except ASFApiError as err:
                _LOGGER.warning(
                    "Could not fetch estates for customer %s: %s",
                    customer_number,
                    err,
                )
                continue

            for estate in estates:
                estate_id = estate.get("id")
                if estate_id is None:
                    continue

                estate_info: dict[str, Any] = {
                    "estate_id": estate_id,
                    "estate_name": _estate_name(estate),
                    "is_active": bool(estate.get("isActive", True)),
                    "products": [],
                }
                profile["estates"].append(estate_info)

                if not estate.get("isActive", True):
                    continue

                estate_count += 1

                try:
                    products = await self.api.async_get_products(
                        customer_number, estate_id
                    )
                except ASFAuthError:
                    raise
                except ASFApiError as err:
                    _LOGGER.warning(
                        "Could not fetch products for estate %s: %s",
                        estate_id,
                        err,
                    )
                    continue

                for product in products:
                    active = _is_active_product(product)
                    installation_id = product.get("installationId")
                    meter_id = product.get("meterNumber") or product.get("meterId")
                    product_type = _product_type(product)

                    # The list endpoint has no latest readings or filters.
                    # The detail endpoint adds latestReadingsGJ/M3 and
                    # availableFilters, which decide which periods work.
                    if active and installation_id is not None and meter_id is not None:
                        detail = await self._optional(
                            self.api.async_get_product(
                                customer_number,
                                str(installation_id),
                                str(product.get("category") or product_type),
                                str(meter_id),
                                product.get("tarifArt"),
                            )
                        )
                        if detail:
                            product = {**product, **detail}
                            active = _is_active_product(product)
                            installation_id = (
                                product.get("installationId") or installation_id
                            )
                            meter_id = (
                                product.get("meterNumber")
                                or product.get("meterId")
                                or meter_id
                            )
                            product_type = _product_type(product)

                    specs = _product_specs(product) if active else []
                    product_info: dict[str, Any] = {
                        "product_name": product.get("productName"),
                        "product_type": product_type,
                        "subscription_state": product.get("subscriptionState"),
                        "installation_id": installation_id,
                        "meter_id": meter_id,
                        "active": active,
                        "available_filters": product.get("availableFilters"),
                        "latest_readings": {
                            "gj": product.get("latestReadingsGJ"),
                            "m3": product.get("latestReadingsM3"),
                        },
                        "sensor_keys": [],
                    }
                    estate_info["products"].append(product_info)

                    if not active:
                        continue

                    if installation_id is None or meter_id is None:
                        _LOGGER.debug(
                            "Skipping product without meter or installation: %s",
                            product.get("productName"),
                        )
                        continue

                    product_info["sensor_keys"] = [spec.key for spec in specs]

                    for spec in specs:
                        sensor_data = await self._fetch_sensor(
                            customer=customer,
                            customer_number=customer_number,
                            estate=estate,
                            estate_id=estate_id,
                            product=product,
                            installation_id=str(installation_id),
                            meter_id=str(meter_id),
                            spec=spec,
                            data_date=data_date,
                        )
                        if sensor_data is None:
                            continue

                        key = (
                            f"{customer_number}_{estate_id}_"
                            f"{installation_id}_{meter_id}_{spec.key}"
                        )
                        sensors[key] = sensor_data

        _LOGGER.debug("Discovered ASF profiles: %s", profiles)

        if not sensors:
            raise UpdateFailed(
                "No active electricity, heat, water or gas meters were found "
                "on this account."
            )

        return {
            "sensors": sensors,
            "profiles": profiles,
            "customer_count": len(customers),
            "estate_count": estate_count,
            "data_date": data_date.isoformat(),
        }

    async def _fetch_sensor(
        self,
        *,
        customer: dict[str, Any],
        customer_number: str,
        estate: dict[str, Any],
        estate_id: Any,
        product: dict[str, Any],
        installation_id: str,
        meter_id: str,
        spec: SensorSpec,
        data_date: Any,
    ) -> dict[str, Any] | None:
        """Fetch totals and build the data for one sensor."""
        if spec.reading is not None:
            reading = spec.reading
            reading_date = spec.reading_date
        else:
            reading = None
            reading_date = None

        filters = {
            str(item).lower() for item in (product.get("availableFilters") or [])
        }
        # Products such as water and heat expose only a default lifetime
        # series. The month/year endpoints return 404 for those.
        default_only = filters == {"default"}
        total_period = "default" if default_only else "total"

        total = await self._optional(
            self.api.async_get_total(
                customer_number,
                estate_id,
                meter_id,
                installation_id,
                spec.api_unit,
                total_period,
            )
        )
        if total is None and total_period == "default":
            # Some deployments expose only the older total period.
            total = await self._optional(
                self.api.async_get_total(
                    customer_number,
                    estate_id,
                    meter_id,
                    installation_id,
                    spec.api_unit,
                    "total",
                )
            )
        elif total is None:
            # The detail request failed or the period is unsupported. Water
            # and heat often only have the default lifetime series.
            total = await self._optional(
                self.api.async_get_total(
                    customer_number,
                    estate_id,
                    meter_id,
                    installation_id,
                    spec.api_unit,
                    "default",
                )
            )

        if default_only:
            month_total = None
            year_total = None
        else:
            month_total = await self._optional(
                self.api.async_get_month(
                    customer_number,
                    estate_id,
                    meter_id,
                    installation_id,
                    spec.api_unit,
                    data_date.year,
                    data_date.month,
                )
            )
            year_total = await self._optional(
                self.api.async_get_year(
                    customer_number,
                    estate_id,
                    meter_id,
                    installation_id,
                    spec.api_unit,
                    data_date.year,
                )
            )

        product_type = _product_type(product)
        meter_reading = as_float(reading)
        last_reading_date = _parse_reading_date(reading_date)
        added_months: dict[str, float] = {}

        if total is None and meter_reading is None:
            return None

        if total is None:
            value = meter_reading
            source = "meter_reading"
        elif (
            product_type == PRODUCT_EL
            or last_reading_date is None
            or last_reading_date.year < data_date.year
        ):
            # The lifetime total ends before the current year (for example,
            # an annual heat reading on 31 December). Year-to-date covers
            # the previous months and month-to-date covers the current
            # month, which the API updates daily (with a one-day lag).
            value = total + (year_total or 0.0) + (month_total or 0.0)
            source = "cumulative_total"
        else:
            # The lifetime total already includes the current year up to
            # the latest physical reading. Add every full month after that
            # reading so a monthly water reading still produces a current
            # cumulative value. This avoids adding year-to-date twice.
            extra = 0.0
            for year, month in _months_after(last_reading_date, data_date):
                month_key = f"{year:04d}-{month:02d}"
                if year == data_date.year and month == data_date.month:
                    month_value = month_total
                else:
                    month_value = await self._optional(
                        self.api.async_get_month(
                            customer_number,
                            estate_id,
                            meter_id,
                            installation_id,
                            spec.api_unit,
                            year,
                            month,
                        )
                    )
                if month_value is not None:
                    added_months[month_key] = as_float(month_value)
                    extra += month_value
            value = total + extra
            source = "cumulative_total"

        return {
            "sensor_key": spec.key,
            "unit": spec.unit,
            "api_unit": spec.api_unit,
            "device_class": spec.device_class,
            "icon": spec.icon,
            "value": as_float(value),
            "meter_reading": meter_reading,
            "last_reading": reading_date,
            "lifetime_consumption": as_float(total),
            "month_to_date": as_float(month_total),
            "year_to_date": as_float(year_total),
            "added_months": added_months or None,
            "value_source": source,
            "customer": customer,
            "customer_number": customer_number,
            "estate": estate,
            "estate_id": estate_id,
            "estate_name": _estate_name(estate),
            "product": product,
            "product_name": product.get("productName"),
            "product_type": _product_type(product),
            "installation_id": installation_id,
            "meter_id": meter_id,
            "meter_number": product.get("meterNumber"),
            "customer_meter_number": product.get("customerMeterNumber"),
            "tariff": product.get("tarifArt"),
            "subscription_state": product.get("subscriptionState"),
        }
