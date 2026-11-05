"""API client for Administrationsservice Fyn."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote, unquote

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import (
    PROFILE_API_BASE,
    REQUEST_TIMEOUT,
    SELFSERVICE_API_BASE,
    SSO_CLIENT_ID,
    SSO_CLIENT_SECRET,
    SSO_ORIGIN,
    SSO_SCOPE,
    SSO_TOKEN_URL,
    TOKEN_REFRESH_MARGIN,
)

_LOGGER = logging.getLogger(__name__)


class ASFApiError(Exception):
    """Base exception for API errors."""


class ASFAuthError(ASFApiError):
    """Raised when authentication fails."""


class ASFConnectionError(ASFApiError):
    """Raised when the API cannot be reached."""


def clean_refresh_token(value: str) -> str:
    """Normalise a refresh token copied from the browser."""
    token = value.strip()
    if ";" in token:
        token = token.split(";", 1)[0].strip()
    if token.lower().startswith("cookie:"):
        token = token[7:].strip()
    for prefix in ("sso_refresh_token=", "refresh_token="):
        if token.lower().startswith(prefix):
            token = token[len(prefix) :].strip()
            break
    token = token.strip().strip('"').strip("'").strip()
    return unquote(token)


def as_float(value: Any) -> float | None:
    """Convert a value from the API to a float."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", ".").strip())
    except ValueError:
        return None


def reading_value(
    product: Mapping[str, Any], key: str
) -> tuple[float | None, str | None]:
    """Return the value and reading date from a latestReadings object."""
    reading = product.get(key)
    if not isinstance(reading, Mapping):
        return None, None
    value = as_float(reading.get("value"))
    date = reading.get("latestReading") or reading.get("readingDate")
    return value, str(date) if date else None


