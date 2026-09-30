from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import SensorEntity
from homeassistant.util import dt as dt_util

from .const import COMMAND_MAX_SESSION_SECONDS, DOMAIN
from .entity import ZendureLocalEntity


def _seconds_until_neutralize(controller) -> int | None:
    """Time left before the dead-man timer drops the command.

    None when nothing is being commanded, so the reading is only ever
    present while the answer actually matters.
    """
    if not controller.control_enabled or controller.applied_w == 0:
        return None
    reference = (
        controller.last_instruction_at or controller.session_started_at
    )
    if reference is None:
        return None
    elapsed = (dt_util.utcnow() - reference).total_seconds()
    return max(0, round(COMMAND_MAX_SESSION_SECONDS - elapsed))


def charge_power_w(signed_ac_power_w: float | None) -> float | None:
    """Always-positive charge power derived from the signed AC power reading.

    Zendure only exposes one signed AC power value (negative while
    charging), but the HEMS battery topology expects separate
    always-non-negative charge/discharge power sensors.
    """
    if signed_ac_power_w is None:
        return None
    return max(-signed_ac_power_w, 0.0)


def discharge_power_w(signed_ac_power_w: float | None) -> float | None:
    """Always-positive discharge power derived from the signed AC power reading."""
    if signed_ac_power_w is None:
        return None
    return max(signed_ac_power_w, 0.0)


@dataclass(frozen=True)
class Description:
    key: str
    name: str
    unit: str | None = None


DESCRIPTIONS = (
    Description("state_of_charge", "Laadstatus", "%"),
    Description("signed_ac_power_w", "AC-vermogen", "W"),
    Description("home_output_power_w", "Vermogen naar woning", "W"),
    Description("grid_input_power_w", "Net-laadvermogen", "W"),
    Description("input_limit_w", "Ingangslimiet", "W"),
    Description("output_limit_w", "Uitgangslimiet", "W"),
    Description("minimum_soc", "Minimum-SOC", "%"),
    Description("rssi", "Wifi-signaal", "dBm"),
)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            ZendureValueSensor(coordinator, entry, description)
            for description in DESCRIPTIONS
        ]
        + [
            ZendureStatusSensor(coordinator, entry),
            ZendureChargePowerSensor(coordinator, entry),
            ZendureDischargePowerSensor(coordinator, entry),
        ]
    )


class ZendureValueSensor(ZendureLocalEntity, SensorEntity):
    def __init__(self, coordinator, entry, description) -> None:
        super().__init__(coordinator, entry)
        self.description = description
        self._attr_unique_id = (
            f"{entry.unique_id}_{description.key}"
        )
        self._attr_name = description.name
        self._attr_native_unit_of_measurement = description.unit

    @property
    def native_value(self):
        return self.coordinator.data["normalized"].get(
            self.description.key
        )


class ZendureStatusSensor(ZendureLocalEntity, SensorEntity):
    _attr_name = "Adapterstatus"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.unique_id}_adapter_status"
        self.entity_id = (
            f"sensor.zendure_batterij_l{self.phase}_status"
        )

    @property
    def native_value(self) -> str:
        return (
            "online"
            if self.coordinator.last_update_success
            else "offline"
        )

    @property
    def extra_state_attributes(self) -> dict:
        normalized = self.coordinator.data.get("normalized", {})
        serial = self.entry.unique_id or ""
        controller = self.coordinator.controller
        headroom = controller.phase_headroom()
        return {
            "phase": f"L{self.phase}",
            "host": self.entry.data["host"],
            "masked_serial": (
                f"***{serial[-4:]}" if serial else None
            ),
            "detected_model": normalized.get("model"),
            "firmware": normalized.get("firmware"),
            "profile": self.coordinator.profile.profile_id,
            "profile_version": self.coordinator.profile.version,
            "profile_experimental": self.coordinator.profile.experimental,
            "actuator_ready": self.coordinator.profile.actuator_ready,
            "ownership": controller.ownership,
            "commissioning_state": (
                "canary" if controller.control_enabled else "not_started"
            ),
            "control_enabled": controller.control_enabled,
            "block_reason": controller.block_reason,
            "requested_setpoint_w": controller.requested_w,
            "applied_setpoint_w": controller.applied_w,
            "max_charge_w": controller.max_charge_w,
            "max_discharge_w": controller.max_discharge_w,
            "phase_data_available": headroom is not None,
            "phase_charge_headroom_w": (
                round(headroom[0]) if headroom else None
            ),
            "phase_discharge_headroom_w": (
                round(headroom[1]) if headroom else None
            ),
            "last_write_at": (
                controller.last_write_at.isoformat()
                if controller.last_write_at
                else None
            ),
            "last_write_error": controller.last_error,
            "last_instruction_at": (
                controller.last_instruction_at.isoformat()
                if controller.last_instruction_at
                else None
            ),
            "seconds_until_neutralize": _seconds_until_neutralize(
                controller
            ),
            "last_seen": (
                self.coordinator.last_seen.isoformat()
                if self.coordinator.last_seen
                else None
            ),
            "state_of_charge": normalized.get("state_of_charge"),
            "minimum_soc": normalized.get("minimum_soc"),
            "signed_ac_power_w": normalized.get("signed_ac_power_w"),
            "fault_level": normalized.get("fault_level"),
            "is_error": normalized.get("is_error"),
            "fault_warning": bool(
                (normalized.get("fault_level") or 0) > 0
                and not (normalized.get("is_error") or 0) > 0
            ),
            "raw_property_count": normalized.get("raw_property_count"),
        }


class ZendureChargePowerSensor(ZendureLocalEntity, SensorEntity):
    """Always-positive charge power, derived for HEMS battery topology entries."""

    _attr_name = "Laadvermogen"
    _attr_native_unit_of_measurement = "W"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.unique_id}_charge_power_w"
        self.entity_id = (
            f"sensor.zendure_batterij_l{self.phase}_laadvermogen"
        )

    @property
    def native_value(self) -> float | None:
        return charge_power_w(
            self.coordinator.data["normalized"].get("signed_ac_power_w")
        )


class ZendureDischargePowerSensor(ZendureLocalEntity, SensorEntity):
    """Always-positive discharge power, derived for HEMS battery topology entries."""

    _attr_name = "Ontlaadvermogen"
    _attr_native_unit_of_measurement = "W"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.unique_id}_discharge_power_w"
        self.entity_id = (
            f"sensor.zendure_batterij_l{self.phase}_ontlaadvermogen"
        )

    @property
    def native_value(self) -> float | None:
        return discharge_power_w(
            self.coordinator.data["normalized"].get("signed_ac_power_w")
        )
