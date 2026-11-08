"""Home Assistant-independent logic for talking to OpenSprinkler.

Everything here is pure (stdlib only, no ``homeassistant`` or ``aiohttp``
imports) so it can be unit tested without a Home Assistant install.

API references:
- Stock firmware: https://opensprinklershop.github.io/2.2.1/221_4_api/
- Analog Sensor API (OpenSprinklerShop firmware 2.3.0+): the ``/sl`` and
  ``/sg`` handlers in opensprinkler_server.cpp of
  https://github.com/opensprinklershop/OpenSprinkler-Firmware
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import re
from typing import Any

# --- Errors -----------------------------------------------------------------

# OpenSprinkler answers most failures with HTTP 200 and {"result": <code>}.
RESULT_SUCCESS = 1
RESULT_UNAUTHORIZED = 2
RESULT_PAGE_NOT_FOUND = 32


class OpenSprinklerError(Exception):
    """Base error for OpenSprinkler API failures."""


class OpenSprinklerConnectionError(OpenSprinklerError):
    """The controller could not be reached."""


class OpenSprinklerAuthError(OpenSprinklerError):
    """The controller rejected the password."""


class OpenSprinklerNotSupportedError(OpenSprinklerError):
    """The endpoint does not exist on this firmware (e.g. no Analog Sensor API)."""


class OpenSprinklerResponseError(OpenSprinklerError):
    """The controller returned something unexpected."""


def parse_response(status: int, text: str) -> Any:
    """Decode a raw HTTP response, mapping OpenSprinkler error codes to exceptions."""
    if status in (401, 403):
        raise OpenSprinklerAuthError(f"HTTP {status}")
    if status == 404:
        raise OpenSprinklerNotSupportedError("HTTP 404")
    if status >= 400:
        raise OpenSprinklerResponseError(f"HTTP {status}")
    try:
        data = json.loads(text)
    except ValueError as err:
        raise OpenSprinklerResponseError("Response was not valid JSON") from err

    if isinstance(data, dict) and set(data) == {"result"}:
        code = data["result"]
        if code == RESULT_SUCCESS:
            return data
        if code == RESULT_UNAUTHORIZED:
            raise OpenSprinklerAuthError("Invalid password")
        if code == RESULT_PAGE_NOT_FOUND:
            raise OpenSprinklerNotSupportedError("Endpoint not found")
        raise OpenSprinklerResponseError(f"OpenSprinkler returned error code {code}")
    return data


# --- Connection settings ----------------------------------------------------

_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def password_hash(password: str) -> str:
    """Return the lowercase MD5 hash OpenSprinkler expects in the ``pw`` parameter.

    Input that already looks like an MD5 hash is passed through, so users can
    paste the hash instead of the plain password.
    """
    if _MD5_RE.match(password):
        return password.lower()
    return hashlib.md5(password.encode("utf-8")).hexdigest()


def normalize_base_url(host: str) -> str:
    """Turn ``192.168.1.50``, ``os.local:8080`` or a full URL into a base URL."""
    url = host.strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = f"http://{url}"
    return url.rstrip("/")


# --- Time -------------------------------------------------------------------


def timezone_offset(options: dict[str, Any], controller: dict[str, Any], now: datetime) -> int:
    """Seconds the controller's clock is ahead of UTC.

    OpenSprinkler reports every timestamp as *local* time expressed as epoch
    seconds. The ``tz`` option encodes the offset as ``hours * 4 + 48``. If it
    is missing, fall back to comparing the controller's clock (``devt``) with
    ours, rounded to the nearest 15 minutes.
    """
    tz = options.get("tz")
    if isinstance(tz, int) and not isinstance(tz, bool):
        return (tz - 48) * 900
    devt = controller.get("devt")
    if isinstance(devt, (int, float)) and devt > 0:
        return round((devt - now.timestamp()) / 900) * 900
    return 0


def local_epoch_to_utc(value: Any, offset: int) -> datetime | None:
    """Convert an OpenSprinkler local-time epoch to an aware UTC datetime."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return datetime.fromtimestamp(value - offset, tz=UTC)


# --- Controller metadata ----------------------------------------------------


def firmware_version(options: dict[str, Any]) -> str | None:
    """``fwv=221, fwm=4`` -> ``2.2.1(4)``."""
    fwv = options.get("fwv")
    if not isinstance(fwv, int):
        return None
    version = ".".join(str(fwv))
    fwm = options.get("fwm")
    return f"{version}({fwm})" if fwm else version


def station_disabled_flags(names: dict[str, Any], count: int) -> list[bool]:
    """Decode ``stn_dis`` (one bitfield byte per 8-station board) from ``/jn``."""
    bits = names.get("stn_dis") or []
    flags = []
    for i in range(count):
        board, bit = divmod(i, 8)
        flags.append(board < len(bits) and bool((int(bits[board]) >> bit) & 1))
    return flags


# Sensor input types (``sn1t``/``sn2t`` in ``/jo``).
SENSOR_TYPE_NONE = 0
SENSOR_TYPE_RAIN = 1
SENSOR_TYPE_FLOW = 2
SENSOR_TYPE_SOIL = 3
SENSOR_TYPE_PROGRAM_SWITCH = 240

SENSOR_INPUT_KEYS = ("s1", "s2")


