"""Base entity for OpenSprinkler Sensors."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo, format_mac
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OpenSprinklerCoordinator
from .model import firmware_version


class OpenSprinklerEntity(CoordinatorEntity[OpenSprinklerCoordinator]):
    """Entity attached to the controller's device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: OpenSprinklerCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        base = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{base}_{key}"

        mac = coordinator.data.controller.get("mac")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base)},
            connections={(CONNECTION_NETWORK_MAC, format_mac(mac))} if mac else set(),
            name=entry.title,
            manufacturer="OpenSprinkler",
            sw_version=firmware_version(coordinator.data.options),
            configuration_url=coordinator.client.base_url,
        )
