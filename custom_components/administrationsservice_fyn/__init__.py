"""Administrationsservice Fyn integration."""

from __future__ import annotations

import logging

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

    async def async_debug_day(call: ServiceCall) -> dict:
        """Return the raw day response for debugging."""
        coordinator: ASFDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
            "coordinator"
        ]
        sensor_key = call.data.get("sensor_key", "electricity")
        data = next(
            item
            for item in coordinator.data["sensors"].values()
            if item["sensor_key"] == sensor_key
        )
        params = call.data.get("params")
        try:
            result = await coordinator.api.async_get_consumption(
                str(data["customer_number"]),
                data["estate_id"],
                data["meter_id"],
                data["installation_id"],
                data["api_unit"],
                "day",
                dict(params) if params else None,
            )
        except Exception as err:  # noqa: BLE001 - debug helper
            return {
                "ok": False,
                "type": type(err).__name__,
                "error": str(err),
                "params": params,
            }
        return {"ok": True, "params": params, "result": result}

    if not hass.services.has_service(DOMAIN, "debug_day"):
        hass.services.async_register(
            DOMAIN,
            "debug_day",
            async_debug_day,
            schema=vol.Schema(
                {
                    vol.Optional("sensor_key", default="electricity"): str,
                    vol.Optional("params"): dict,
                }
            ),
            supports_response=SupportsResponse.ONLY,
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload only when the options changed, not when tokens rotate."""
    stored = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if stored is None:
        return
    if dict(entry.options) != stored["options"]:
        await hass.config_entries.async_reload(entry.entry_id)
