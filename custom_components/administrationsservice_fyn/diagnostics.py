"""Diagnostics support for Administrationsservice Fyn."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from .const import DOMAIN, TO_REDACT


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    coordinator_data = coordinator.data or {}
    sensors = []
    for data in coordinator_data.get("sensors", {}).values():
        sensors.append(
            {
                "sensor_key": data.get("sensor_key"),
                "value": data.get("value"),
                "unit": data.get("unit"),
                "api_unit": data.get("api_unit"),
                "value_source": data.get("value_source"),
                "last_reading": data.get("last_reading"),
                "meter_reading": data.get("meter_reading"),
                "lifetime_consumption": data.get("lifetime_consumption"),
                "month_to_date": data.get("month_to_date"),
                "year_to_date": data.get("year_to_date"),
                "added_months": data.get("added_months"),
                "customer_number": data.get("customer_number"),
                "estate_id": data.get("estate_id"),
                "estate_name": data.get("estate_name"),
                "installation_id": data.get("installation_id"),
                "meter_id": data.get("meter_id"),
                "meter_number": data.get("meter_number"),
                "product_name": data.get("product_name"),
                "product_type": data.get("product_type"),
                "tariff": data.get("tariff"),
                "subscription_state": data.get("subscription_state"),
            }
        )

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": (
                repr(coordinator.last_exception) if coordinator.last_exception else None
            ),
            "customer_count": coordinator_data.get("customer_count"),
            "estate_count": coordinator_data.get("estate_count"),
            "data_date": coordinator_data.get("data_date"),
            "profiles": async_redact_data(
                coordinator_data.get("profiles", []), TO_REDACT
            ),
            "sensors": [async_redact_data(item, TO_REDACT) for item in sensors],
        },
    }
