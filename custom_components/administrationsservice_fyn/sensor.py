"""Sensor platform for Administrationsservice Fyn."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_HEAT_PRICE,
    CONF_HEAT_WATER_PRICE,
    CONF_WATER_PRICE,
    DEFAULT_HEAT_PRICE,
    DEFAULT_HEAT_WATER_PRICE,
    DEFAULT_WATER_PRICE,
    DOMAIN,
    HEAT_PRICE_PDF_URL,
    PRICE_VALID_FROM,
    SENSOR_ELECTRICITY,
    SENSOR_GAS,
    SENSOR_HEAT,
    SENSOR_HOT_WATER,
    SENSOR_WATER,
    SSO_LOGIN_URL,
    WATER_PRICE_URL,
)
from .coordinator import ASFDataUpdateCoordinator
from .quarterly import QuarterlyCostCoordinator
from .statistics import statistic_id

SENSOR_LABELS = {
    SENSOR_ELECTRICITY: "Electricity",
    SENSOR_HEAT: "Heating",
    SENSOR_HOT_WATER: "Hot water",
    SENSOR_WATER: "Water",
    SENSOR_GAS: "Gas",
}


@dataclass(frozen=True, slots=True)
class PriceSpec:
    """Description of a tariff sensor."""

    key: str
    option: str
    default: float
    unit: str
    icon: str
    source: str
    attributes: dict[str, Any] = field(default_factory=dict)


WATER_PRICE = PriceSpec(
    key="water_price",
    option=CONF_WATER_PRICE,
    default=DEFAULT_WATER_PRICE,
    unit="DKK/m³",
    icon="mdi:water",
    source=WATER_PRICE_URL,
    attributes={
        "fixed_annual_meter_fee": 600.0,
        "wastewater_connection_fee": 750.0,
        "note": (
            "Variable water and wastewater price including VAT. "
            "Fixed annual fees are not included."
        ),
    },
)
HEAT_PRICE = PriceSpec(
    key="heat_price",
    option=CONF_HEAT_PRICE,
    default=DEFAULT_HEAT_PRICE,
    unit="DKK/GJ",
    icon="mdi:radiator",
    source=HEAT_PRICE_PDF_URL,
    attributes={
        "fixed_annual_meter_fee": 600.0,
        "effect_contribution_per_m2": 12.5,
        "effect_contribution_note": "1-1,000 m² BBR area, including VAT",
        "heat_water_price": DEFAULT_HEAT_WATER_PRICE,
        "note": (
            "Variable district heating energy price including VAT. "
            "Fixed fees are not included."
        ),
    },
)
HEAT_WATER_PRICE = PriceSpec(
    key="heat_water_price",
    option=CONF_HEAT_WATER_PRICE,
    default=DEFAULT_HEAT_WATER_PRICE,
    unit="DKK/m³",
    icon="mdi:water-thermometer",
    source=HEAT_PRICE_PDF_URL,
    attributes={
        "note": (
            "Price for measured district heating water including VAT. "
            "The energy price is charged per GJ separately."
        ),
    },
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from a config entry."""
    coordinator: ASFDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    sensors = coordinator.data.get("sensors", {})
    counts = Counter(
        (data["estate_id"], data["sensor_key"]) for data in sensors.values()
    )

    entities: list[SensorEntity] = [
        ASFConsumptionSensor(coordinator, key, counts) for key in sensors
    ]

    by_estate: dict[Any, dict[str, dict[str, Any]]] = {}
    for data in sensors.values():
        by_estate.setdefault(data["estate_id"], {})[data["sensor_key"]] = data

    quarterly = hass.data[DOMAIN][entry.entry_id].get("quarterly")
    if quarterly is not None and sensors:
        entities.append(
            ASFQuarterlyCostSensor(quarterly, entry, next(iter(sensors.values())))
        )

    for estate_sensors in by_estate.values():
        base = next(iter(estate_sensors.values()))
        if SENSOR_WATER in estate_sensors:
            entities.append(ASFPriceSensor(coordinator, entry, base, WATER_PRICE))
        if SENSOR_HEAT in estate_sensors:
            entities.append(ASFPriceSensor(coordinator, entry, base, HEAT_PRICE))
        if SENSOR_HOT_WATER in estate_sensors:
            entities.append(ASFPriceSensor(coordinator, entry, base, HEAT_WATER_PRICE))

    async_add_entities(entities)


