#!/usr/bin/env python3
"""A fake OpenSprinkler controller for previewing the integration.

Serves the endpoints the integration reads (/jc /jo /jn /js /jl /sl /sg) with
data that changes over time, so dashboards have something real to show:

- zones 1-4 run in sequence every 5 minutes (40 s each);
- the rain sensor (input 1) is active for 2 minutes out of every 10;
- soil moisture rises after the matching zone waters, then dries out;
- a week of watering history is in the log, so "last on" is filled at startup.

Analog sensors are served the way official firmware 2.2.1(5)+ does (``/jsn``)
by default; ``--api shop`` serves OpenSprinklerShop's ``/sl`` + ``/sg`` instead.

Stdlib only. Password: ``opendoor``.

    python3 dev/fake_controller.py --port 8099
"""

from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import time
from urllib.parse import parse_qs, urlparse

PASSWORD_HASH = hashlib.md5(b"opendoor").hexdigest()

ZONES = ["Front lawn", "Back lawn", "Vegetable beds", "Hedges", "S05", "S06", "S07", "S08"]
ACTIVE_ZONES = 4
DISABLED_BITS = 0b11110000  # S05-S08 disabled

CYCLE = 300  # a program starts every 5 minutes...
ZONE_SLOT = 45  # ...each zone gets a 45 s slot...
ZONE_RUN = 40  # ...and runs for 40 s of it.

RAIN_PERIOD = 600
RAIN_START, RAIN_END = 60, 180  # rain sensor active 1:00-3:00 of every 10 min

# Analog sensors: (nr, name, unit id, unit, zone that waters it, baseline)
ANALOG = [
    (1, "Tomatoes", 1, "%", 2, 42.0),
    (2, "Basil", 1, "%", 2, 27.0),
    (3, "Lawn", 1, "%", 0, 15.0),
    (4, "Bed temperature", 2, "°C", None, 14.0),
]

# Official firmware unit IDs for the same sensors: 40 = %VMC, 17 = °C.
OFFICIAL_UNITS = {1: 40, 2: 17}

STARTED = time.time()
SENSOR_API = "official"


def utc_offset() -> int:
    return time.localtime().tm_gmtoff


def local(t: float) -> int:
    """OpenSprinkler reports local time as epoch seconds."""
    return int(t + utc_offset())


