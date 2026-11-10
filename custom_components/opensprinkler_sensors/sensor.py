"""Sensor entities: analog sensor readings and "last on" timestamps."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    LIGHT_LUX,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
    UnitOfVolumetricFlux,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import model
from .coordinator import OpenSprinklerConfigEntry, OpenSprinklerCoordinator
from .entity import OpenSprinklerEntity
from .model import (
    SENSOR_TYPE_FLOW,
    SENSOR_TYPE_NONE,
    SENSOR_TYPE_PROGRAM_SWITCH,
    SENSOR_TYPE_RAIN,
    SENSOR_TYPE_SOIL,
    AnalogSensor,
    is_on,
    station_key,
)

# The controller is polled centrally by the coordinator.
PARALLEL_UPDATES = 0

_M = SensorStateClass.MEASUREMENT
_TOTAL = SensorStateClass.TOTAL_INCREASING
_D = SensorDeviceClass

# What an analog sensor measures (model.KIND_*) -> (unit, device class, state class).
_KINDS: dict[str, tuple[str | None, SensorDeviceClass | None, SensorStateClass]] = {
    model.KIND_MOISTURE: (PERCENTAGE, _D.MOISTURE, _M),
    model.KIND_HUMIDITY: (PERCENTAGE, _D.HUMIDITY, _M),
    model.KIND_PERCENT: (PERCENTAGE, None, _M),
    model.KIND_TEMPERATURE_C: (UnitOfTemperature.CELSIUS, _D.TEMPERATURE, _M),
    model.KIND_TEMPERATURE_F: (UnitOfTemperature.FAHRENHEIT, _D.TEMPERATURE, _M),
    model.KIND_TEMPERATURE_K: (UnitOfTemperature.KELVIN, _D.TEMPERATURE, _M),
    model.KIND_VOLTAGE: (UnitOfElectricPotential.VOLT, _D.VOLTAGE, _M),
    model.KIND_VOLTAGE_MV: (UnitOfElectricPotential.MILLIVOLT, _D.VOLTAGE, _M),
    model.KIND_CURRENT_A: (UnitOfElectricCurrent.AMPERE, _D.CURRENT, _M),
    model.KIND_CURRENT_MA: (UnitOfElectricCurrent.MILLIAMPERE, _D.CURRENT, _M),
    model.KIND_POWER_W: (UnitOfPower.WATT, _D.POWER, _M),
    model.KIND_ENERGY_WH: (UnitOfEnergy.WATT_HOUR, _D.ENERGY, _TOTAL),
    model.KIND_ENERGY_KWH: (UnitOfEnergy.KILO_WATT_HOUR, _D.ENERGY, _TOTAL),
    model.KIND_PRESSURE_KPA: (UnitOfPressure.KPA, _D.PRESSURE, _M),
    model.KIND_PRESSURE_BAR: (UnitOfPressure.BAR, _D.PRESSURE, _M),
    model.KIND_PRESSURE_PA: (UnitOfPressure.PA, _D.PRESSURE, _M),
    model.KIND_PRESSURE_PSI: (UnitOfPressure.PSI, _D.PRESSURE, _M),
    model.KIND_ILLUMINANCE: (LIGHT_LUX, _D.ILLUMINANCE, _M),
    model.KIND_PRECIPITATION_MM: (UnitOfPrecipitationDepth.MILLIMETERS, _D.PRECIPITATION, _M),
    model.KIND_PRECIPITATION_IN: (UnitOfPrecipitationDepth.INCHES, _D.PRECIPITATION, _M),
    model.KIND_RAIN_RATE_MM_H: (
        UnitOfVolumetricFlux.MILLIMETERS_PER_HOUR,
        _D.PRECIPITATION_INTENSITY,
        _M,
    ),
    model.KIND_RAIN_RATE_IN_H: (UnitOfVolumetricFlux.INCHES_PER_HOUR, _D.PRECIPITATION_INTENSITY, _M),
    model.KIND_SPEED_MS: (UnitOfSpeed.METERS_PER_SECOND, _D.SPEED, _M),
    model.KIND_SPEED_KMH: (UnitOfSpeed.KILOMETERS_PER_HOUR, _D.WIND_SPEED, _M),
    model.KIND_SPEED_MPH: (UnitOfSpeed.MILES_PER_HOUR, _D.WIND_SPEED, _M),
    model.KIND_VOLUME_L: (UnitOfVolume.LITERS, _D.VOLUME_STORAGE, _M),
    model.KIND_VOLUME_GAL: (UnitOfVolume.GALLONS, _D.VOLUME_STORAGE, _M),
    model.KIND_WATER_L: (UnitOfVolume.LITERS, _D.WATER, _TOTAL),
    model.KIND_WATER_GAL: (UnitOfVolume.GALLONS, _D.WATER, _TOTAL),
    model.KIND_FLOW_L_MIN: (UnitOfVolumeFlowRate.LITERS_PER_MINUTE, _D.VOLUME_FLOW_RATE, _M),
    model.KIND_FLOW_GPM: (UnitOfVolumeFlowRate.GALLONS_PER_MINUTE, _D.VOLUME_FLOW_RATE, _M),
    model.KIND_PH: (None, _D.PH, _M),
}

_INPUT_LABELS = {
    SENSOR_TYPE_RAIN: "Rain sensor",
    SENSOR_TYPE_SOIL: "Soil sensor",
    SENSOR_TYPE_PROGRAM_SWITCH: "Program switch",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenSprinklerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create entities, adding new zones / sensors as they appear on the controller."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_entities() -> None:
        data = coordinator.data
        new: list[SensorEntity] = []

        for index in range(len(data.station_names)):
            key = station_key(index)
            if not data.station_disabled[index] and key not in known:
                known.add(key)
                new.append(LastOnSensor(coordinator, key))

        for key, sensor_type in data.sensor_types.items():
            # A flow meter is never "on"; it only counts pulses.
            if sensor_type in (SENSOR_TYPE_NONE, SENSOR_TYPE_FLOW) or key in known:
                continue
            known.add(key)
            new.append(LastOnSensor(coordinator, key))

        for key, analog in data.analog.items():
            if key not in known:
                known.add(key)
                new.append(AnalogSensorEntity(coordinator, analog))

        if new:
            async_add_entities(new)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class AnalogSensorEntity(OpenSprinklerEntity, SensorEntity):
    """An analog sensor reading, e.g. soil moisture."""

    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: OpenSprinklerCoordinator, analog: AnalogSensor) -> None:
        super().__init__(coordinator, analog.key)
        self._key = analog.key
        self._number = analog.number
        native_unit, device_class, state_class = _KINDS.get(
            analog.kind or "", (analog.unit.strip() or None, None, _M)
        )
        self._attr_native_unit_of_measurement = native_unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class

    @property
    def _analog(self) -> AnalogSensor | None:
        return self.coordinator.data.analog.get(self._key)

    @property
    def name(self) -> str:
        analog = self._analog
        return analog.name if analog else f"Sensor {self._number}"

    @property
    def available(self) -> bool:
        return super().available and self._analog is not None

    @property
    def native_value(self) -> float | None:
        analog = self._analog
        return analog.value if analog else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        analog = self._analog
        attributes: dict[str, Any] = {"sensor_number": self._number}
        if analog and analog.last_read:
            attributes["last_read"] = analog.last_read.isoformat()
        return attributes


class LastOnSensor(OpenSprinklerEntity, RestoreSensor):
    """When a zone or built-in sensor input was last on."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: OpenSprinklerCoordinator, key: str) -> None:
        super().__init__(coordinator, f"last_on_{key}")
        self._key = key

    @property
    def _station_index(self) -> int | None:
        return int(self._key.split("_", 1)[1]) if self._key.startswith("station_") else None

    @property
    def name(self) -> str:
        data = self.coordinator.data
        index = self._station_index
        if index is not None:
            label = data.station_names[index] if index < len(data.station_names) else f"Zone {index + 1}"
        else:
            label = _INPUT_LABELS.get(data.sensor_types.get(self._key, 0), "Sensor")
            if self._key == "s2":
                label = f"{label} 2"
        return f"{label} last on"

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        index = self._station_index
        return index is None or index < len(self.coordinator.data.station_names)

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.tracker.get(self._key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        return {
            "on": is_on(self._key, data.station_status, data.controller),
            # Seconds; elapsed so far while the zone / input is still on.
            "last_run_duration": self.coordinator.tracker.duration(self._key),
        }

    async def async_added_to_hass(self) -> None:
        """Merge the value saved before restart with what the log already told us."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and isinstance(last.native_value, datetime):
            last_state = await self.async_get_last_state()
            duration = last_state.attributes.get("last_run_duration") if last_state else None
            self.coordinator.tracker.record(self._key, last.native_value, duration)
