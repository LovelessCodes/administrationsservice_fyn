"""Button platform for Administrationsservice Fyn."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME, SSO_LOGIN_URL
from .coordinator import ASFDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the refresh button."""
    coordinator: ASFDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    async_add_entities([ASFRefreshButton(coordinator, entry)])


class ASFRefreshButton(
    CoordinatorEntity[ASFDataUpdateCoordinator],
    ButtonEntity,
):
    """Button that triggers an immediate refresh."""

    _attr_has_entity_name = True
    _attr_translation_key = "refresh"

    def __init__(
        self,
        coordinator: ASFDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_refresh"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=NAME,
            manufacturer="Administrationsservice Fyn",
            model="Self-service portal",
            configuration_url=SSO_LOGIN_URL,
        )

    async def async_press(self) -> None:
        """Refresh all meters."""
        await self.coordinator.async_request_refresh()