def sensor_input_types(options: dict[str, Any]) -> dict[str, int]:
    """Type of each built-in sensor input, keyed ``s1``/``s2``."""
    types = {}
    for key in SENSOR_INPUT_KEYS:
        value = options.get(f"sn{key[1]}t")
        if isinstance(value, int):
            types[key] = value
        elif key == "s1" and options.get("urs") == 1:
            # Firmware before 2.1.9 only had a "use rain sensor" flag.
            types[key] = SENSOR_TYPE_RAIN
        else:
            types[key] = SENSOR_TYPE_NONE
    return types


# --- Analog sensors ---------------------------------------------------------

# Unit IDs from sensors.h in the OpenSprinklerShop firmware.
UNIT_NONE = 0
UNIT_PERCENT = 1  # soil moisture
UNIT_DEGREE = 2
UNIT_FAHRENHEIT = 3
UNIT_VOLT = 4
UNIT_HUM_PERCENT = 5  # air humidity
UNIT_INCH = 6
UNIT_MM = 7
UNIT_MPH = 8
UNIT_KMH = 9
UNIT_LEVEL = 10
UNIT_DK = 11
UNIT_LM = 12
UNIT_LX = 13
UNIT_LITER = 14
UNIT_GALLON = 15
UNIT_LITER_CONSUMPTION = 16
UNIT_GALLON_CONSUMPTION = 17
UNIT_USERDEF = 99


@dataclass(frozen=True)
class AnalogSensor:
    """One reading from the Analog Sensor API."""

    nr: int
    name: str
    value: float | None
    unit: str
    unit_id: int
    last_read: datetime | None


def parse_analog_sensors(
    sensor_list: dict[str, Any] | None,
    sensor_values: dict[str, Any],
    offset: int,
) -> dict[int, AnalogSensor]:
    """Merge ``/sl`` (names) with ``/sg`` (readings), keyed by sensor number."""
    names: dict[int, str] = {}
    for item in (sensor_list or {}).get("sensors") or []:
        if isinstance(item, dict) and isinstance(item.get("nr"), int):
            names[item["nr"]] = str(item.get("name") or "").strip()

    sensors: dict[int, AnalogSensor] = {}
    for item in sensor_values.get("datas") or []:
        if not isinstance(item, dict):
            continue
        nr = item.get("nr")
        if not isinstance(nr, int) or nr <= 0:
            continue
        last_read = local_epoch_to_utc(item.get("last"), offset)
        raw = item.get("data")
        value = (
            float(raw)
            if last_read is not None and isinstance(raw, (int, float)) and not isinstance(raw, bool)
            else None
        )
        unit_id = item.get("unitid")
        sensors[nr] = AnalogSensor(
            nr=nr,
            name=names.get(nr) or f"Sensor {nr}",
            value=value,
            unit=str(item.get("unit") or ""),
            unit_id=unit_id if isinstance(unit_id, int) else UNIT_NONE,
            last_read=last_read,
        )
    return sensors


# --- "Last on" tracking -----------------------------------------------------


def station_key(index: int) -> str:
    """Tracker key for a zone (0-based station index)."""
    return f"station_{index}"


class LastOnTracker:
    """Remembers the most recent time each zone / sensor input was on.

    Fed from three sources, newest wins:
    - the controller log (``/jl``), which has exact end times for every run
      and for sensor-input activations;
    - ``lrun`` in ``/jc``, the end time of the most recently finished run;
    - live status: anything on right now is "last on" *now*.
    """

    def __init__(self) -> None:
        self._last: dict[str, datetime] = {}

    def get(self, key: str) -> datetime | None:
        return self._last.get(key)

    def record(self, key: str, when: datetime | None) -> None:
        if when is None:
            return
        current = self._last.get(key)
        if current is None or when > current:
            self._last[key] = when

    def seed_from_log(self, records: list[Any], offset: int) -> None:
        """Ingest ``/jl`` records: ``[pid, sid, duration, end_time, (flow)]``."""
        for record in records:
            if not isinstance(record, list) or len(record) < 4:
                continue
            pid, sid, _duration, end = record[:4]
            when = local_epoch_to_utc(end, offset)
            if pid == 0 and sid in SENSOR_INPUT_KEYS:
                self.record(sid, when)
            elif pid != 0 and isinstance(sid, int) and not isinstance(sid, bool):
                self.record(station_key(sid), when)

    def update(
        self,
        *,
        now: datetime,
        station_status: list[int],
        controller: dict[str, Any],
        offset: int,
    ) -> None:
        """Apply one poll's worth of live state."""
        for index, on in enumerate(station_status):
            if on:
                self.record(station_key(index), now)

        lrun = controller.get("lrun")
        if (
            isinstance(lrun, list)
            and len(lrun) >= 4
            and isinstance(lrun[0], int)
            and not isinstance(lrun[0], bool)
        ):
            self.record(station_key(lrun[0]), local_epoch_to_utc(lrun[3], offset))

        for key in SENSOR_INPUT_KEYS:
            if controller.get(f"sn{key[1]}") == 1:
                self.record(key, now)


def is_on(key: str, station_status: list[int], controller: dict[str, Any]) -> bool:
    """Whether the zone / sensor input behind ``key`` is on right now."""
    if key in SENSOR_INPUT_KEYS:
        return controller.get(f"sn{key[1]}") == 1
    index = int(key.split("_", 1)[1])
    return index < len(station_status) and bool(station_status[index])
