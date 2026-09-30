from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers.entity import EntityCategory

from .const import COMMISSIONING_CEILING_W, DOMAIN, SETPOINT_STEP_W
from .entity import ZendureLocalEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            ZendureSetpointNumber(coordinator, entry),
            ZendureChargeCeilingNumber(coordinator, entry),
            ZendureDischargeCeilingNumber(coordinator, entry),
        ]
    )


class ZendureSetpointNumber(ZendureLocalEntity, NumberEntity):
    """One signed setpoint: positive discharges, negative charges.

    The bounds come from the commissioning caps on the config entry, so
    a canary battery physically cannot be commanded beyond the limit the
    operator qualified it for.
    """

    _attr_name = "Commando-vermogen"
    _attr_native_unit_of_measurement = "W"
    _attr_native_step = SETPOINT_STEP_W
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.unique_id}_setpoint"

    @property
    def available(self) -> bool:
        return self.coordinator.profile.actuator_ready

    @property
    def native_min_value(self) -> float:
        return -self.coordinator.controller.max_charge_w

    @property
    def native_max_value(self) -> float:
        return self.coordinator.controller.max_discharge_w

    @property
    def native_value(self) -> float:
        return self.coordinator.controller.requested_w

    @property
    def extra_state_attributes(self) -> dict:
        controller = self.coordinator.controller
        return {
            "applied_setpoint_w": controller.applied_w,
            "block_reason": controller.block_reason,
            "direction": (
                "discharge"
                if controller.applied_w > 0
                else "charge"
                if controller.applied_w < 0
                else "stop"
            ),
        }

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.controller.async_set_setpoint(value)


class ZendureChargeCeilingNumber(ZendureLocalEntity, NumberEntity):
    """Device-side persisted max-charge ceiling (mirrors the Zendure app).

    Writing this replaces the app's charge-power slider so the device
    never needs to be commissioned through the app again. Unlike the
    runtime setpoint this is believed to write straight to flash, so it
    is a typed box rather than a slider and the HEMS optimizer never
    touches it.
    """

    _attr_name = "Max oplaadvermogen (apparaat)"
    _attr_native_unit_of_measurement = "W"
    _attr_native_step = SETPOINT_STEP_W
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = COMMISSIONING_CEILING_W

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.unique_id}_charge_ceiling"

    @property
    def available(self) -> bool:
        return self.coordinator.profile.actuator_ready

    @property
    def native_value(self) -> float | None:
        normalized = (self.coordinator.data or {}).get("normalized", {})
        return normalized.get("charge_max_limit_w")

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.controller.async_set_charge_ceiling_w(value)


class ZendureDischargeCeilingNumber(ZendureLocalEntity, NumberEntity):
    """Device-side persisted max-discharge (inverter) ceiling.

    Writes `inverseMaxPower`, the officially read/write "max inverter
    output" property, replacing the app's discharge-power slider.
    """

    _attr_name = "Max ontlaadvermogen (apparaat)"
    _attr_native_unit_of_measurement = "W"
    _attr_native_step = SETPOINT_STEP_W
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = COMMISSIONING_CEILING_W

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.unique_id}_discharge_ceiling"

    @property
    def available(self) -> bool:
        return self.coordinator.profile.actuator_ready

    @property
    def native_value(self) -> float | None:
        normalized = (self.coordinator.data or {}).get("normalized", {})
        return normalized.get("inverter_max_power_w")

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.controller.async_set_discharge_ceiling_w(
            value
        )
