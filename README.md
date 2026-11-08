# OpenSprinkler Sensors

A Home Assistant integration that reads two things from an
[OpenSprinkler](https://opensprinkler.com) controller over its local HTTP API:

- **Soil moisture**, plus any other analog sensor (temperature, humidity,
  voltage, water level and so on) connected through the
  [Analog Sensor API](https://opensprinklershop.github.io/analog-sensor-config/).
- **When each zone and sensor input was last on**, as timestamp sensors. These
  survive Home Assistant restarts and are seeded from the controller's own run
  log, so they're correct from the moment you install.

It complements the [OpenSprinkler integration](https://github.com/vinteo/hass-opensprinkler),
which controls zones and programs, and can run alongside it. This integration
only reads; it never starts or stops anything.

## Requirements

| Feature | Firmware |
| --- | --- |
| "Last on" for zones and the rain/soil sensor inputs | Any OpenSprinkler firmware 2.1.9+ |
| Soil moisture and other analog readings | [OpenSprinklerShop firmware](https://opensprinklershop.github.io/) 2.3.0+ with the Analog Sensor API |

On stock firmware the analog sensors are skipped and everything else still works.

## Installation

### HACS

1. In HACS, open the ⋮ menu → **Custom repositories**.
2. Add `https://github.com/klahdenk/ha-opensprinkler-sensors` with type **Integration**.
3. Install **OpenSprinkler Sensors** and restart Home Assistant.

### Manual

Copy `custom_components/opensprinkler_sensors` into your Home Assistant
`config/custom_components/` folder and restart.

## Setup

**Settings → Devices & services → Add integration → OpenSprinkler Sensors**, then enter:

- **Host**: the controller's IP address or URL, e.g. `192.168.1.50` or
  `http://opensprinkler.local:8080`.
- **Password**: the device password. You can paste its MD5 hash instead. Either
  way, only the hash is stored. (The factory default is `opendoor`; change it if
  you never have.)

If you later change the password on the controller, Home Assistant will ask you
to re-enter it.

## Entities

All entities belong to one device per controller. New zones and analog sensors
appear automatically, with no reload needed.

| Entity | Example | Notes |
| --- | --- | --- |
| Analog sensor | `sensor.opensprinkler_tomatoes` | Named as on the controller. Soil moisture sensors use the `moisture` device class and `%`. Attributes: `sensor_number`, `last_read`. |
| Zone last on | `sensor.opensprinkler_front_lawn_last_on` | Timestamp. Disabled zones are skipped. Attribute `on` is `true` while the zone runs. |
| Sensor input last on | `sensor.opensprinkler_rain_sensor_last_on` | For sensor inputs 1 and 2 when set to rain, soil or program switch. Flow meters are skipped. |

"Last on" is the end of the most recent run, or *now* while the zone is
running. It's built from the controller log (the last 7 days on startup, then
topped up hourly), the controller's last-run record, and live status polled every
30 seconds.

## Dashboard examples

### Soil moisture gauges

Using the built-in gauge card:

```yaml
type: grid
title: Soil moisture
columns: 3
square: false
cards:
  - type: gauge
    entity: sensor.opensprinkler_tomatoes
    needle: true
    min: 0
    max: 100
    segments:
      - { from: 0, color: "#db4437" }
      - { from: 20, color: "#ffa600" }
      - { from: 35, color: "#43a047" }
      - { from: 70, color: "#039be5" }
```

With [auto-entities](https://github.com/thomasloven/lovelace-auto-entities),
this picks up every moisture sensor automatically, driest first:

```yaml
type: custom:auto-entities
card:
  type: grid
  title: Soil moisture
  columns: 3
  square: false
card_param: cards
filter:
  include:
    - integration: opensprinkler_sensors
      attributes:
        device_class: moisture
      options:
        type: gauge
        needle: true
        min: 0
        max: 100
        segments:
          - { from: 0, color: "#db4437" }
          - { from: 20, color: "#ffa600" }
          - { from: 35, color: "#43a047" }
          - { from: 70, color: "#039be5" }
sort:
  method: state
  numeric: true
```

### Last on

```yaml
type: custom:auto-entities
card:
  type: entities
  title: Last on
filter:
  include:
    - integration: opensprinkler_sensors
      entity_id: "*_last_on"
      options:
        format: relative
sort:
  method: state
  reverse: true
```

Or list them by hand with the built-in entities card, using `format: relative`
on each row.

## Development

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt ruff
pytest
ruff check . && ruff format --check .
```

Tests run against a real Home Assistant core with the controller's HTTP
responses mocked. CI also runs `hassfest` and the HACS validator.

## License

MIT
