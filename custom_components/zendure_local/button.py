from __future__ import annotations

from homeassistant.components.button import ButtonEntity

from .const import DOMAIN
from .entity import ZendureLocalEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ZendureEmergencyStopButton(coordinator, entry)])


class ZendureEmergencyStopButton(ZendureLocalEntity, ButtonEntity):
    """Forces the stop payload and drops ownership in one action.

    Stays available even when the profile is not actuator ready, because
    the safest reachable action must never itself be gated.
    """

    _attr_name = "Noodstop"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.unique_id}_emergency_stop"

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        await self.coordinator.controller.async_stop()
