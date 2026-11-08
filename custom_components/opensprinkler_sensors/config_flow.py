"""Config flow for OpenSprinkler Sensors."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac

from .api import OpenSprinklerClient
from .const import CONF_PASSWORD_HASH, DOMAIN, LOGGER
from .model import (
    OpenSprinklerAuthError,
    OpenSprinklerError,
    normalize_base_url,
    password_hash,
)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PASSWORD): str,
    }
)
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


async def _async_connect(hass: HomeAssistant, url: str, pw_hash: str) -> dict[str, Any]:
    client = OpenSprinklerClient(async_get_clientsession(hass), url, pw_hash)
    return await client.controller()


class OpenSprinklerSensorsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for OpenSprinkler Sensors."""

    VERSION = 1

    async def _async_try_connect(self, url: str, pw_hash: str) -> tuple[dict[str, Any] | None, str | None]:
        try:
            return await _async_connect(self.hass, url, pw_hash), None
        except OpenSprinklerAuthError:
            return None, "invalid_auth"
        except OpenSprinklerError:
            return None, "cannot_connect"
        except Exception:  # noqa: BLE001
            LOGGER.exception("Unexpected error connecting to OpenSprinkler")
            return None, "unknown"

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the controller address and password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = normalize_base_url(user_input[CONF_HOST])
            pw_hash = password_hash(user_input[CONF_PASSWORD])
            controller, error = await self._async_try_connect(url, pw_hash)
            if error:
                errors["base"] = error
            else:
                mac = controller.get("mac") if controller else None
                await self.async_set_unique_id(format_mac(mac) if mac else url)
                self._abort_if_unique_id_configured(updates={CONF_URL: url, CONF_PASSWORD_HASH: pw_hash})
                return self.async_create_entry(
                    title="OpenSprinkler",
                    data={CONF_URL: url, CONF_PASSWORD_HASH: pw_hash},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, {CONF_HOST: (user_input or {}).get(CONF_HOST)}
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauth when the controller rejects the stored password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the new password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            pw_hash = password_hash(user_input[CONF_PASSWORD])
            _, error = await self._async_try_connect(entry.data[CONF_URL], pw_hash)
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_PASSWORD_HASH: pw_hash})

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )
