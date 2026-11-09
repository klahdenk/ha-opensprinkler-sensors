#!/usr/bin/env python3
"""First-run setup for the preview Home Assistant in dev/config.

Waits for Home Assistant to come up, then, on a fresh config only:
1. completes onboarding with a local "preview" user (random password saved to
   dev/config/.preview-credentials, which git ignores; you never need it,
   because localhost logins are bypassed);
2. removes the default integrations onboarding adds (TTS, weather, radio...),
   which would only pull in extra dependencies and clutter the preview;
3. adds the OpenSprinkler Sensors integration pointed at the fake controller;
4. exits with RESTART_NEEDED so preview.sh restarts Home Assistant, fully
   unloading the removed integrations.

Stdlib only. Safe to run again: it does nothing once onboarding is done.
"""

from __future__ import annotations

import json
from pathlib import Path
import secrets
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8123"
CLIENT_ID = f"{BASE}/"
CONTROLLER = {"host": "127.0.0.1:8099", "password": "opendoor"}
CREDENTIALS = Path(__file__).parent / "config" / ".preview-credentials"
# Exit code asking preview.sh to restart Home Assistant: a `hass` started from a
# shell exits on restart instead of relaunching itself.
RESTART_NEEDED = 3


def call(
    path: str,
    body: dict | None = None,
    token: str | None = None,
    form: bool = False,
    method: str | None = None,
) -> object:
    headers = {}
    data = None
    if body is not None:
        if form:
            data = urlencode(body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    with urlopen(request, timeout=30) as resp:
        text = resp.read().decode()
    return json.loads(text) if text else None


def wait_for_home_assistant(timeout: float = 900) -> list[dict]:
    """The first start installs the frontend, which can take a few minutes."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            return call("/api/onboarding")  # type: ignore[return-value]
        except HTTPError as err:
            if err.code == 404:
                return []  # onboarding finished earlier, so the endpoint is gone
            time.sleep(3)
        except (URLError, ConnectionError):
            time.sleep(3)
    sys.exit("Home Assistant did not start in time; check its output above.")


def main() -> None:
    print("Waiting for Home Assistant...", flush=True)
    steps = wait_for_home_assistant()
    pending = [s["step"] for s in steps if not s["done"]]
    if "user" not in pending:
        print("Already set up.")
        return

    password = secrets.token_urlsafe(18)
    CREDENTIALS.write_text(f"username: preview\npassword: {password}\n")
    auth_code = call(
        "/api/onboarding/users",
        {
            "client_id": CLIENT_ID,
            "name": "Preview",
            "username": "preview",
            "password": password,
            "language": "en",
        },
    )["auth_code"]
    token = call(
        "/auth/token",
        {"grant_type": "authorization_code", "code": auth_code, "client_id": CLIENT_ID},
        form=True,
    )["access_token"]

    for step in pending:
        if step == "user":
            continue
        body = (
            {"client_id": CLIENT_ID, "redirect_uri": f"{CLIENT_ID}?auth_callback=1"}
            if step == "integration"
            else {}
        )
        call(f"/api/onboarding/{step}", body, token)

    for entry in call("/api/config/config_entries/entry", token=token):
        if entry["domain"] != "opensprinkler_sensors":
            call(f"/api/config/config_entries/entry/{entry['entry_id']}", token=token, method="DELETE")

    flow = call("/api/config/config_entries/flow", {"handler": "opensprinkler_sensors"}, token)
    result = call(f"/api/config/config_entries/flow/{flow['flow_id']}", CONTROLLER, token)
    if result.get("type") != "create_entry":
        sys.exit(f"Adding the integration failed: {result}")
    print("Onboarded and added OpenSprinkler Sensors; restarting Home Assistant.", flush=True)
    sys.exit(RESTART_NEEDED)


if __name__ == "__main__":
    main()