class ASFApiClient:
    """Client for the Administrationsservice Fyn self-service API."""

    def __init__(
        self,
        session: ClientSession,
        refresh_token: str,
        access_token: str | None = None,
        expires_at: float | None = None,
        token_callback: Callable[[str, str, float], None] | None = None,
    ) -> None:
        """Initialize the API client."""
        self._session = session
        self._refresh_token = clean_refresh_token(refresh_token)
        self._access_token = access_token
        self._expires_at = float(expires_at or 0)
        self._token_callback = token_callback
        self._token_lock = asyncio.Lock()
        self._timeout = ClientTimeout(total=REQUEST_TIMEOUT)

    @property
    def access_token(self) -> str | None:
        """Return the current access token."""
        return self._access_token

    @property
    def refresh_token(self) -> str:
        """Return the current refresh token."""
        return self._refresh_token

    @property
    def expires_at(self) -> float:
        """Return the current token expiry timestamp."""
        return self._expires_at

    async def async_ensure_token(self, force: bool = False) -> None:
        """Ensure a valid access token is available."""
        if not force and self._access_token and time.time() < self._expires_at:
            return
        await self.async_refresh_access_token(force=force)

    async def async_refresh_access_token(self, force: bool = False) -> None:
        """Exchange the refresh token for a new access token."""
        async with self._token_lock:
            # Another task may have refreshed while we waited for the lock.
            if not force and self._access_token and time.time() < self._expires_at - 5:
                return

            if not self._refresh_token:
                raise ASFAuthError("No refresh token available")

            payload = {
                "client_id": SSO_CLIENT_ID,
                "client_secret": SSO_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
                "scope": SSO_SCOPE,
            }
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "Origin": SSO_ORIGIN,
                "Referer": f"{SSO_ORIGIN}/",
            }

            try:
                async with self._session.post(
                    SSO_TOKEN_URL,
                    data=payload,
                    headers=headers,
                    timeout=self._timeout,
                ) as response:
                    status = response.status
                    text = await response.text()
            except (ClientError, TimeoutError, OSError) as err:
                raise ASFConnectionError(f"Token refresh failed: {err}") from err

            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = {}

            if status != 200:
                error = data.get("error") or text[:200]
                if status in (400, 401, 403):
                    raise ASFAuthError(f"Token refresh rejected: {error}")
                raise ASFApiError(f"Token refresh failed with HTTP {status}: {error}")

            access_token = data.get("access_token")
            if not access_token:
                raise ASFAuthError("Token response did not contain an access token")

            expires_in = int(data.get("expires_in") or 3600)
            self._access_token = access_token
            self._refresh_token = clean_refresh_token(
                data.get("refresh_token") or self._refresh_token
            )
            self._expires_at = time.time() + max(30, expires_in - TOKEN_REFRESH_MARGIN)

            if self._token_callback is not None:
                self._token_callback(
                    self._refresh_token, self._access_token, self._expires_at
                )

            _LOGGER.debug("Access token refreshed, valid for %s seconds", expires_in)

    async def _async_request(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        """Perform a GET request, refreshing the token once on 401."""
        for attempt in range(2):
            await self.async_ensure_token(force=attempt > 0)
            headers = {
                "Authorization": f"Bearer {self._access_token}",
                "Accept": "application/json",
            }

            try:
                async with self._session.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=self._timeout,
                ) as response:
                    status = response.status
                    text = await response.text()
            except (ClientError, TimeoutError, OSError) as err:
                raise ASFConnectionError(f"Request to {url} failed: {err}") from err

            if status == 401 and attempt == 0:
                _LOGGER.debug("API returned 401, refreshing access token")
                continue

            if status == 404:
                return None

            if status == 401:
                raise ASFAuthError("API rejected the access token")

            if status >= 400:
                raise ASFApiError(f"GET {url} failed with HTTP {status}: {text[:200]}")

            if not text:
                return None

            try:
                return json.loads(text)
            except json.JSONDecodeError as err:
                raise ASFApiError(f"Invalid JSON from {url}") from err

        raise ASFAuthError("API rejected the access token")

    async def async_get_customers(self) -> list[dict[str, Any]]:
        """Return all customers on the account."""
        data = await self._async_request(f"{PROFILE_API_BASE}/customers/")
        return data if isinstance(data, list) else []

    async def async_get_estates(self, customer: str) -> list[dict[str, Any]]:
        """Return all estates for a customer."""
        data = await self._async_request(
            f"{SELFSERVICE_API_BASE}/customers/{quote(customer, safe='')}/estates"
        )
        return data if isinstance(data, list) else []

    async def async_get_products(
        self, customer: str, estate: str | int
    ) -> list[dict[str, Any]]:
        """Return all products for an estate."""
        data = await self._async_request(
            f"{SELFSERVICE_API_BASE}/customers/{quote(customer, safe='')}"
            f"/estates/{quote(str(estate), safe='')}/products",
            params={"isLarge": "false"},
        )
        return data if isinstance(data, list) else []

    async def async_get_product(
        self,
        customer: str,
        installation: str | int,
        category: str | None,
        meter: str | int,
        tariff: str | None,
    ) -> dict[str, Any] | None:
        """Return the detailed product, including latest meter readings."""
        params: dict[str, Any] = {"meterId": meter}
        if category:
            params["category"] = category
        if tariff:
            params["tarifArt"] = tariff
        data = await self._async_request(
            f"{SELFSERVICE_API_BASE}/customers/{quote(customer, safe='')}"
            f"/installations/{quote(str(installation), safe='')}/products",
            params=params,
        )
        return data if isinstance(data, dict) else None

    async def async_get_consumption(
        self,
        customer: str,
        estate: str | int,
        meter: str | int,
        installation: str | int,
        unit: str,
        period: str,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Return consumption data for a meter and period."""
        url = (
            f"{SELFSERVICE_API_BASE}/consumptions/"
            f"{quote(customer, safe='')}/{quote(str(estate), safe='')}/"
            f"{quote(str(meter), safe='')}/{quote(str(installation), safe='')}/"
            f"{quote(unit, safe='')}/{quote(period, safe='')}"
        )
        data = await self._async_request(url, params=params)
        return data if isinstance(data, dict) else None

    async def async_get_consumption_summary(
        self,
        customer: str,
        estate: str | int,
        meter: str | int,
        installation: str | int,
        unit: str,
        period: str,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Return the summary block for a consumption period."""
        data = await self.async_get_consumption(
            customer, estate, meter, installation, unit, period, params
        )
        if not data:
            return None
        summary = data.get("summary")
        return summary if isinstance(summary, dict) else None

    async def async_get_total(
        self,
        customer: str,
        estate: str | int,
        meter: str | int,
        installation: str | int,
        unit: str,
        period: str = "total",
    ) -> float | None:
        """Return the lifetime total for a meter."""
        summary = await self.async_get_consumption_summary(
            customer, estate, meter, installation, unit, period
        )
        return as_float(summary.get("total")) if summary else None

    async def async_get_month(
        self,
        customer: str,
        estate: str | int,
        meter: str | int,
        installation: str | int,
        unit: str,
        year: int,
        month: int,
    ) -> float | None:
        """Return the month-to-date total for a meter."""
        summary = await self.async_get_consumption_summary(
            customer,
            estate,
            meter,
            installation,
            unit,
            "month",
            {"year": year, "month": month},
        )
        return as_float(summary.get("total")) if summary else None

    async def async_get_year(
        self,
        customer: str,
        estate: str | int,
        meter: str | int,
        installation: str | int,
        unit: str,
        year: int,
    ) -> float | None:
        """Return the year-to-date total for a meter."""
        summary = await self.async_get_consumption_summary(
            customer,
            estate,
            meter,
            installation,
            unit,
            "year",
            {"year": year},
        )
        return as_float(summary.get("total")) if summary else None
