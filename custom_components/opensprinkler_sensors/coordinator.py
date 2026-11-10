"""Polling coordinator for OpenSprinkler Sensors."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import OpenSprinklerClient
from .const import DOMAIN, LOG_SEED_DAYS, LOGGER, SCAN_INTERVAL, SLOW_REFRESH_INTERVAL
from .model import (
    SENSOR_API_OFFICIAL,
    SENSOR_API_SHOP,
    AnalogSensor,
    LastOnTracker,
    OpenSprinklerAuthError,
    OpenSprinklerError,
    OpenSprinklerNotSupportedError,
    parse_analog_sensors,
    parse_official_sensors,
    sensor_input_types,
    station_disabled_flags,
    timezone_offset,
)


@dataclass
class OpenSprinklerData:
    """One snapshot of controller state."""

    controller: dict[str, Any]
    options: dict[str, Any]
    station_names: list[str]
    station_disabled: list[bool]
    station_status: list[int]
    sensor_types: dict[str, int]
    analog: dict[str, AnalogSensor] = field(default_factory=dict)


class OpenSprinklerCoordinator(DataUpdateCoordinator[OpenSprinklerData]):
    """Polls the controller and keeps the "last on" tracker current."""

    config_entry: OpenSprinklerConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: OpenSprinklerConfigEntry,
        client: OpenSprinklerClient,
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.tracker = LastOnTracker()
        # Which analog sensor API the firmware has: SENSOR_API_OFFICIAL (/jsn),
        # SENSOR_API_SHOP (/sl + /sg), or None. Re-detected on each slow refresh.
        self.sensor_api: str | None = None
        self._options: dict[str, Any] = {}
        self._names: dict[str, Any] = {}
        self._sensor_list: dict[str, Any] | None = None
        self._next_slow_refresh: datetime | None = None
        self._log_seeded = False

    async def _async_update_data(self) -> OpenSprinklerData:
        now = dt_util.utcnow()
        try:
            controller = await self.client.controller()
            if self._next_slow_refresh is None or now >= self._next_slow_refresh:
                await self._async_slow_refresh(now, controller)
            status = await self.client.station_status()
            analog_raw = await self._async_fetch_analog()
        except OpenSprinklerAuthError as err:
            raise ConfigEntryAuthFailed("OpenSprinkler rejected the password") from err
        except OpenSprinklerError as err:
            raise UpdateFailed(f"Error communicating with OpenSprinkler: {err}") from err

        offset = timezone_offset(self._options, controller, now)
        names = [str(name) for name in self._names.get("snames") or []]
        station_status = [int(on) for on in status.get("sn") or []]

        self.tracker.update(
            now=now,
            station_status=station_status,
            controller=controller,
            offset=offset,
        )

        return OpenSprinklerData(
            controller=controller,
            options=self._options,
            station_names=names,
            station_disabled=station_disabled_flags(self._names, len(names)),
            station_status=station_status,
            sensor_types=sensor_input_types(self._options),
            analog=self._parse_analog(analog_raw, offset),
        )

    async def _async_slow_refresh(self, now: datetime, controller: dict[str, Any]) -> None:
        """Refresh rarely-changing data and top up "last on" from the log."""
        self._options = await self.client.options()
        self._names = await self.client.station_names()

        await self._async_detect_sensor_api()

        offset = timezone_offset(self._options, controller, now)
        days = 1 if self._log_seeded else LOG_SEED_DAYS
        try:
            self.tracker.seed_from_log(await self.client.log(days), offset)
        except OpenSprinklerAuthError:
            raise
        except OpenSprinklerError as err:
            # Logging can be disabled on the controller; live tracking still works.
            LOGGER.debug("Could not read controller log: %s", err)
        else:
            self._log_seeded = True

        self._next_slow_refresh = now + SLOW_REFRESH_INTERVAL

    async def _async_detect_sensor_api(self) -> None:
        """Prefer the official /jsn API, fall back to OpenSprinklerShop's /sl."""
        previous = self.sensor_api
        self._sensor_list = None
        try:
            await self.client.official_sensors()
        except OpenSprinklerNotSupportedError:
            try:
                self._sensor_list = await self.client.sensor_list()
            except OpenSprinklerNotSupportedError:
                self.sensor_api = None
            else:
                self.sensor_api = SENSOR_API_SHOP
        else:
            self.sensor_api = SENSOR_API_OFFICIAL
        if self.sensor_api != previous or self._next_slow_refresh is None:
            LOGGER.debug("Analog sensor API: %s", self.sensor_api or "none")

    async def _async_fetch_analog(self) -> dict[str, Any] | None:
        if self.sensor_api == SENSOR_API_OFFICIAL:
            return await self.client.official_sensors()
        if self.sensor_api == SENSOR_API_SHOP:
            return await self.client.sensor_values()
        return None

    def _parse_analog(self, raw: dict[str, Any] | None, offset: int) -> dict[str, AnalogSensor]:
        if raw is None:
            return {}
        if self.sensor_api == SENSOR_API_OFFICIAL:
            return parse_official_sensors(raw)
        return parse_analog_sensors(self._sensor_list, raw, offset)


type OpenSprinklerConfigEntry = ConfigEntry[OpenSprinklerCoordinator]
