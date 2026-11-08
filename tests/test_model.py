"""Unit tests for the HA-independent API logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.opensprinkler_sensors.model import (
    SENSOR_TYPE_NONE,
    SENSOR_TYPE_RAIN,
    SENSOR_TYPE_SOIL,
    LastOnTracker,
    OpenSprinklerAuthError,
    OpenSprinklerNotSupportedError,
    OpenSprinklerResponseError,
    firmware_version,
    is_on,
    local_epoch_to_utc,
    normalize_base_url,
    parse_analog_sensors,
    parse_response,
    password_hash,
    sensor_input_types,
    station_disabled_flags,
    timezone_offset,
)

NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)
OFFSET = 7200
LOCAL_NOW = int(NOW.timestamp()) + OFFSET


def test_password_hash() -> None:
    assert password_hash("opendoor") == "a6d82bced638de3def1e9bbb4983225c"
    assert password_hash("A6D82BCED638DE3DEF1E9BBB4983225C") == "a6d82bced638de3def1e9bbb4983225c"


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("192.168.1.50", "http://192.168.1.50"),
        (" os.local:8080/ ", "http://os.local:8080"),
        ("https://os.example.com/", "https://os.example.com"),
        ("HTTP://10.0.0.2", "HTTP://10.0.0.2"),
    ],
)
def test_normalize_base_url(host: str, expected: str) -> None:
    assert normalize_base_url(host) == expected


def test_parse_response_success_and_errors() -> None:
    assert parse_response(200, '{"devt": 1}') == {"devt": 1}
    assert parse_response(200, "[[1,0,60,100]]") == [[1, 0, 60, 100]]
    assert parse_response(200, '{"result": 1}') == {"result": 1}
    with pytest.raises(OpenSprinklerAuthError):
        parse_response(200, '{"result": 2}')
    with pytest.raises(OpenSprinklerNotSupportedError):
        parse_response(200, '{"result": 32}')
    with pytest.raises(OpenSprinklerNotSupportedError):
        parse_response(404, "Not found")
    with pytest.raises(OpenSprinklerResponseError):
        parse_response(200, '{"result": 16}')
    with pytest.raises(OpenSprinklerResponseError):
        parse_response(200, "<html>")
    with pytest.raises(OpenSprinklerAuthError):
        parse_response(401, "")


def test_timezone_offset_prefers_tz_option() -> None:
    assert timezone_offset({"tz": 56}, {"devt": 0}, NOW) == 7200
    assert timezone_offset({"tz": 32}, {}, NOW) == -4 * 3600
    assert timezone_offset({"tz": 86}, {}, NOW) == int(9.5 * 3600)


def test_timezone_offset_falls_back_to_clock_comparison() -> None:
    # Controller clock 2h ahead, a few seconds of drift: rounds to 2h.
    assert timezone_offset({}, {"devt": LOCAL_NOW + 7}, NOW) == 7200
    assert timezone_offset({}, {}, NOW) == 0


def test_local_epoch_to_utc() -> None:
    assert local_epoch_to_utc(LOCAL_NOW, OFFSET) == NOW
    assert local_epoch_to_utc(0, OFFSET) is None
    assert local_epoch_to_utc(None, OFFSET) is None
    assert local_epoch_to_utc(True, OFFSET) is None


def test_firmware_version() -> None:
    assert firmware_version({"fwv": 221, "fwm": 4}) == "2.2.1(4)"
    assert firmware_version({"fwv": 219}) == "2.1.9"
    assert firmware_version({}) is None


def test_station_disabled_flags() -> None:
    names = {"stn_dis": [0b00000101, 0b10000000]}
    flags = station_disabled_flags(names, 16)
    assert [i for i, off in enumerate(flags) if off] == [0, 2, 15]
    assert station_disabled_flags({}, 3) == [False, False, False]
    # Fewer bytes than boards: missing boards count as enabled.
    assert station_disabled_flags({"stn_dis": [0]}, 10) == [False] * 10


def test_sensor_input_types() -> None:
    assert sensor_input_types({"sn1t": 1, "sn2t": 3}) == {"s1": SENSOR_TYPE_RAIN, "s2": SENSOR_TYPE_SOIL}
    assert sensor_input_types({"urs": 1}) == {"s1": SENSOR_TYPE_RAIN, "s2": SENSOR_TYPE_NONE}
    assert sensor_input_types({}) == {"s1": SENSOR_TYPE_NONE, "s2": SENSOR_TYPE_NONE}


def test_parse_analog_sensors() -> None:
    sensor_list = {"sensors": [{"nr": 1, "name": "Tomatoes"}, {"nr": 3, "name": "  "}]}
    values = {
        "datas": [
            {"nr": 1, "data": 31.5, "unit": "%", "unitid": 1, "last": LOCAL_NOW},
            {"nr": 3, "data": 0, "unit": "", "unitid": 0, "last": 0},
            {"nr": 0, "data": 1},
            "garbage",
        ]
    }
    sensors = parse_analog_sensors(sensor_list, values, OFFSET)
    assert set(sensors) == {1, 3}
    assert sensors[1].name == "Tomatoes"
    assert sensors[1].value == 31.5
    assert sensors[1].last_read == NOW
    # Never read: no value, and a blank name falls back to the number.
    assert sensors[3].value is None
    assert sensors[3].name == "Sensor 3"
    assert parse_analog_sensors(None, {}, OFFSET) == {}


def test_tracker_seeds_from_log() -> None:
    tracker = LastOnTracker()
    tracker.seed_from_log(
        [
            [1, 0, 900, LOCAL_NOW - 3600],
            [2, 0, 900, LOCAL_NOW - 7200],  # older run of the same zone
            [0, "s1", 1200, LOCAL_NOW - 600],
            [0, "rd", 86400, LOCAL_NOW],  # rain delay: ignored
            [0, "fl", 30, LOCAL_NOW],  # flow reading: ignored
            [99, 4, 60, LOCAL_NOW - 60, 12.5],  # manual run, with flow column
            "garbage",
            [1, 2],
        ],
        OFFSET,
    )
    assert tracker.get("station_0") == NOW - timedelta(hours=1)
    assert tracker.get("s1") == NOW - timedelta(minutes=10)
    assert tracker.get("station_4") == NOW - timedelta(minutes=1)
    assert tracker.get("station_1") is None
    assert tracker.get("s2") is None


def test_tracker_update_live_state() -> None:
    tracker = LastOnTracker()
    tracker.record("station_2", NOW - timedelta(days=1))
    tracker.update(
        now=NOW,
        station_status=[0, 1, 0],
        controller={"lrun": [2, 1, 600, LOCAL_NOW - 60], "sn1": 0, "sn2": 1},
        offset=OFFSET,
    )
    assert tracker.get("station_1") == NOW  # running now
    assert tracker.get("station_2") == NOW - timedelta(minutes=1)  # from lrun, newer than log
    assert tracker.get("s2") == NOW
    assert tracker.get("s1") is None


def test_tracker_never_goes_backwards() -> None:
    tracker = LastOnTracker()
    tracker.record("station_0", NOW)
    tracker.record("station_0", NOW - timedelta(hours=1))
    tracker.update(
        now=NOW - timedelta(hours=2),
        station_status=[1],
        controller={"lrun": [0, 0, 0, 0]},
        offset=OFFSET,
    )
    assert tracker.get("station_0") == NOW


def test_is_on() -> None:
    controller = {"sn1": 1, "sn2": 0}
    assert is_on("s1", [], controller)
    assert not is_on("s2", [], controller)
    assert is_on("station_1", [0, 1], controller)
    assert not is_on("station_5", [0, 1], controller)
