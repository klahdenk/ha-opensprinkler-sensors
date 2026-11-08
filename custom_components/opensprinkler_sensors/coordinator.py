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
    AnalogSensor,
    LastOnTracker,
    OpenSprinklerAuthError,
    OpenSprinklerError,
    OpenSprinklerNotSupportedError,
    parse_analog_sensors,
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
    analog: dict[int, AnalogSensor] = field(default_factory=dict)


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
        self.analog_supported = False
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
            values = await self.client.sensor_values() if self.analog_supported else None
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
            analog=(parse_analog_sensors(self._sensor_list, values, offset) if values is not None else {}),
        )

    async def _async_slow_refresh(self, now: datetime, controller: dict[str, Any]) -> None:
        """Refresh rarely-changing data and top up "last on" from the log."""
        self._options = await self.client.options()
        self._names = await self.client.station_names()

        try:
            self._sensor_list = await self.client.sensor_list()
        except OpenSprinklerNotSupportedError:
            if self.analog_supported or self._next_slow_refresh is None:
                LOGGER.debug("Controller has no Analog Sensor API; skipping /sl and /sg")
            self._sensor_list = None
            self.analog_supported = False
        else:
            self.analog_supported = True

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


type OpenSprinklerConfigEntry = ConfigEntry[OpenSprinklerCoordinator]
