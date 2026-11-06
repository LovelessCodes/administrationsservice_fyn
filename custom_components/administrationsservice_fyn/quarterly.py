"""Quarterly cost estimates for Administrationsservice Fyn."""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import ASFApiClient
from .const import (
    CONF_ELECTRICITY_PRICE,
    CONF_HEAT_PRICE,
    CONF_HEAT_WATER_PRICE,
    CONF_WATER_PRICE,
    DEFAULT_ELECTRICITY_PRICE,
    DEFAULT_HEAT_PRICE,
    DEFAULT_HEAT_WATER_PRICE,
    DEFAULT_WATER_PRICE,
    DOMAIN,
)
from .statistics import statistic_id

_LOGGER = logging.getLogger(__name__)

QUARTER_UPDATE_INTERVAL = timedelta(hours=6)
WATER_TOTAL_STATISTIC_ID = "sensor.axioma_w1_watermeter_total"
_QUARTER_MONTHS = {1: (1, 2, 3), 2: (4, 5, 6), 3: (7, 8, 9), 4: (10, 11, 12)}


def _as_float(value: Any) -> float | None:
    """Return a float or None."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _quarter_start(year: int, quarter: int) -> date:
    """Return the first day of a quarter."""
    return date(year, 3 * (quarter - 1) + 1, 1)


def _quarter_end(year: int, quarter: int) -> date:
    """Return the last day of a quarter."""
    month = 3 * quarter
    return date(year, month, calendar.monthrange(year, month)[1])


def _month_key_from_timestamp(timestamp: float) -> tuple[int, int]:
    """Return the local year and month for a statistic timestamp."""
    if timestamp > 100_000_000_000:
        timestamp /= 1000
    local = dt_util.as_local(datetime.fromtimestamp(timestamp, tz=timezone.utc))
    return local.year, local.month


def _parse_api_months(data: Mapping[str, Any] | None) -> dict[tuple[int, int], float]:
    """Parse monthly items from a year consumption response."""
    months: dict[tuple[int, int], float] = {}
    if not data:
        return months
    for item in data.get("items") or []:
        raw_date = item.get("date")
        value = _as_float(item.get("value"))
        if not raw_date or value is None:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
        except ValueError:
            continue
        months[(parsed.year, parsed.month)] = value
    return months


class QuarterlyCostCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Build quarterly cost estimates from consumption history."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: ASFApiClient,
        sensors: Mapping[str, Mapping[str, Any]],
        options: Mapping[str, Any],
    ) -> None:
        """Initialize the quarterly cost coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} quarterly costs",
            update_interval=QUARTER_UPDATE_INTERVAL,
        )
        self.api = api
        self.options = options
        self.sensors = {str(data.get("sensor_key")): data for data in sensors.values()}
        self._year_cache: dict[tuple[str, int], dict[tuple[int, int], float]] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        """Collect monthly consumption and build quarterly costs."""
        now = dt_util.now()
        current_year = now.year
        current_quarter = (now.month - 1) // 3 + 1

        monthly: dict[str, dict[tuple[int, int], float]] = {
            "electricity": {},
            "heat": {},
            "water": {},
            "hot_water": {},
        }

        for product, sensor_key in (("electricity", "electricity"), ("heat", "heat")):
            if sensor_key not in self.sensors:
                continue
            for year in (current_year - 2, current_year - 1, current_year):
                cache_key = (product, year)
                if cache_key in self._year_cache and year < current_year:
                    monthly[product].update(self._year_cache[cache_key])
                    continue
                try:
                    data = await self.api.async_get_consumption(
                        self.sensors[sensor_key]["customer_number"],
                        self.sensors[sensor_key]["estate_id"],
                        self.sensors[sensor_key]["meter_id"],
                        self.sensors[sensor_key]["installation_id"],
                        self.sensors[sensor_key]["unit"],
                        "year",
                        {"year": year},
                    )
                    values = _parse_api_months(data)
                except Exception:  # noqa: BLE001 - an optional estimate must not fail setup
                    _LOGGER.debug(
                        "Could not fetch %s consumption for %s",
                        product,
                        year,
                        exc_info=True,
                    )
                    values = self._year_cache.get(cache_key, {})
                self._year_cache[cache_key] = values
                monthly[product].update(values)

        monthly["water"], monthly["hot_water"] = await self._async_stat_months()

        quarters = self._build_quarters(monthly, now, current_quarter)
        current = next(
            (
                item
                for item in quarters
                if item["year"] == current_year and item["quarter"] == current_quarter
            ),
            None,
        )
        tariffs = {
            "electricity": float(
                self.options.get(CONF_ELECTRICITY_PRICE, DEFAULT_ELECTRICITY_PRICE)
            ),
            "water": float(self.options.get(CONF_WATER_PRICE, DEFAULT_WATER_PRICE)),
            "heat": float(self.options.get(CONF_HEAT_PRICE, DEFAULT_HEAT_PRICE)),
            "hot_water": float(
                self.options.get(CONF_HEAT_WATER_PRICE, DEFAULT_HEAT_WATER_PRICE)
            ),
        }
        annual = sum(
            item["total"]
            for item in quarters
            if item["year"] == current_year and item["total"] is not None
        )
        return {
            "quarters": quarters,
            "current_quarter": current["label"] if current else None,
            "current_quarter_to_date": (
                current.get("total_to_date") if current else None
            ),
            "current_quarter_projected": current.get("total") if current else None,
            "current_quarter_days_elapsed": (
                current.get("days_elapsed") if current else None
            ),
            "current_quarter_days_total": (
                current.get("days_total") if current else None
            ),
            "annual_forecast": round(annual, 2),
            "tariffs": tariffs,
            "currency": "DKK",
            "method": (
                "Variable costs at current tariffs. Electricity uses the configured "
                "average price; the current quarter is projected from the run rate. "
                "Fixed annual fees are not included."
            ),
            "updated": now.isoformat(),
        }

    async def _async_stat_months(
        self,
    ) -> tuple[dict[tuple[int, int], float], dict[tuple[int, int], float]]:
        """Return monthly water and hot-water consumption from recorder statistics."""
        current_year = dt_util.now().year
        start = datetime(current_year - 2, 1, 1, tzinfo=timezone.utc)
        end = dt_util.utcnow()
        statistic_ids = {WATER_TOTAL_STATISTIC_ID}
        if "water" in self.sensors:
            statistic_ids.add(statistic_id(self.sensors["water"]))
        if "hot_water" in self.sensors:
            statistic_ids.add(statistic_id(self.sensors["hot_water"]))

        try:
            recorder = get_instance(self.hass)
            stats = await recorder.async_add_executor_job(
                statistics_during_period,
                self.hass,
                start,
                end,
                statistic_ids,
                "month",
                None,
                {"change"},
            )
        except Exception as err:  # noqa: BLE001 - optional history
            _LOGGER.debug("Could not read water statistics: %s", err)
            return {}, {}

        def convert(stat_id: str) -> dict[tuple[int, int], float]:
            values: dict[tuple[int, int], float] = {}
            for row in stats.get(stat_id, []):
                timestamp = row.get("start")
                change = _as_float(row.get("change"))
                if timestamp is None or change is None:
                    continue
                values[_month_key_from_timestamp(timestamp)] = change
            return values

        water = convert(WATER_TOTAL_STATISTIC_ID)
        if not water and "water" in self.sensors:
            water = convert(statistic_id(self.sensors["water"]))
        hot_water = (
            convert(statistic_id(self.sensors["hot_water"]))
            if "hot_water" in self.sensors
            else {}
        )
        return water, hot_water

    def _build_quarters(
        self,
        monthly: Mapping[str, Mapping[tuple[int, int], float]],
        now: datetime,
        current_quarter: int,
    ) -> list[dict[str, Any]]:
        """Build quarter rows with actual and projected values."""
        current_year = now.year
        today = now.date()
        quarters: list[dict[str, Any]] = []

        for year in (current_year - 2, current_year - 1, current_year):
            for quarter in (1, 2, 3, 4):
                if year == current_year and quarter > current_quarter:
                    continue
                if year < current_year and not any(
                    (year, month) in monthly.get(product, {})
                    for product in ("electricity", "heat")
                    for month in _QUARTER_MONTHS[quarter]
                ):
                    continue

                is_current = year == current_year and quarter == current_quarter
                if is_current:
                    values, to_date, days_elapsed, days_total = self._current_values(
                        monthly, year, quarter, today
                    )
                    status = "projected"
                else:
                    values = {
                        product: self._actual_value(
                            monthly.get(product, {}), year, quarter
                        )
                        for product in ("electricity", "heat", "water", "hot_water")
                    }
                    to_date = None
                    days_elapsed = days_total = None
                    status = "actual"

                costs = {
                    "electricity": self._cost("electricity", values["electricity"]),
                    "heating": self._cost("heat", values["heat"]),
                    "water": self._cost("water", values["water"]),
                    "hot_water": self._cost("hot_water", values["hot_water"]),
                }
                total_values = [value for value in costs.values() if value is not None]
                total = round(sum(total_values), 2) if total_values else None
                to_date_costs = (
                    {
                        "electricity": self._cost(
                            "electricity", to_date["electricity"]
                        ),
                        "heating": self._cost("heat", to_date["heat"]),
                        "water": self._cost("water", to_date["water"]),
                        "hot_water": self._cost("hot_water", to_date["hot_water"]),
                    }
                    if to_date
                    else {}
                )
                to_date_total_values = [
                    value for value in to_date_costs.values() if value is not None
                ]
                to_date_total = (
                    round(sum(to_date_total_values), 2)
                    if to_date_total_values
                    else None
                )
                missing = [
                    product
                    for product in ("electricity", "heat", "water", "hot_water")
                    if values[product] is None
                ]
                quarters.append(
                    {
                        "label": f"{year} Q{quarter}",
                        "start": _quarter_start(year, quarter).isoformat(),
                        "year": year,
                        "quarter": quarter,
                        "status": status,
                        "partial": bool(missing),
                        "missing": missing,
                        "electricity": costs["electricity"],
                        "heating": costs["heating"],
                        "water": costs["water"],
                        "hot_water": costs["hot_water"],
                        "total": total,
                        "total_to_date": to_date_total,
                        "electricity_kwh": values["electricity"],
                        "heating_gj": values["heat"],
                        "water_m3": values["water"],
                        "hot_water_m3": values["hot_water"],
                        "days_elapsed": days_elapsed,
                        "days_total": days_total,
                    }
                )
        return quarters

    def _current_values(
        self,
        monthly: Mapping[str, Mapping[tuple[int, int], float]],
        year: int,
        quarter: int,
        today: date,
    ) -> tuple[dict[str, float | None], dict[str, float | None], int, int]:
        """Return actual-to-date and projected consumption for the current quarter."""
        start = _quarter_start(year, quarter)
        end = _quarter_end(year, quarter)
        days_elapsed = max(1, (today - start).days + 1)
        days_total = (end - start).days + 1
        months = [month for month in _QUARTER_MONTHS[quarter] if month <= today.month]

        values: dict[str, float | None] = {}
        to_date: dict[str, float | None] = {}
        for product in ("electricity", "heat", "water", "hot_water"):
            source = monthly.get(product, {})
            present = [month for month in months if (year, month) in source]
            if not present:
                to_date[product] = None
                values[product] = None
                continue
            actual = sum(source[(year, month)] for month in present)
            to_date[product] = actual

            if product in ("water", "hot_water"):
                previous = (
                    self._actual_value(source, year, quarter - 1)
                    if quarter > 1
                    else None
                )
                if previous is None:
                    previous = self._actual_value(source, year - 1, 4)
                projected = previous if previous is not None else actual
            else:
                projected = actual / days_elapsed * days_total
            values[product] = projected

        return values, to_date, days_elapsed, days_total

    @staticmethod
    def _actual_value(
        monthly: Mapping[tuple[int, int], float], year: int, quarter: int
    ) -> float | None:
        """Return a completed quarter's consumption, or None if incomplete."""
        months = _QUARTER_MONTHS[quarter]
        if not all((year, month) in monthly for month in months):
            return None
        return sum(monthly[(year, month)] for month in months)

    def _cost(self, product: str, value: float | None) -> float | None:
        """Convert consumption to cost."""
        if value is None:
            return None
        option = {
            "electricity": CONF_ELECTRICITY_PRICE,
            "heat": CONF_HEAT_PRICE,
            "water": CONF_WATER_PRICE,
            "hot_water": CONF_HEAT_WATER_PRICE,
        }[product]
        default = {
            "electricity": DEFAULT_ELECTRICITY_PRICE,
            "heat": DEFAULT_HEAT_PRICE,
            "water": DEFAULT_WATER_PRICE,
            "hot_water": DEFAULT_HEAT_WATER_PRICE,
        }[product]
        tariff = float(self.options.get(option, default))
        return round(value * tariff, 2)