class ASFConsumptionSensor(
    CoordinatorEntity[ASFDataUpdateCoordinator],
    SensorEntity,
):
    """Cumulative consumption sensor for one meter."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 3

    def __init__(
        self,
        coordinator: ASFDataUpdateCoordinator,
        key: str,
        counts: Counter[tuple[Any, str]],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._key = key
        data = coordinator.data["sensors"][key]
        sensor_key = data["sensor_key"]

        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_translation_key = sensor_key
        if counts[(data["estate_id"], sensor_key)] > 1:
            self._attr_name = (
                f"{SENSOR_LABELS.get(sensor_key, sensor_key)} {data.get('meter_id')}"
            )

        self._attr_device_class = data.get("device_class")
        self._attr_native_unit_of_measurement = data.get("unit")
        self._attr_icon = data.get("icon")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{data['customer_number']}_{data['estate_id']}")},
            name=data["estate_name"],
            manufacturer="Administrationsservice Fyn",
            model="Utility meters",
            configuration_url=SSO_LOGIN_URL,
        )

    @property
    def _data(self) -> dict[str, Any] | None:
        """Return the current data for this sensor."""
        return self.coordinator.data.get("sensors", {}).get(self._key)

    @property
    def available(self) -> bool:
        """Return True if the sensor still exists in the coordinator data."""
        return super().available and self._data is not None

    @property
    def native_value(self) -> float | None:
        """Return the cumulative meter value."""
        data = self._data
        return data.get("value") if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return meter and product details."""
        data = self._data
        if not data:
            return {}

        attributes = {
            "customer_number": data.get("customer_number"),
            "estate_id": data.get("estate_id"),
            "estate_name": data.get("estate_name"),
            "installation_id": data.get("installation_id"),
            "meter_id": data.get("meter_id"),
            "meter_number": data.get("meter_number"),
            "customer_meter_number": data.get("customer_meter_number"),
            "product_name": data.get("product_name"),
            "product_type": data.get("product_type"),
            "tariff": data.get("tariff"),
            "subscription_state": data.get("subscription_state"),
            "last_reading": data.get("last_reading"),
            "meter_reading": data.get("meter_reading"),
            "lifetime_consumption": data.get("lifetime_consumption"),
            "month_to_date": data.get("month_to_date"),
            "year_to_date": data.get("year_to_date"),
            "value_source": data.get("value_source"),
            "api_unit": data.get("api_unit"),
            "statistic_id": statistic_id(data),
        }
        if data.get("added_months"):
            attributes["added_months"] = data["added_months"]
        return attributes


class ASFPriceSensor(
    CoordinatorEntity[ASFDataUpdateCoordinator],
    SensorEntity,
):
    """Variable tariff for one utility product."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: ASFDataUpdateCoordinator,
        entry: ConfigEntry,
        data: dict[str, Any],
        spec: PriceSpec,
    ) -> None:
        """Initialize the tariff sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._spec = spec
        self._attr_unique_id = (
            f"{DOMAIN}_{entry.entry_id}_{data['estate_id']}_{spec.key}"
        )
        self._attr_translation_key = spec.key
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_icon = spec.icon
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{data['customer_number']}_{data['estate_id']}")},
            name=data["estate_name"],
            manufacturer="Administrationsservice Fyn",
            model="Utility meters",
            configuration_url=SSO_LOGIN_URL,
        )

    @property
    def native_value(self) -> float:
        """Return the configured tariff."""
        value = self._entry.options.get(self._spec.option, self._spec.default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return self._spec.default

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return tariff validity and fixed-fee details."""
        attributes = {
            "price_valid_from": PRICE_VALID_FROM,
            "includes_vat": True,
            "source": self._spec.source,
        }
        attributes.update(self._spec.attributes)
        return attributes


class ASFQuarterlyCostSensor(
    CoordinatorEntity[QuarterlyCostCoordinator],
    SensorEntity,
):
    """Projected variable cost for the current quarter."""

    _attr_has_entity_name = True
    _attr_translation_key = "quarterly_cost"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "DKK"
    _attr_icon = "mdi:cash-multiple"
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: QuarterlyCostCoordinator,
        entry: ConfigEntry,
        data: dict[str, Any],
    ) -> None:
        """Initialize the quarterly cost sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_quarterly_cost"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{data['customer_number']}_{data['estate_id']}")},
            name=data["estate_name"],
            manufacturer="Administrationsservice Fyn",
            model="Utility meters",
            configuration_url=SSO_LOGIN_URL,
        )

    @property
    def native_value(self) -> float | None:
        """Return the projected cost for the current quarter."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("current_quarter_projected")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the quarterly series and tariff assumptions."""
        return dict(self.coordinator.data or {})

    @property
    def available(self) -> bool:
        """Return True once the quarterly data has been loaded."""
        return super().available and bool(self.coordinator.data)
