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
#
# Two firmware families expose analog sensors (soil moisture etc.) differently:
# - official OpenSprinkler firmware 2.2.1(5)+: ``/jsn`` (names + values);
# - OpenSprinklerShop firmware 2.3.0+: ``/sl`` (names) and ``/sg`` (values).
# Both are normalized into AnalogSensor, with ``kind`` naming what the value
# measures in a firmware-independent way (see KIND_*), so the sensor platform
# needs only one unit table.

SENSOR_API_OFFICIAL = "official"
SENSOR_API_SHOP = "shop"

KIND_MOISTURE = "moisture"
KIND_HUMIDITY = "humidity"
KIND_PERCENT = "percent"
KIND_TEMPERATURE_C = "temperature_c"
KIND_TEMPERATURE_F = "temperature_f"
KIND_TEMPERATURE_K = "temperature_k"
KIND_VOLTAGE = "voltage"
KIND_VOLTAGE_MV = "voltage_mv"
KIND_CURRENT_A = "current_a"
KIND_CURRENT_MA = "current_ma"
KIND_POWER_W = "power_w"
KIND_ENERGY_WH = "energy_wh"
KIND_ENERGY_KWH = "energy_kwh"
KIND_PRESSURE_KPA = "pressure_kpa"
KIND_PRESSURE_BAR = "pressure_bar"
KIND_PRESSURE_PA = "pressure_pa"
KIND_PRESSURE_PSI = "pressure_psi"
KIND_ILLUMINANCE = "illuminance"
KIND_PRECIPITATION_MM = "precipitation_mm"
KIND_PRECIPITATION_IN = "precipitation_in"
KIND_RAIN_RATE_MM_H = "rain_rate_mm_h"
KIND_RAIN_RATE_IN_H = "rain_rate_in_h"
KIND_SPEED_MS = "speed_ms"
KIND_SPEED_KMH = "speed_kmh"
KIND_SPEED_MPH = "speed_mph"
KIND_VOLUME_L = "volume_l"
KIND_VOLUME_GAL = "volume_gal"
KIND_WATER_L = "water_l"
KIND_WATER_GAL = "water_gal"
KIND_FLOW_L_MIN = "flow_l_min"
KIND_FLOW_GPM = "flow_gpm"
KIND_PH = "ph"

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

SHOP_UNIT_KINDS = {
    UNIT_PERCENT: KIND_MOISTURE,
    UNIT_DEGREE: KIND_TEMPERATURE_C,
    UNIT_FAHRENHEIT: KIND_TEMPERATURE_F,
    UNIT_VOLT: KIND_VOLTAGE,
    UNIT_HUM_PERCENT: KIND_HUMIDITY,
    UNIT_INCH: KIND_PRECIPITATION_IN,
    UNIT_MM: KIND_PRECIPITATION_MM,
    UNIT_MPH: KIND_SPEED_MPH,
    UNIT_KMH: KIND_SPEED_KMH,
    UNIT_LEVEL: KIND_PERCENT,
    UNIT_LX: KIND_ILLUMINANCE,
    UNIT_LITER: KIND_VOLUME_L,
    UNIT_GALLON: KIND_VOLUME_GAL,
    UNIT_LITER_CONSUMPTION: KIND_WATER_L,
    UNIT_GALLON_CONSUMPTION: KIND_WATER_GAL,
}

# Official firmware unit table (SensorUnit in sensors/sensor.h): id -> symbol.
OFFICIAL_UNIT_SYMBOLS = [
    "", "%", "ppm", "mV", "V", "mA", "A", "Ω", "mΩ", "kΩ", "",
    "L/s", "gal/s", "kPa", "bar", "Pa", "torr", "°C", "°F", "K", "lx",
    "lm", "mm", "cm", "m", "km", "in", "ft", "mi", "m/s", "km/h",
    "mph", "mL", "L", "m³", "gal", "ft³", "L/min", "gpm", "psi", "%VMC",
    "W", "mW", "Wh", "kWh", "uS/cm", "mS/cm", "pH", "deg", "rad", "mm/h",
    "in/h", "KB", "mm/day", "in/day", "kWh/m2/day",
]  # fmt: skip

