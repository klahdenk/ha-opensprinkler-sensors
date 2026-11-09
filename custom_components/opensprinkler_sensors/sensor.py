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
    UnitOfElectricPotential,
    UnitOfPrecipitationDepth,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import OpenSprinklerConfigEntry, OpenSprinklerCoordinator
from .entity import OpenSprinklerEntity
from .model import (
    SENSOR_TYPE_FLOW,
    SENSOR_TYPE_NONE,
    SENSOR_TYPE_PROGRAM_SWITCH,
    SENSOR_TYPE_RAIN,
    SENSOR_TYPE_SOIL,
    UNIT_DEGREE,
    UNIT_FAHRENHEIT,
    UNIT_GALLON,
    UNIT_GALLON_CONSUMPTION,
    UNIT_HUM_PERCENT,
    UNIT_INCH,
    UNIT_KMH,
    UNIT_LEVEL,
    UNIT_LITER,
    UNIT_LITER_CONSUMPTION,
    UNIT_LX,
    UNIT_MM,
    UNIT_MPH,
    UNIT_PERCENT,
    UNIT_VOLT,
    is_on,
    station_key,
)

# The controller is polled centrally by the coordinator.
PARALLEL_UPDATES = 0

# Analog unit ID -> (unit, device class, state class).
_UNITS: dict[int, tuple[str | None, SensorDeviceClass | None, SensorStateClass]] = {
    UNIT_PERCENT: (PERCENTAGE, SensorDeviceClass.MOISTURE, SensorStateClass.MEASUREMENT),
    UNIT_DEGREE: (UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT),
    UNIT_FAHRENHEIT: (
        UnitOfTemperature.FAHRENHEIT,
        SensorDeviceClass.TEMPERATURE,
        SensorStateClass.MEASUREMENT,
    ),
    UNIT_VOLT: (UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
    UNIT_HUM_PERCENT: (PERCENTAGE, SensorDeviceClass.HUMIDITY, SensorStateClass.MEASUREMENT),
    UNIT_INCH: (
        UnitOfPrecipitationDepth.INCHES,
        SensorDeviceClass.PRECIPITATION,
        SensorStateClass.MEASUREMENT,
    ),
    UNIT_MM: (
        UnitOfPrecipitationDepth.MILLIMETERS,
        SensorDeviceClass.PRECIPITATION,
        SensorStateClass.MEASUREMENT,
    ),
    UNIT_MPH: (UnitOfSpeed.MILES_PER_HOUR, SensorDeviceClass.WIND_SPEED, SensorStateClass.MEASUREMENT),
    UNIT_KMH: (UnitOfSpeed.KILOMETERS_PER_HOUR, SensorDeviceClass.WIND_SPEED, SensorStateClass.MEASUREMENT),
    UNIT_LEVEL: (PERCENTAGE, None, SensorStateClass.MEASUREMENT),
    UNIT_LX: (LIGHT_LUX, SensorDeviceClass.ILLUMINANCE, SensorStateClass.MEASUREMENT),
    UNIT_LITER: (UnitOfVolume.LITERS, SensorDeviceClass.VOLUME_STORAGE, SensorStateClass.MEASUREMENT),
    UNIT_GALLON: (UnitOfVolume.GALLONS, SensorDeviceClass.VOLUME_STORAGE, SensorStateClass.MEASUREMENT),
    UNIT_LITER_CONSUMPTION: (UnitOfVolume.LITERS, SensorDeviceClass.WATER, SensorStateClass.TOTAL_INCREASING),
    UNIT_GALLON_CONSUMPTION: (
        UnitOfVolume.GALLONS,
        SensorDeviceClass.WATER,
        SensorStateClass.TOTAL_INCREASING,
    ),
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

        for nr, analog in data.analog.items():
            key = f"analog_{nr}"
            if key not in known:
                known.add(key)
                new.append(AnalogSensorEntity(coordinator, nr, analog.unit_id, analog.unit))

        if new:
            async_add_entities(new)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class AnalogSensorEntity(OpenSprinklerEntity, SensorEntity):
    """A reading from the Analog Sensor API, e.g. soil moisture."""

    def __init__(
        self,
        coordinator: OpenSprinklerCoordinator,
        nr: int,
        unit_id: int,
        unit: str,
    ) -> None:
        super().__init__(coordinator, f"analog_{nr}")
        self._nr = nr
        native_unit, device_class, state_class = _UNITS.get(
            unit_id, (unit or None, None, SensorStateClass.MEASUREMENT)
        )
        self._attr_native_unit_of_measurement = native_unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class

    @property
    def _analog(self):
        return self.coordinator.data.analog.get(self._nr)

    @property
    def name(self) -> str:
        analog = self._analog
        return analog.name if analog else f"Sensor {self._nr}"

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
        return {
            "sensor_number": self._nr,
            "last_read": analog.last_read.isoformat() if analog and analog.last_read else None,
        }


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
