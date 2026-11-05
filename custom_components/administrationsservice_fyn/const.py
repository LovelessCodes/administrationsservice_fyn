"""Constants for the Administrationsservice Fyn integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "administrationsservice_fyn"
NAME: Final = "Administrationsservice Fyn"
VERSION: Final = "1.1.1"

# API endpoints
SELFSERVICE_API_BASE: Final = "https://efselfserviceapi.azurewebsites.net/api"
PROFILE_API_BASE: Final = "https://efprofileservice.azurewebsites.net/api"
SSO_TOKEN_URL: Final = "https://accounts.forsyningslogin.dk/connect/token"
SSO_CLIENT_ID: Final = "af-spa"
SSO_CLIENT_SECRET: Final = "208c7724d296407aa22f31fa200c6459"
SSO_SCOPE: Final = "openid profile customer offline_access"
SSO_ORIGIN: Final = "https://www.asfyn.dk"
SSO_LOGIN_URL: Final = "https://www.asfyn.dk/"
SSO_STORAGE_KEY: Final = "oidc.user:https://accounts.forsyningslogin.dk:af-spa"

# Config entry keys
CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_ACCESS_TOKEN: Final = "access_token"
CONF_TOKEN_EXPIRES: Final = "token_expires"
CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_SCAN_INTERVAL: Final = 60  # minutes
MIN_SCAN_INTERVAL: Final = 15
MAX_SCAN_INTERVAL: Final = 24 * 60
TOKEN_REFRESH_MARGIN: Final = 300  # seconds before expiry to refresh

UPDATE_INTERVAL: Final = timedelta(minutes=DEFAULT_SCAN_INTERVAL)
REQUEST_TIMEOUT: Final = 30

# Product / meter types
PRODUCT_EL: Final = "el"
PRODUCT_HEAT: Final = "heat"
PRODUCT_WATER: Final = "water"
PRODUCT_GAS: Final = "gas"

# Sensor keys
SENSOR_ELECTRICITY: Final = "electricity"
SENSOR_HEAT: Final = "heat"
SENSOR_HOT_WATER: Final = "hot_water"
SENSOR_WATER: Final = "water"
SENSOR_GAS: Final = "gas"

# API units (the API uses lowercase and no superscript)
API_UNIT_KWH: Final = "kWh"
API_UNIT_GJ: Final = "gj"
API_UNIT_M3: Final = "m3"

# Display units
UNIT_KWH: Final = "kWh"
UNIT_GJ: Final = "GJ"
UNIT_M3: Final = "m³"

# Attributes redacted from diagnostics
TO_REDACT: Final = {
    CONF_REFRESH_TOKEN,
    CONF_ACCESS_TOKEN,
    CONF_TOKEN_EXPIRES,
    "cpr",
    "email",
    "mobile",
    "phone",
}
