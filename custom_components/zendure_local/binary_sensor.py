from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity

from .const import DOMAIN
from .entity import ZendureLocalEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            ZendureOnlineSensor(coordinator, entry),
            ZendureFaultSensor(coordinator, entry),
            ZendureCommandAcknowledgedSensor(coordinator, entry),
            ZendureCommandMismatchSensor(coordinator, entry),
        ]
    )


class ZendureOnlineSensor(ZendureLocalEntity, BinarySensorEntity):
    _attr_name = "Online"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.unique_id}_online"

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success


class ZendureFaultSensor(ZendureLocalEntity, BinarySensorEntity):
    _attr_name = "Storing"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.unique_id}_fault"

    @property
    def is_on(self) -> bool:
        normalized = self.coordinator.data.get("normalized", {})
        return bool((normalized.get("is_error") or 0) > 0)


class ZendureCommandAcknowledgedSensor(
    ZendureLocalEntity,
    BinarySensorEntity,
):
    """Whether the device reports back the limits we last commanded."""

    _attr_name = "Commando bevestigd"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.unique_id}_command_acknowledged"

    @property
    def is_on(self) -> bool:
        return self.coordinator.controller.command_acknowledged


class ZendureCommandMismatchSensor(ZendureLocalEntity, BinarySensorEntity):
    """Measured power contradicts the active command.

    This is the canary signal: it catches a battery that accepted a
    command but is doing something else, which is the failure mode that
    matters most before anything is allowed to run unattended.
    """

    _attr_name = "Commando wijkt af"
    _attr_device_class = "problem"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.unique_id}_command_mismatch"

    @property
    def is_on(self) -> bool:
        return self.coordinator.controller.command_mismatch
