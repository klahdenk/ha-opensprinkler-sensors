"""Config flow tests."""

from __future__ import annotations

from unittest.mock import patch

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.opensprinkler_sensors.const import CONF_PASSWORD_HASH, DOMAIN
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import BASE_URL, PW_HASH


@pytest.fixture(autouse=True)
def skip_setup():
    """Don't run the real integration setup after the flow creates an entry."""
    with patch("custom_components.opensprinkler_sensors.async_setup_entry", return_value=True):
        yield


async def test_user_flow_creates_entry(hass: HomeAssistant, mock_controller) -> None:
    mock_controller()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.50", CONF_PASSWORD: "opendoor"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_URL: BASE_URL, CONF_PASSWORD_HASH: PW_HASH}
    assert result["result"].unique_id == "aa:bb:cc:dd:ee:ff"
    # Only the hash is stored, never the password.
    assert "opendoor" not in str(result["data"])


@pytest.mark.parametrize(
    ("jc", "error"),
    [
        ({"result": 2}, "invalid_auth"),
        (aiohttp.ClientError(), "cannot_connect"),
        ({"hello": "world"}, "cannot_connect"),
    ],
)
async def test_user_flow_errors_then_recovers(hass: HomeAssistant, mock_controller, jc, error) -> None:
    mock_controller(jc=jc)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.50", CONF_PASSWORD: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    mock_controller()
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.50", CONF_PASSWORD: "opendoor"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_already_configured(hass: HomeAssistant, mock_controller) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={CONF_URL: "http://old-address", CONF_PASSWORD_HASH: PW_HASH},
    ).add_to_hass(hass)
    mock_controller()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.1.50", CONF_PASSWORD: "opendoor"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    # A changed IP address is picked up rather than ignored.
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.data[CONF_URL] == BASE_URL


async def test_reauth(hass: HomeAssistant, mock_controller) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={CONF_URL: BASE_URL, CONF_PASSWORD_HASH: "0" * 32},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    mock_controller(jc={"result": 2})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "still-wrong"})
    assert result["errors"] == {"base": "invalid_auth"}

    mock_controller()
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "opendoor"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD_HASH] == PW_HASH
