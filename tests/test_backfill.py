"""Backfill of analog sensor history from the controller's sensor log (/jsl)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.opensprinkler_sensors.const import CONF_PASSWORD_HASH, DOMAIN
from homeassistant.components.recorder import Recorder, get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import BASE_URL, PW_HASH, TZ_OFFSET, official_sensor_payloads

SOIL = "sensor.opensprinkler_center_tree_soil"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock: Recorder, enable_custom_integrations: None) -> None:
    """Override conftest's version: the recorder must be set up before ``hass``."""


NOW = datetime(2026, 7, 1, 12, 30, tzinfo=dt_util.UTC)
HOUR = NOW.replace(minute=0)


def _local(when: datetime) -> int:
    return int(when.timestamp()) + TZ_OFFSET


def _log(hours_back: list[int], uuid: int = 1) -> list[list]:
    """Two readings per hour: value = 40 + hours_back, and that + 2."""
    records = []
    for back in hours_back:
        start = HOUR - timedelta(hours=back)
        records.append([uuid, _local(start + timedelta(minutes=10)), 40.0 + back])
        records.append([uuid, _local(start + timedelta(minutes=40)), 42.0 + back])
    return records


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="OpenSprinkler",
        unique_id="aa:bb:cc:dd:ee:ff",
        data={CONF_URL: BASE_URL, CONF_PASSWORD_HASH: PW_HASH},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)
    return entry


async def _hourly(hass: HomeAssistant, statistic_id: str) -> dict[datetime, tuple]:
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        HOUR - timedelta(days=40),
        None,
        {statistic_id},
        "hour",
        None,
        {"mean", "min", "max"},
    )
    return {
        dt_util.utc_from_timestamp(row["start"]): (row["mean"], row["min"], row["max"])
        for row in rows.get(statistic_id, [])
    }


def _jsl_calls(aioclient_mock) -> list:
    return [call for call in aioclient_mock.mock_calls if call[1].path == "/jsl"]


async def test_logged_hours_become_statistics(
    recorder_mock: Recorder, hass: HomeAssistant, mock_controller, aioclient_mock, freezer
) -> None:
    freezer.move_to(NOW)
    payloads = official_sensor_payloads()
    # Hours 3, 2 and 1 ago; another sensor's record and the current hour are ignored.
    payloads["jsl"] = _log([3, 2, 1]) + _log([1], uuid=2) + [[1, _local(NOW), 99.0]]
    mock_controller(**payloads)

    await _setup(hass)

    stats = await _hourly(hass, SOIL)
    assert stats == {
        HOUR - timedelta(hours=3): (44.0, 43.0, 45.0),
        HOUR - timedelta(hours=2): (43.0, 42.0, 44.0),
        HOUR - timedelta(hours=1): (42.0, 41.0, 43.0),
    }
    # One /jsl request per official sensor, asking for everything in the window.
    params = {call[1].query.get("uuid"): call[1].query for call in _jsl_calls(aioclient_mock)}
    assert set(params) == {"1", "2", "3", "4"}
    assert params["1"]["count"] == "all"
    assert int(params["1"]["before"]) == _local(HOUR)
    assert int(params["1"]["after"]) == _local(HOUR - timedelta(days=30))


async def test_no_log_imports_nothing(
    recorder_mock: Recorder, hass: HomeAssistant, mock_controller, freezer
) -> None:
    freezer.move_to(NOW)
    mock_controller(**official_sensor_payloads())  # /jsl answers {"result": 80}
    entry = await _setup(hass)
    assert entry.state.name == "LOADED"
    assert await _hourly(hass, SOIL) == {}


async def test_never_overwrites_home_assistant_statistics(
    recorder_mock: Recorder, hass: HomeAssistant, mock_controller, aioclient_mock, freezer
) -> None:
    freezer.move_to(NOW)
    # Home Assistant already has statistics from two hours ago onwards.
    async_import_statistics(
        hass,
        StatisticMetaData(
            mean_type=StatisticMeanType.ARITHMETIC,
            has_sum=False,
            name=None,
            source="recorder",
            statistic_id=SOIL,
            unit_class=None,
            unit_of_measurement="%",
        ),
        [StatisticData(start=HOUR - timedelta(hours=2), mean=10.0, min=10.0, max=10.0)],
    )
    await async_wait_recording_done(hass)

    payloads = official_sensor_payloads()
    payloads["jsl"] = _log([4, 3, 2, 1])
    mock_controller(**payloads)
    await _setup(hass)

    stats = await _hourly(hass, SOIL)
    assert stats[HOUR - timedelta(hours=2)] == (10.0, 10.0, 10.0)  # untouched
    assert stats[HOUR - timedelta(hours=3)] == (44.0, 43.0, 45.0)
    assert stats[HOUR - timedelta(hours=4)] == (45.0, 44.0, 46.0)
    assert HOUR - timedelta(hours=1) not in stats
    soil_call = next(c for c in _jsl_calls(aioclient_mock) if c[1].query.get("uuid") == "1")
    assert int(soil_call[1].query["before"]) == _local(HOUR - timedelta(hours=2))


async def test_shop_firmware_does_not_backfill(
    recorder_mock: Recorder, hass: HomeAssistant, mock_controller, aioclient_mock, freezer
) -> None:
    freezer.move_to(NOW)
    mock_controller()  # OpenSprinklerShop firmware fixture
    await _setup(hass)
    assert _jsl_calls(aioclient_mock) == []
