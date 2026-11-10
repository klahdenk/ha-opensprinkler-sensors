"""Setup and entity tests against a mocked controller."""

from __future__ import annotations

from datetime import timedelta

import aiohttp
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.opensprinkler_sensors.const import CONF_PASSWORD_HASH, DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .conftest import BASE_URL, PW_HASH, official_sensor_payloads


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="OpenSprinkler",
        unique_id="aa:bb:cc:dd:ee:ff",
        data={CONF_URL: BASE_URL, CONF_PASSWORD_HASH: PW_HASH},
    )


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = _entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _seconds_ago(hass: HomeAssistant, entity_id: str) -> float:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    when = dt_util.parse_datetime(state.state)
    assert when is not None, f"{entity_id} = {state.state}"
    return (dt_util.utcnow() - when).total_seconds()


async def test_soil_moisture_and_other_analog_sensors(hass: HomeAssistant, mock_controller) -> None:
    mock_controller()
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.sensor_api == "shop"

    moisture = hass.states.get("sensor.opensprinkler_tomatoes")
    assert moisture is not None
    assert moisture.state == "31.5"
    assert moisture.attributes["device_class"] == "moisture"
    assert moisture.attributes["unit_of_measurement"] == "%"
    assert moisture.attributes["state_class"] == "measurement"
    assert moisture.attributes["sensor_number"] == 1

    temperature = hass.states.get("sensor.opensprinkler_bed_temperature")
    assert temperature is not None
    assert temperature.state == "14.2"
    assert temperature.attributes["device_class"] == "temperature"


async def test_last_on_sensors(hass: HomeAssistant, mock_controller) -> None:
    mock_controller()
    await _setup(hass)

    # From the log: front lawn finished an hour ago.
    assert abs(_seconds_ago(hass, "sensor.opensprinkler_front_lawn_last_on") - 3600) < 5
    # Running right now.
    assert _seconds_ago(hass, "sensor.opensprinkler_back_lawn_last_on") < 5
    assert hass.states.get("sensor.opensprinkler_back_lawn_last_on").attributes["on"] is True
    # lrun (60 s ago) beats the older log entry (5000 s ago).
    assert abs(_seconds_ago(hass, "sensor.opensprinkler_beds_last_on") - 60) < 5
    # Never ran: entity exists but has no value yet.
    assert hass.states.get("sensor.opensprinkler_s05_last_on").state == "unknown"
    # Rain sensor input (s1) was active two hours ago.
    assert abs(_seconds_ago(hass, "sensor.opensprinkler_rain_sensor_last_on") - 7200) < 5
    # Soil sensor on input 2 has never been active.
    assert hass.states.get("sensor.opensprinkler_soil_sensor_2_last_on").state == "unknown"


async def test_last_run_duration(hass: HomeAssistant, mock_controller) -> None:
    mock_controller()
    await _setup(hass)

    def duration(entity_id: str):
        return hass.states.get(entity_id).attributes["last_run_duration"]

    assert duration("sensor.opensprinkler_front_lawn_last_on") == 900  # from the log
    assert duration("sensor.opensprinkler_beds_last_on") == 600  # from lrun
    assert duration("sensor.opensprinkler_rain_sensor_last_on") == 1200  # s1 log event
    # Running: elapsed since the program start time in ps.
    assert abs(duration("sensor.opensprinkler_back_lawn_last_on") - 100) < 5
    assert duration("sensor.opensprinkler_s05_last_on") is None


async def test_last_on_survives_restart(hass: HomeAssistant, mock_controller) -> None:
    # S05 has nothing in the log, so only the value saved before restart can fill it in.
    earlier = dt_util.utcnow() - timedelta(days=30)
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(
                    "sensor.opensprinkler_s05_last_on",
                    earlier.isoformat(),
                    {"last_run_duration": 1800},
                ),
                {
                    "native_value": {
                        "__type": "<class 'datetime.datetime'>",
                        "isoformat": earlier.isoformat(),
                    },
                    "native_unit_of_measurement": None,
                },
            )
        ],
    )
    mock_controller()
    await _setup(hass)
    assert abs(_seconds_ago(hass, "sensor.opensprinkler_s05_last_on") - 30 * 86400) < 5
    assert hass.states.get("sensor.opensprinkler_s05_last_on").attributes["last_run_duration"] == 1800