OFFICIAL_UNIT_KINDS = {
    1: KIND_PERCENT,
    3: KIND_VOLTAGE_MV,
    4: KIND_VOLTAGE,
    5: KIND_CURRENT_MA,
    6: KIND_CURRENT_A,
    13: KIND_PRESSURE_KPA,
    14: KIND_PRESSURE_BAR,
    15: KIND_PRESSURE_PA,
    17: KIND_TEMPERATURE_C,
    18: KIND_TEMPERATURE_F,
    19: KIND_TEMPERATURE_K,
    20: KIND_ILLUMINANCE,
    29: KIND_SPEED_MS,
    30: KIND_SPEED_KMH,
    31: KIND_SPEED_MPH,
    33: KIND_VOLUME_L,
    35: KIND_VOLUME_GAL,
    37: KIND_FLOW_L_MIN,
    38: KIND_FLOW_GPM,
    39: KIND_PRESSURE_PSI,
    40: KIND_MOISTURE,  # volumetric moisture content, %VMC
    41: KIND_POWER_W,
    43: KIND_ENERGY_WH,
    44: KIND_ENERGY_KWH,
    47: KIND_PH,
    50: KIND_RAIN_RATE_MM_H,
    51: KIND_RAIN_RATE_IN_H,
}

# Official firmware runtime status bits (sensor_memory_t::status).
OFFICIAL_STATUS_VALID = 1 << 0
OFFICIAL_STATUS_ERROR = 1 << 1
OFFICIAL_STATUS_STALE = 1 << 2


@dataclass(frozen=True)
class AnalogSensor:
    """One analog sensor reading, from either firmware family."""

    key: str  # stable entity key: "sensor_<uuid>" (official) or "analog_<nr>" (shop)
    number: int  # the controller's own sensor id (uuid / nr)
    name: str
    value: float | None
    unit: str  # unit symbol as the controller shows it
    kind: str | None  # what it measures (KIND_*), None if unmapped
    last_read: datetime | None = None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return float(value)


def parse_official_sensors(data: dict[str, Any]) -> dict[str, AnalogSensor]:
    """``/jsn`` from official firmware 2.2.1(5)+, keyed by entity key."""
    sensors: dict[str, AnalogSensor] = {}
    for item in data.get("sn") or []:
        if not isinstance(item, dict):
            continue
        uuid = item.get("uuid")
        if not isinstance(uuid, int) or isinstance(uuid, bool) or uuid <= 0:
            continue
        status = item.get("status")
        status = status if isinstance(status, int) else 0
        valid = bool(status & OFFICIAL_STATUS_VALID) and not (
            status & (OFFICIAL_STATUS_ERROR | OFFICIAL_STATUS_STALE)
        )
        unit_id = item.get("unit")
        unit_id = unit_id if isinstance(unit_id, int) and not isinstance(unit_id, bool) else 0
        unit = OFFICIAL_UNIT_SYMBOLS[unit_id] if 0 <= unit_id < len(OFFICIAL_UNIT_SYMBOLS) else ""
        key = f"sensor_{uuid}"
        sensors[key] = AnalogSensor(
            key=key,
            number=uuid,
            name=str(item.get("name") or "").strip() or f"Sensor {uuid}",
            value=_number(item.get("value")) if valid else None,
            unit=unit,
            kind=OFFICIAL_UNIT_KINDS.get(unit_id),
        )
    return sensors


def parse_analog_sensors(
    sensor_list: dict[str, Any] | None,
    sensor_values: dict[str, Any],
    offset: int,
) -> dict[str, AnalogSensor]:
    """OpenSprinklerShop ``/sl`` (names) merged with ``/sg`` (readings)."""
    names: dict[int, str] = {}
    for item in (sensor_list or {}).get("sensors") or []:
        if isinstance(item, dict) and isinstance(item.get("nr"), int):
            names[item["nr"]] = str(item.get("name") or "").strip()

    sensors: dict[str, AnalogSensor] = {}
    for item in sensor_values.get("datas") or []:
        if not isinstance(item, dict):
            continue
        nr = item.get("nr")
        if not isinstance(nr, int) or nr <= 0:
            continue
        last_read = local_epoch_to_utc(item.get("last"), offset)
        unit_id = item.get("unitid")
        unit_id = unit_id if isinstance(unit_id, int) else UNIT_NONE
        key = f"analog_{nr}"
        sensors[key] = AnalogSensor(
            key=key,
            number=nr,
            name=names.get(nr) or f"Sensor {nr}",
            value=_number(item.get("data")) if last_read is not None else None,
            unit=str(item.get("unit") or ""),
            kind=SHOP_UNIT_KINDS.get(unit_id),
            last_read=last_read,
        )
    return sensors


