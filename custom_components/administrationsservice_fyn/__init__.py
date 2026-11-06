"""Administrationsservice Fyn integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse

from .const import DOMAIN
from .coordinator import ASFDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Administrationsservice Fyn from a config entry."""
    coordinator = ASFDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "options": dict(entry.options),
    }
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_debug_service(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


def _async_register_debug_service(hass: HomeAssistant) -> None:
    """Register a temporary consumption probe service."""
    if hass.services.has_service(DOMAIN, "debug_consumption"):
        return

    async def _async_debug_consumption(call: ServiceCall) -> dict[str, Any]:
        entry_id = call.data.get("entry_id")
        if entry_id is None:
            entry_id = next(iter(hass.data[DOMAIN]))
        coordinator: ASFDataUpdateCoordinator = hass.data[DOMAIN][entry_id][
            "coordinator"
        ]
        sensor_key = call.data["sensor_key"]
        sensor = next(
            data
            for data in coordinator.data["sensors"].values()
            if data["sensor_key"] == sensor_key
        )
        params = {
            key: call.data[key]
            for key in ("year", "quarter", "month", "week", "day")
            if key in call.data
        }
        result = await coordinator.api.async_get_consumption(
            sensor["customer_number"],
            sensor["estate_id"],
            sensor["meter_id"],
            sensor["installation_id"],
            sensor["unit"],
            call.data.get("period", "quarter"),
            params or None,
        )
        return {"result": result}

    hass.services.async_register(
        DOMAIN,
        "debug_consumption",
        _async_debug_consumption,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Required("sensor_key"): vol.In(
                    ["electricity", "water", "heat", "hot_water", "gas"]
                ),
                vol.Optional("period", default="quarter"): str,
                vol.Optional("year"): int,
                vol.Optional("quarter"): int,
                vol.Optional("month"): int,
                vol.Optional("week"): int,
                vol.Optional("day"): int,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload only when the options changed, not when tokens rotate."""
    stored = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if stored is None:
        return
    if dict(entry.options) != stored["options"]:
        await hass.config_entries.async_reload(entry.entry_id)
