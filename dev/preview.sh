#!/usr/bin/env bash
# Preview the integration: a fake OpenSprinkler controller plus a throwaway
# Home Assistant (config in dev/config) with a dashboard of the example cards.
#
#   dev/preview.sh                     # uses `hass` and `python3` from PATH
#   HASS=.venv/bin/hass dev/preview.sh
#
# Then open http://127.0.0.1:8123/opensprinkler-preview/garden. Ctrl-C stops both.
# Delete dev/config's untracked files (git clean -fdX dev/config) to start fresh.
set -euo pipefail
cd "$(dirname "$0")"

HASS=${HASS:-hass}
PYTHON=${PYTHON:-python3}
if ! command -v "$HASS" >/dev/null; then
  echo "Home Assistant not found. In a Python 3.13 venv: pip install homeassistant" >&2
  echo "or point HASS at an existing install: HASS=/path/to/hass $0" >&2
  exit 1
fi

FAKE="" HA=""
trap '[ -n "$HA" ] && kill "$HA" 2>/dev/null; [ -n "$FAKE" ] && kill "$FAKE" 2>/dev/null; true' EXIT

"$PYTHON" fake_controller.py --port 8099 &
FAKE=$!
"$HASS" -c config &
HA=$!

status=0
"$PYTHON" bootstrap.py || status=$?
if [ "$status" -eq 3 ]; then
  # First run: restart so the integrations onboarding added are fully unloaded.
  kill "$HA"
  wait "$HA" 2>/dev/null || true
  "$HASS" -c config &
  HA=$!
  "$PYTHON" bootstrap.py
elif [ "$status" -ne 0 ]; then
  exit "$status"
fi
echo
echo "Dashboard: http://127.0.0.1:8123/opensprinkler-preview/garden   (Ctrl-C to stop)"
wait "$HA"