# --- "Last on" tracking -----------------------------------------------------


def station_key(index: int) -> str:
    """Tracker key for a zone (0-based station index)."""
    return f"station_{index}"


class LastOnTracker:
    """Remembers when each zone / sensor input was last on, and for how long.

    Fed from three sources, newest wins:
    - the controller log (``/jl``), which has exact end times and durations for
      every run and for sensor-input activations;
    - ``lrun`` in ``/jc``, the end time and duration of the most recently
      finished run;
    - live status: anything on right now is "last on" *now*, with the time
      elapsed so far as its duration (from the program start time in ``ps``
      when the controller reports one, else from when we first saw it on).
    """

    def __init__(self) -> None:
        self._last: dict[str, datetime] = {}
        self._duration: dict[str, int] = {}
        self._on_since: dict[str, datetime] = {}

    def get(self, key: str) -> datetime | None:
        """End of the most recent run, or the latest poll while still on."""
        return self._last.get(key)

    def duration(self, key: str) -> int | None:
        """Length of that run in seconds (elapsed so far while still on)."""
        return self._duration.get(key)

    def record(self, key: str, when: datetime | None, duration: Any = None) -> None:
        if when is None:
            return
        seconds = (
            max(0, int(duration))
            if isinstance(duration, (int, float)) and not isinstance(duration, bool)
            else None
        )
        current = self._last.get(key)
        if current is None or when > current:
            self._last[key] = when
            if seconds is None:
                self._duration.pop(key, None)
            else:
                self._duration[key] = seconds
        elif when == current and seconds is not None and key not in self._duration:
            self._duration[key] = seconds

    def seed_from_log(self, records: list[Any], offset: int) -> None:
        """Ingest ``/jl`` records: ``[pid, sid, duration, end_time, (flow)]``."""
        for record in records:
            if not isinstance(record, list) or len(record) < 4:
                continue
            pid, sid, duration, end = record[:4]
            when = local_epoch_to_utc(end, offset)
            if pid == 0 and sid in SENSOR_INPUT_KEYS:
                self.record(sid, when, duration)
            elif pid != 0 and isinstance(sid, int) and not isinstance(sid, bool):
                self.record(station_key(sid), when, duration)

    def update(
        self,
        *,
        now: datetime,
        station_status: list[int],
        controller: dict[str, Any],
        offset: int,
    ) -> None:
        """Apply one poll's worth of live state."""
        program_status = controller.get("ps")
        if not isinstance(program_status, list):
            program_status = []

        for index, on in enumerate(station_status):
            key = station_key(index)
            if not on:
                self._on_since.pop(key, None)
                continue
            start = None
            if index < len(program_status):
                entry = program_status[index]
                # ps entry: [program_index, remaining_seconds, start_time, ...]
                if isinstance(entry, list) and len(entry) >= 3 and entry[0]:
                    start = local_epoch_to_utc(entry[2], offset)
            if start is None or start > now:
                start = self._on_since.setdefault(key, now)
            self.record(key, now, (now - start).total_seconds())

        lrun = controller.get("lrun")
        if (
            isinstance(lrun, list)
            and len(lrun) >= 4
            and isinstance(lrun[0], int)
            and not isinstance(lrun[0], bool)
        ):
            self.record(station_key(lrun[0]), local_epoch_to_utc(lrun[3], offset), lrun[2])

        for key in SENSOR_INPUT_KEYS:
            if controller.get(f"sn{key[1]}") == 1:
                start = self._on_since.setdefault(key, now)
                self.record(key, now, (now - start).total_seconds())
            else:
                self._on_since.pop(key, None)


def is_on(key: str, station_status: list[int], controller: dict[str, Any]) -> bool:
    """Whether the zone / sensor input behind ``key`` is on right now."""
    if key in SENSOR_INPUT_KEYS:
        return controller.get(f"sn{key[1]}") == 1
    index = int(key.split("_", 1)[1])
    return index < len(station_status) and bool(station_status[index])
