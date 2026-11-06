"""Config flow for Administrationsservice Fyn."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    ASFApiClient,
    ASFApiError,
    ASFAuthError,
    clean_refresh_token,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_ELECTRICITY_PRICE,
    CONF_HEAT_PRICE,
    CONF_HEAT_WATER_PRICE,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN_EXPIRES,
    CONF_WATER_PRICE,
    DEFAULT_ELECTRICITY_PRICE,
    DEFAULT_HEAT_PRICE,
    DEFAULT_HEAT_WATER_PRICE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WATER_PRICE,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    NAME,
    SSO_LOGIN_URL,
    SSO_STORAGE_KEY,
)

_LOGGER = logging.getLogger(__name__)


def _token_subject(token: str | None) -> str | None:
    """Read the subject from a JWT without validating it."""
    if not token or token.count(".") < 2:
        return None
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:  # noqa: BLE001 - best-effort decoding only
        return None
    if not isinstance(data, dict):
        return None
    return data.get("sub") or data.get("personId") or data.get("customer_id")


class ASFConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Administrationsservice Fyn."""

    VERSION = 1

    async def _async_validate(
        self, refresh_token: str
    ) -> tuple[ASFApiClient, list[dict[str, Any]]]:
        """Validate a refresh token and return the client and customers."""
        client = ASFApiClient(
            async_get_clientsession(self.hass),
            clean_refresh_token(refresh_token),
        )
        await client.async_refresh_access_token()
        customers = await client.async_get_customers()
        return client, customers

    def _unique_id(self, client: ASFApiClient, customers: list[dict[str, Any]]) -> str:
        """Build a stable unique id for the account."""
        subject = _token_subject(client.access_token)
        if subject:
            return str(subject)
        customer_number = customers[0].get("customerNumber")
        return f"customer_{customer_number}"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                client, customers = await self._async_validate(
                    user_input[CONF_REFRESH_TOKEN]
                )
            except ASFAuthError:
                errors["base"] = "invalid_auth"
            except ASFApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during setup")
                errors["base"] = "unknown"
            else:
                if not customers:
                    errors["base"] = "no_customers"
                else:
                    await self.async_set_unique_id(self._unique_id(client, customers))
                    self._abort_if_unique_id_configured(
                        updates={
                            CONF_REFRESH_TOKEN: client.refresh_token,
                            CONF_ACCESS_TOKEN: client.access_token,
                            CONF_TOKEN_EXPIRES: client.expires_at,
                        },
                        reload_on_update=True,
                    )
                    return self.async_create_entry(
                        title=NAME,
                        data={
                            CONF_REFRESH_TOKEN: client.refresh_token,
                            CONF_ACCESS_TOKEN: client.access_token,
                            CONF_TOKEN_EXPIRES: client.expires_at,
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_REFRESH_TOKEN): str}),
            errors=errors,
            description_placeholders={
                "url": SSO_LOGIN_URL,
                "storage_key": SSO_STORAGE_KEY,
            },
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle a reauthentication request."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new refresh token."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                client, customers = await self._async_validate(
                    user_input[CONF_REFRESH_TOKEN]
                )
            except ASFAuthError:
                errors["base"] = "invalid_auth"
            except ASFApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during reauthentication")
                errors["base"] = "unknown"
            else:
                if not customers:
                    errors["base"] = "no_customers"
                else:
                    entry = self._get_reauth_entry()
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={
                            CONF_REFRESH_TOKEN: client.refresh_token,
                            CONF_ACCESS_TOKEN: client.access_token,
                            CONF_TOKEN_EXPIRES: client.expires_at,
                        },
                    )
                    return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_REFRESH_TOKEN): str}),
            errors=errors,
            description_placeholders={
                "url": SSO_LOGIN_URL,
                "storage_key": SSO_STORAGE_KEY,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return ASFOptionsFlow(config_entry)


class ASFOptionsFlow(OptionsFlow):
    """Handle options for Administrationsservice Fyn."""

    def __init__(self, config_entry: ConfigEntry | None = None) -> None:
        """Initialise the options flow for old and new Home Assistant versions."""
        if config_entry is not None:
            try:
                super().__init__(config_entry)
            except TypeError:
                super().__init__()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the update interval."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = int(
            self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        electricity_price = float(
            self.config_entry.options.get(
                CONF_ELECTRICITY_PRICE, DEFAULT_ELECTRICITY_PRICE
            )
        )
        water_price = float(
            self.config_entry.options.get(CONF_WATER_PRICE, DEFAULT_WATER_PRICE)
        )
        heat_price = float(
            self.config_entry.options.get(CONF_HEAT_PRICE, DEFAULT_HEAT_PRICE)
        )
        heat_water_price = float(
            self.config_entry.options.get(
                CONF_HEAT_WATER_PRICE, DEFAULT_HEAT_WATER_PRICE
            )
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    ),
                    vol.Optional(
                        CONF_ELECTRICITY_PRICE, default=electricity_price
                    ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                    vol.Optional(CONF_WATER_PRICE, default=water_price): vol.All(
                        vol.Coerce(float), vol.Range(min=0, max=10000)
                    ),
                    vol.Optional(CONF_HEAT_PRICE, default=heat_price): vol.All(
                        vol.Coerce(float), vol.Range(min=0, max=10000)
                    ),
                    vol.Optional(
                        CONF_HEAT_WATER_PRICE, default=heat_water_price
                    ): vol.All(vol.Coerce(float), vol.Range(min=0, max=10000)),
                }
            ),
        )
