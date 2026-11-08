"""Async client for the OpenSprinkler HTTP API."""

from __future__ import annotations

from typing import Any

import aiohttp

from .const import REQUEST_TIMEOUT
from .model import (
    OpenSprinklerConnectionError,
    OpenSprinklerResponseError,
    parse_response,
)


class OpenSprinklerClient:
    """Thin wrapper over the controller's JSON endpoints.

    The controller is a small single-threaded device, so callers should issue
    requests one at a time rather than concurrently.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        password_hash: str,
        timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        self._session = session
        self.base_url = base_url
        self._password_hash = password_hash
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def _get(self, path: str, **params: Any) -> Any:
        query = {"pw": self._password_hash, **{k: str(v) for k, v in params.items()}}
        try:
            async with self._session.get(
                f"{self.base_url}/{path}", params=query, timeout=self._timeout
            ) as resp:
                text = await resp.text()
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise OpenSprinklerConnectionError(str(err) or type(err).__name__) from err
        return parse_response(status, text)

    async def _get_dict(self, path: str, **params: Any) -> dict[str, Any]:
        data = await self._get(path, **params)
        if not isinstance(data, dict):
            raise OpenSprinklerResponseError(f"/{path} did not return an object")
        return data

    async def controller(self) -> dict[str, Any]:
        """``/jc``: clock, sensor input status, last run."""
        data = await self._get_dict("jc")
        if "devt" not in data:
            raise OpenSprinklerResponseError("/jc response is missing devt; not an OpenSprinkler?")
        return data

    async def options(self) -> dict[str, Any]:
        """``/jo``: firmware version, time zone, sensor input types."""
        return await self._get_dict("jo")

    async def station_status(self) -> dict[str, Any]:
        """``/js``: on/off per zone."""
        return await self._get_dict("js")

    async def station_names(self) -> dict[str, Any]:
        """``/jn``: zone names and attributes."""
        return await self._get_dict("jn")

    async def sensor_list(self) -> dict[str, Any]:
        """``/sl``: analog sensor configuration (Analog Sensor API only)."""
        return await self._get_dict("sl")

    async def sensor_values(self) -> dict[str, Any]:
        """``/sg``: latest analog sensor readings (Analog Sensor API only)."""
        return await self._get_dict("sg")

    async def log(self, days: int) -> list[Any]:
        """``/jl``: run and sensor-event log for the last ``days`` days."""
        data = await self._get("jl", hist=days)
        if not isinstance(data, list):
            raise OpenSprinklerResponseError("/jl did not return a list")
        return data
