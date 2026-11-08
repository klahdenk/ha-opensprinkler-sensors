"""Diagnostics for OpenSprinkler Sensors."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_PASSWORD_HASH
from .coordinator import OpenSprinklerConfigEntry

TO_REDACT = {
    CONF_PASSWORD_HASH,
    "mac",
    "loc",
    "lat",
    "lon",
    "ifkey",
    "wto",
    "mqtt",
    "email",
    "otc",
    "eip",
    "external_ip",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: OpenSprinklerConfigEntry
) -> dict[str, Any]:
    """Return redacted controller state for bug reports."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return async_redact_data(
        {
            "entry": dict(entry.data),
            "analog_supported": coordinator.analog_supported,
            "controller": data.controller,
            "options": data.options,
            "station_names": data.station_names,
            "station_disabled": data.station_disabled,
            "station_status": data.station_status,
            "sensor_types": data.sensor_types,
            "analog": {nr: asdict(sensor) for nr, sensor in data.analog.items()},
        },
        TO_REDACT,
    )
