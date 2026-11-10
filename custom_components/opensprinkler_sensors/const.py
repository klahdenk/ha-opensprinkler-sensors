"""Constants for the OpenSprinkler Sensors integration."""

from datetime import timedelta
import logging

DOMAIN = "opensprinkler_sensors"
LOGGER = logging.getLogger(__package__)

CONF_PASSWORD_HASH = "password_hash"

# Live state (/jc, /js, /sg).
SCAN_INTERVAL = timedelta(seconds=30)
# Things that rarely change (/jo, /jn, /sl) plus a log top-up that catches
# runs too short to be seen between two polls.
SLOW_REFRESH_INTERVAL = timedelta(hours=1)
# How far back the controller log is read on startup to seed "last on".
LOG_SEED_DAYS = 7

REQUEST_TIMEOUT = 15

# How far back analog sensor history is imported from the controller's own
# sensor log (official firmware /jsl) into Home Assistant's statistics.
BACKFILL_DAYS = 30
