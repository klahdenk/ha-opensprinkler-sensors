# OpenSprinkler Sensors

A Home Assistant integration that reads two things from an
[OpenSprinkler](https://opensprinkler.com) controller over its local HTTP API:

- **Soil moisture**, plus any other analog sensor (temperature, pressure,
  voltage, water level and so on) configured on the controller.
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
| Soil moisture and other analog readings | Official OpenSprinkler firmware **2.2.1(5)+** (sensors on the v4.0 built-in ports or a [Sensor Expander](https://opensprinkler.com/product/sensor-expander/)), or [OpenSprinklerShop firmware](https://opensprinklershop.github.io/) 2.3.0+ with its Analog Sensor API |

The integration detects which sensor API the firmware has (official `/jsn`
first, then OpenSprinklerShop's `/sl` + `/sg`). On firmware with neither, the
analog sensors are skipped and everything else still works.

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
| Analog sensor | `sensor.opensprinkler_tomatoes` | Named as on the controller. Soil moisture (%VMC on official firmware) uses the `moisture` device class and `%`; other units map to the matching device class where Home Assistant has one. A read error or stale reading shows as unknown. Attributes: `sensor_number`, and `last_read` on OpenSprinklerShop firmware. |
| Zone last on | `sensor.opensprinkler_front_lawn_last_on` | Timestamp. Disabled zones are skipped. Attributes: `on` (`true` while the zone runs) and `last_run_duration` (seconds; elapsed so far while running). |
| Sensor input last on | `sensor.opensprinkler_rain_sensor_last_on` | For sensor inputs 1 and 2 when set to rain, soil or program switch. Flow meters are skipped. Same attributes as zones. |

**History from before installation.** On official firmware, sensors with
**Logging** enabled (OpenSprinkler app → Edit Sensors) keep their own history
on the controller. When an analog sensor entity is added, the integration
imports up to 30 days of that log into Home Assistant's long-term statistics as
hourly mean/min/max, for the hours before Home Assistant's own statistics for
that sensor begin, so it never overwrites anything Home Assistant recorded
itself. History and statistics graphs then cover the time before the
integration was installed. Without logging enabled there is nothing to import,
and history simply starts when the integration does.

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

### Last on, with run duration

A built-in markdown card that lists every zone and sensor input this
integration knows about, most recent first, with how long each last ran:

```yaml
type: markdown
title: Last on
content: |
  {%- macro duration(s) -%}
    {%- if s is none -%}—
    {%- elif s >= 3600 -%}{{ s // 3600 }} h {{ (s % 3600) // 60 }} min
    {%- elif s >= 60 -%}{{ s // 60 }} min{{ (' ' ~ (s % 60) ~ ' s') if s % 60 else '' }}
    {%- else -%}{{ s }} s
    {%- endif -%}
  {%- endmacro %}
  {% set all = expand(integration_entities('opensprinkler_sensors') | select('search', '_last_on$') | list) %}
  {% set never = ['unknown', 'unavailable'] %}
  {% set rows = (all | rejectattr('state', 'in', never) | sort(attribute='state', reverse=true) | list)
                + (all | selectattr('state', 'in', never) | list) %}
  | | Last on | Ran for |
  |:--|--:|--:|
  {% for s in rows -%}
  | {{ s.name | replace('OpenSprinkler ', '') | replace(' last on', '') }} | {{ '🟢 running' if s.attributes.on else ('—' if s.state in never else relative_time(s.state | as_datetime) ~ ' ago') }} | {{ duration(s.attributes.last_run_duration) }} |
  {% endfor %}
```

## Preview locally

`dev/` has a fake OpenSprinkler controller (`dev/fake_controller.py`: zones
cycling every 5 minutes, drifting moisture, a rain sensor, a week of log
history) and a throwaway Home Assistant config with a dashboard of the cards
above. With Home Assistant installed in a Python 3.13 venv:

```bash
HASS=.venv/bin/hass dev/preview.sh
```

The first run onboards a local user and adds the integration, which takes a
minute or two. Then open http://127.0.0.1:8123/opensprinkler-preview/garden; no
login is needed from localhost. Run `git clean -fdX dev/config` to start over.

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
