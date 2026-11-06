"""Sensor platform for Administrationsservice Fyn."""

from __future__ import annotations

from collections import Counter
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    SENSOR_ELECTRICITY,
    SENSOR_GAS,
    SENSOR_HEAT,
    SENSOR_HOT_WATER,
    SENSOR_WATER,
    SSO_LOGIN_URL,
)
from .coordinator import ASFDataUpdateCoordinator
from .statistics import statistic_id

SENSOR_LABELS = {
    SENSOR_ELECTRICITY: "Electricity",
    SENSOR_HEAT: "Heating",
    SENSOR_HOT_WATER: "Hot water",
    SENSOR_WATER: "Water",
    SENSOR_GAS: "Gas",
}


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

    async_add_entities(
        ASFConsumptionSensor(coordinator, key, counts) for key in sensors
    )


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