def running_zone(t: float) -> int | None:
    phase = (t - STARTED) % CYCLE
    zone = int(phase // ZONE_SLOT)
    if zone < ACTIVE_ZONES and phase - zone * ZONE_SLOT < ZONE_RUN:
        return zone
    return None


def program_status(t: float) -> list[list[int]]:
    """``ps`` in /jc: [program, remaining seconds, start time] per zone."""
    status = [[0, 0, 0] for _ in ZONES]
    zone = running_zone(t)
    if zone is not None:
        start = STARTED + math.floor((t - STARTED) / CYCLE) * CYCLE + zone * ZONE_SLOT
        status[zone] = [2, int(start + ZONE_RUN - t), local(start)]
    return status


def last_end(t: float, zone: int) -> float | None:
    """End time of the zone's most recent finished run since startup."""
    cycle_start = STARTED + math.floor((t - STARTED) / CYCLE) * CYCLE
    end = cycle_start + zone * ZONE_SLOT + ZONE_RUN
    if end > t:
        end -= CYCLE
    return end if end >= STARTED else None


def rain_active(t: float) -> bool:
    phase = (t - STARTED) % RAIN_PERIOD
    return RAIN_START <= phase < RAIN_END


def history(days: int, now: float) -> list[list]:
    """A week of 06:00 watering plus a couple of rain events, then live runs."""
    records = []
    midnight = now - (now + utc_offset()) % 86400
    for day in range(7, 0, -1):
        start = midnight - day * 86400 + 6 * 3600
        for zone in range(ACTIVE_ZONES):
            end = start + (zone + 1) * 600
            records.append([1, zone, 600, local(end)])
        if day in (5, 2):
            records.append([0, "s1", 5400, local(start - 3600)])
    # Runs since the fake controller started.
    cycle = 0
    while True:
        cycle_start = STARTED + cycle * CYCLE
        if cycle_start > now:
            break
        for zone in range(ACTIVE_ZONES):
            end = cycle_start + zone * ZONE_SLOT + ZONE_RUN
            if end <= now:
                records.append([2, zone, ZONE_RUN, local(end)])
        cycle += 1
    cutoff = local(midnight - days * 86400)
    return [r for r in records if r[3] >= cutoff]


def moisture(t: float, zone: int | None, base: float) -> float:
    value = base + 3 * math.sin(t / 600)
    if zone is not None:
        end = last_end(t, zone)
        if running_zone(t) == zone:
            value += 15
        elif end is not None and t - end < 600:
            value += 15 * (1 - (t - end) / 600)
    return round(max(0.0, min(100.0, value)), 1)


def analog_value(t: float, unit_id: int, zone: int | None, base: float) -> float:
    if unit_id == 1:
        return moisture(t, zone, base)
    return round(base + 4 * math.sin(t / 1800), 1)


def respond(path: str, query: dict[str, list[str]]) -> object:
    now = time.time()
    if query.get("pw", [""])[0] != PASSWORD_HASH:
        return {"result": 2}

    if path == "jc":
        ends = [(last_end(now, z), z) for z in range(ACTIVE_ZONES)]
        finished = [(end, z) for end, z in ends if end is not None]
        lrun = [0, 0, 0, 0]
        if finished:
            end, zone = max(finished)
            lrun = [zone, 2, ZONE_RUN, local(end)]
        return {
            "devt": local(now),
            "nbrd": 1,
            "en": 1,
            "sn1": int(rain_active(now)),
            "sn2": 0,
            "rd": 0,
            "lrun": lrun,
            "ps": program_status(now),
            "mac": "02:00:00:0F:A4:E1",
            "loc": "Preview garden",
        }
    if path == "jo":
        tz = 48 + utc_offset() // 900
        return {"fwv": 240, "fwm": 199, "tz": tz, "sn1t": 1, "sn2t": 3, "hwv": 33}
    if path == "jn":
        return {"snames": ZONES, "maxlen": 32, "stn_dis": [DISABLED_BITS]}
    if path == "js":
        zone = running_zone(now)
        return {"sn": [int(i == zone) for i in range(len(ZONES))], "nstations": len(ZONES)}
    if path == "jl":
        return history(int(query.get("hist", ["7"])[0]), now)
    if path == "jsn" and SENSOR_API == "official":
        sensors = []
        for nr, name, unit_id, _unit, zone, base in ANALOG:
            value = analog_value(now, unit_id, zone, base)
            sensors.append(
                {
                    "uuid": nr,
                    "name": name,
                    "unit": OFFICIAL_UNITS[unit_id],
                    "flag": 5,
                    "status": 1,
                    "interval": 1,
                    "min": 0,
                    "max": 100,
                    "value": value,
                    "type": 1,
                    "extra": {"pin": nr, "scale": 1, "offset": 0, "subtype": 15},
                }
            )
        return {"sn": sensors, "count": len(sensors)}
    if path == "sl" and SENSOR_API == "shop":
        return {
            "count": len(ANALOG),
            "detected": 0,
            "sensors": [{"nr": nr, "type": 1, "name": name} for nr, name, *_ in ANALOG],
        }
    if path == "sg" and SENSOR_API == "shop":
        last = local(now - now % 60)
        datas = []
        for nr, _name, unit_id, unit, zone, base in ANALOG:
            value = analog_value(now, unit_id, zone, base)
            datas.append(
                {
                    "nr": nr,
                    "nativedata": int(value * 10),
                    "data": value,
                    "unit": unit,
                    "unitid": unit_id,
                    "last": last,
                }
            )
        return {"datas": datas}
    return {"result": 32}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        url = urlparse(self.path)
        body = json.dumps(respond(url.path.strip("/"), parse_qs(url.query))).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument(
        "--api",
        choices=["official", "shop"],
        default="official",
        help="analog sensor API to emulate: official /jsn, or OpenSprinklerShop /sl + /sg",
    )
    args = parser.parse_args()
    global SENSOR_API
    SENSOR_API = args.api
    print(
        f"Fake OpenSprinkler on http://{args.host}:{args.port} ({args.api} sensor API, password: opendoor)",
        flush=True,
    )
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
