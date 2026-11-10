"""Shared fixtures: a fake controller served through aioclient_mock."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from homeassistant.util import dt as dt_util

BASE_URL = "http://192.168.1.50"
MAC = "AA:BB:CC:DD:EE:FF"
PW_HASH = "a6d82bced638de3def1e9bbb4983225c"  # md5("opendoor")
TZ_OFFSET = 2 * 3600  # controller set to GMT+2 (tz option 56)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


def local(seconds_ago: float) -> int:
    """A controller-local epoch timestamp ``seconds_ago`` before now."""
    return int(dt_util.utcnow().timestamp() - seconds_ago + TZ_OFFSET)


def controller_payloads() -> dict[str, Any]:
    """Responses for a controller with analog sensors, one zone running."""
    return {
        "jc": {
            "devt": local(0),
            "nbrd": 1,
            "en": 1,
            "sn1": 0,
            "sn2": 0,
            "rd": 0,
            "lrun": [2, 1, 600, local(60)],
            # [program, remaining s, start time] per zone; Back lawn started 100 s ago.
            "ps": [[0, 0, 0], [1, 500, local(100)]] + [[0, 0, 0]] * 6,
            "mac": MAC,
        },
        "jo": {"fwv": 240, "fwm": 199, "tz": 56, "sn1t": 1, "sn2t": 3},
        "jn": {
            "snames": ["Front lawn", "Back lawn", "Beds", "S04", "S05", "S06", "S07", "S08"],
            # S04 disabled; S05-S08 are left enabled to check they still get entities.
            "stn_dis": [0b00001000],
        },
        "js": {"sn": [0, 1, 0, 0, 0, 0, 0, 0], "nstations": 8},
        # Default fixture is OpenSprinklerShop firmware: no official /jsn API.
        "jsn": {"result": 32},
        "sl": {
            "count": 2,
            "detected": 0,
            "sensors": [
                {"nr": 1, "type": 2, "name": "Tomatoes"},
                {"nr": 2, "type": 3, "name": "Bed temperature"},
            ],
        },
        "sg": {
            "datas": [
                {"nr": 1, "nativedata": 1234, "data": 31.5, "unit": "%", "unitid": 1, "last": local(20)},
                {"nr": 2, "nativedata": 456, "data": 14.2, "unit": "°C", "unitid": 2, "last": local(20)},
            ]
        },
        "jl": [
            [1, 0, 900, local(3600)],
            [0, "s1", 1200, local(7200)],
            [1, 2, 600, local(5000)],
            [0, "rd", 86400, local(9000)],
        ],
    }


@pytest.fixture
def mock_controller(aioclient_mock) -> Callable[..., dict[str, Any]]:
    """Register the fake controller; pass overrides as ``endpoint=payload``."""

    def _register(**overrides: Any) -> dict[str, Any]:
        payloads = controller_payloads() | overrides
        aioclient_mock.clear_requests()
        for endpoint, payload in payloads.items():
            if isinstance(payload, Exception):
                aioclient_mock.get(f"{BASE_URL}/{endpoint}", exc=payload)
            else:
                aioclient_mock.get(f"{BASE_URL}/{endpoint}", json=payload)
        return payloads

    return _register


def official_sensor_payloads() -> dict[str, Any]:
    """Overrides for official firmware 2.2.1(5)+: /jsn instead of /sl + /sg.

    Shaped like a real OpenSprinkler v4.0 response (VH400 soil sensors, unit 40
    = %VMC), plus a temperature sensor and one reporting a read error.
    """

    def sensor(uuid: int, name: str, unit: int, value: float, status: int = 1) -> dict[str, Any]:
        return {
            "uuid": uuid,
            "name": name,
            "unit": unit,
            "flag": 5,
            "status": status,
            "interval": 1,
            "min": 0,
            "max": 100,
            "value": value,
            "type": 1,
            "extra": {"pin": uuid, "scale": 1, "offset": 0, "subtype": 15},
        }

    return {
        "jsn": {
            "sn": [
                sensor(1, "Center tree soil", 40, 43.1132),
                sensor(2, "Back yard fence soil", 40, 29.5963),
                sensor(3, "Shed temperature", 17, 18.25),
                sensor(4, "Broken probe", 40, 0.0, status=3),  # valid + error bits
            ],
            "count": 4,
        },
        "sl": {"result": 32},
        "sg": {"result": 32},
    }
