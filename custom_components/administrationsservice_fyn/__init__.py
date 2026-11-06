"""Administrationsservice Fyn integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import ASFDataUpdateCoordinator
from .quarterly import QuarterlyCostCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Administrationsservice Fyn from a config entry."""
    coordinator = ASFDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    quarterly = QuarterlyCostCoordinator(
        hass,
        coordinator.api,
        coordinator.data.get("sensors", {}),
        dict(entry.options),
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "quarterly": quarterly,
        "options": dict(entry.options),
    }
    hass.async_create_background_task(
        quarterly.async_refresh(), f"{DOMAIN}_quarterly_costs"
    )
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    stored = hass.data[DOMAIN].get(entry.entry_id, {})
    quarterly = stored.get("quarterly")
    if quarterly is not None:
        await quarterly.async_shutdown()
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