async def test_disabled_zones_get_no_entity(hass: HomeAssistant, mock_controller) -> None:
    mock_controller()
    await _setup(hass)
    assert hass.states.get("sensor.opensprinkler_s04_last_on") is None
    assert hass.states.get("sensor.opensprinkler_s08_last_on") is not None


async def test_official_firmware_sensors(hass: HomeAssistant, mock_controller) -> None:
    mock_controller(**official_sensor_payloads())
    entry = await _setup(hass)
    assert entry.runtime_data.sensor_api == "official"

    soil = hass.states.get("sensor.opensprinkler_center_tree_soil")
    assert soil is not None
    assert float(soil.state) == 43.1132
    assert soil.attributes["device_class"] == "moisture"
    assert soil.attributes["unit_of_measurement"] == "%"
    assert soil.attributes["sensor_number"] == 1
    assert "last_read" not in soil.attributes

    temperature = hass.states.get("sensor.opensprinkler_shed_temperature")
    assert temperature.attributes["device_class"] == "temperature"
    assert float(temperature.state) == 18.25

    # A read error shows as unknown rather than a bogus 0 %.
    assert hass.states.get("sensor.opensprinkler_broken_probe").state == "unknown"
    # Last-on sensors are unaffected by which sensor API the firmware has.
    assert hass.states.get("sensor.opensprinkler_front_lawn_last_on") is not None

    registry = er.async_get(hass)
    unique_ids = {e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    assert "aa:bb:cc:dd:ee:ff_sensor_1" in unique_ids


async def test_official_sensor_values_update(hass: HomeAssistant, mock_controller) -> None:
    payloads = mock_controller(**official_sensor_payloads())
    entry = await _setup(hass)
    payloads["jsn"]["sn"][0]["value"] = 51.0
    mock_controller(**payloads)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert float(hass.states.get("sensor.opensprinkler_center_tree_soil").state) == 51.0


async def test_stock_firmware_without_analog_api(hass: HomeAssistant, mock_controller) -> None:
    mock_controller(sl={"result": 32}, sg={"result": 32})
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.sensor_api is None
    assert hass.states.get("sensor.opensprinkler_tomatoes") is None
    assert hass.states.get("sensor.opensprinkler_front_lawn_last_on") is not None


async def test_log_unavailable_still_loads(hass: HomeAssistant, mock_controller) -> None:
    mock_controller(jl={"result": 16})
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    # No log, but lrun and live status still fill in.
    assert hass.states.get("sensor.opensprinkler_front_lawn_last_on").state == "unknown"
    assert abs(_seconds_ago(hass, "sensor.opensprinkler_beds_last_on") - 60) < 5


async def test_unique_ids_are_stable(hass: HomeAssistant, mock_controller) -> None:
    mock_controller()
    entry = await _setup(hass)
    registry = er.async_get(hass)
    unique_ids = {e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    assert "aa:bb:cc:dd:ee:ff_analog_1" in unique_ids
    assert "aa:bb:cc:dd:ee:ff_last_on_station_0" in unique_ids
    assert "aa:bb:cc:dd:ee:ff_last_on_s1" in unique_ids


async def test_new_analog_sensor_is_added_without_reload(
    hass: HomeAssistant, mock_controller, freezer
) -> None:
    payloads = mock_controller()
    entry = await _setup(hass)
    assert hass.states.get("sensor.opensprinkler_basil") is None

    payloads["sl"]["sensors"].append({"nr": 3, "type": 2, "name": "Basil"})
    payloads["sg"]["datas"].append(
        {"nr": 3, "data": 44.0, "unit": "%", "unitid": 1, "last": payloads["jc"]["devt"]}
    )
    mock_controller(**payloads)
    # Past the slow-refresh interval so /sl is re-read.
    freezer.tick(timedelta(hours=1, seconds=1))
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("sensor.opensprinkler_basil").state == "44.0"


async def test_auth_failure_starts_reauth(hass: HomeAssistant, mock_controller) -> None:
    mock_controller(jc={"result": 2})
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_connection_failure_retries(hass: HomeAssistant, mock_controller) -> None:
    mock_controller(jc=aiohttp.ClientError())
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload(hass: HomeAssistant, mock_controller) -> None:
    mock_controller()
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
