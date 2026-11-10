"""Import a sensor's history from the controller's own sensor log.

Official OpenSprinkler firmware can log each analog sensor (Edit Sensors ->
Logging). When an analog sensor entity is added, the readings the controller
logged before Home Assistant had any statistics for it are bucketed into hours
and imported as long-term statistics, so history and statistics graphs also
cover the time before the integration was installed.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import BACKFILL_DAYS, LOGGER
from .model import (
    SENSOR_API_OFFICIAL,
    OpenSprinklerAuthError,
    OpenSprinklerError,
    hourly_statistics,
)

if TYPE_CHECKING:
    from .coordinator import OpenSprinklerCoordinator


async def async_backfill_sensor(
    hass: HomeAssistant,
    coordinator: OpenSprinklerCoordinator,
    *,
    entity_id: str,
    uuid: int,
    unit: str | None,
    device_class: str | None,
) -> int:
    """Import logged history for one sensor; returns the number of hours imported."""
    if coordinator.sensor_api != SENSOR_API_OFFICIAL or "recorder" not in hass.config.components:
        return 0
    try:
        from homeassistant.components.recorder import get_instance  # noqa: PLC0415
        from homeassistant.components.recorder.models import (  # noqa: PLC0415
            StatisticData,
            StatisticMeanType,
            StatisticMetaData,
        )
        from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
            async_import_statistics,
            statistics_during_period,
        )
        from homeassistant.components.sensor import UNIT_CONVERTERS  # noqa: PLC0415
    except ImportError:  # older Home Assistant without mean_type statistics
        LOGGER.debug("Sensor log backfill needs a newer Home Assistant; skipping")
        return 0

    now = dt_util.utcnow()
    window_start = (now - timedelta(days=BACKFILL_DAYS)).replace(minute=0, second=0, microsecond=0)
    # Only fill hours before Home Assistant's own statistics for this entity begin.
    existing = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, window_start, None, {entity_id}, "hour", None, {"mean"}
    )
    rows = existing.get(entity_id) or []
    end: datetime = (
        dt_util.utc_from_timestamp(rows[0]["start"])
        if rows
        else now.replace(minute=0, second=0, microsecond=0)
    )
    if end <= window_start:
        return 0

    offset = coordinator.data.offset
    try:
        records = await coordinator.client.sensor_log(
            uuid,
            after=int(window_start.timestamp()) + offset,
            before=int(end.timestamp()) + offset,
        )
    except OpenSprinklerAuthError:
        return 0  # the coordinator surfaces this and starts reauth
    except OpenSprinklerError as err:
        # Most often: logging is off for every sensor, so there is no log yet.
        LOGGER.debug("No sensor log for %s: %s", entity_id, err)
        return 0

    stats = hourly_statistics(records, uuid, offset, end)
    if not stats:
        return 0

    converter = UNIT_CONVERTERS.get(device_class) if device_class else None
    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.ARITHMETIC,
        has_sum=False,
        name=None,
        source="recorder",
        statistic_id=entity_id,
        unit_class=converter.UNIT_CLASS if converter and unit in converter.VALID_UNITS else None,
        unit_of_measurement=unit,
    )
    async_import_statistics(
        hass,
        metadata,
        [StatisticData(start=s.start, mean=s.mean, min=s.min, max=s.max) for s in stats],
    )
    LOGGER.debug("Imported %d hours of logged history for %s", len(stats), entity_id)
    return len(stats)
