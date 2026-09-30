from __future__ import annotations

from homeassistant.components.switch import SwitchEntity

from .const import DOMAIN
from .entity import ZendureLocalEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ZendureControlSwitch(coordinator, entry)])


class ZendureControlSwitch(ZendureLocalEntity, SwitchEntity):
    """Takes or releases single-writer ownership of the battery.

    Ownership is runtime-only and is never persisted, so Home Assistant
    always starts in OFF_SAFE and a restart cannot silently resume
    commanding the battery.
    """

    _attr_name = "HA-aansturing"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.unique_id}_ha_control"

    @property
    def available(self) -> bool:
        return self.coordinator.profile.actuator_ready

    @property
    def is_on(self) -> bool:
        return self.coordinator.controller.control_enabled

    @property
    def extra_state_attributes(self) -> dict:
        controller = self.coordinator.controller
        return {
            "ownership": controller.ownership,
            "block_reason": controller.block_reason,
            "applied_setpoint_w": controller.applied_w,
        }

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.controller.async_enable(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.controller.async_enable(False)
