from __future__ import annotations

import logging

from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .client import ZendureLocalError
from .const import (
    AC_MODE_CHARGE,
    AC_MODE_DISCHARGE,
    BLOCK_BATTERY_FULL,
    BLOCK_CONTROL_DISABLED,
    BLOCK_FAULT_LATCHED,
    BLOCK_MINIMUM_SOC,
    BLOCK_NONE,
    BLOCK_PHASE_DATA_UNAVAILABLE,
    BLOCK_PHASE_LIMIT_REACHED,
    BLOCK_PROFILE_NOT_ACTUATOR_READY,
    BLOCK_SESSION_EXPIRED,
    BLOCK_TELEMETRY_STALE,
    BLOCK_WRITE_FAILED,
    COMMAND_ACK_TOLERANCE_W,
    COMMAND_KEEPALIVE_SECONDS,
    COMMAND_MAX_SESSION_SECONDS,
    COMMISSIONING_CEILING_W,
    CONF_GRID_PHASE_CURRENT_ENTITY,
    CONF_GRID_PHASE_POWER_ENTITY,
    CONF_MAX_CHARGE_W,
    CONF_MAX_DISCHARGE_W,
    CONF_PHASE,
    CONF_PHASE_CURRENT_LIMIT_A,
    CONF_PHASE_OPERATING_MARGIN_A,
    DEFAULT_MAX_CHARGE_W,
    DEFAULT_MAX_DISCHARGE_W,
    GRID_PHASE_CURRENT,
    GRID_PHASE_POWER,
    NOMINAL_PHASE_VOLTAGE_V,
    OWNERSHIP_FAULT,
    OWNERSHIP_HA_CONTROL,
    OWNERSHIP_OFF_SAFE,
    P1_MAX_AGE_SECONDS,
    PHASE_CURRENT_LIMIT_A,
    PHASE_OPERATING_MARGIN_A,
)
from .phase_guard import phase_headroom_w, worst_case_current_a

_LOGGER = logging.getLogger(__name__)

# Positive setpoints discharge the battery into the house, negative
# setpoints charge it. Zero is an explicit stop that clears both limits.
STOP_PAYLOAD = {
    "smartMode": 0,
    "acMode": AC_MODE_DISCHARGE,
    "inputLimit": 0,
    "outputLimit": 0,
}


class ZendureCommandController:
    """Owns every write to one battery.

    The controller is the single writer for its device. It never makes
    an energy decision of its own: it clamps, guards and translates one
    signed setpoint into a complete ZenSDK command, then verifies the
    readback. Control is runtime-only and always starts disabled, so a
    Home Assistant restart necessarily returns the device to OFF_SAFE.
    """

    def __init__(self, hass, entry, coordinator) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.control_enabled = False
        self.requested_w = 0.0
        self.applied_w = 0.0
        self.block_reason = BLOCK_CONTROL_DISABLED
        self.last_payload: dict | None = None
        self.last_write_at = None
        self.last_error: str | None = None
        self.session_started_at = None
        self.last_instruction_at = None
        self._cancel_keepalive = None
        self._closing = False

    @property
    def max_charge_w(self) -> float:
        configured_limit = float(
            self.entry.options.get(
                CONF_MAX_CHARGE_W,
                self.entry.data.get(
                    CONF_MAX_CHARGE_W,
                    DEFAULT_MAX_CHARGE_W,
                ),
            )
        )
        return self._effective_device_limit(
            configured_limit,
            "charge_max_limit_w",
        )

    @property
    def max_discharge_w(self) -> float:
        configured_limit = float(
            self.entry.options.get(
                CONF_MAX_DISCHARGE_W,
                self.entry.data.get(
                    CONF_MAX_DISCHARGE_W,
                    DEFAULT_MAX_DISCHARGE_W,
                ),
            )
        )
        return self._effective_device_limit(
            configured_limit,
            "inverter_max_power_w",
        )

    @property
    def phase(self) -> str | None:
        raw = self.entry.options.get(
            CONF_PHASE,
            self.entry.data.get(CONF_PHASE),
        )
        return None if raw is None else str(raw)

    def _grid_phase_power_entity(self, default_index: int) -> str:
        configured = self.entry.options.get(
            CONF_GRID_PHASE_POWER_ENTITY,
            self.entry.data.get(CONF_GRID_PHASE_POWER_ENTITY),
        )
        return configured or GRID_PHASE_POWER[default_index]

    def _grid_phase_current_entity(self, default_index: int) -> str:
        configured = self.entry.options.get(
            CONF_GRID_PHASE_CURRENT_ENTITY,
            self.entry.data.get(CONF_GRID_PHASE_CURRENT_ENTITY),
        )
        return configured or GRID_PHASE_CURRENT[default_index]

    @property
    def _phase_current_limit_a(self) -> float:
        return float(
            self.entry.options.get(
                CONF_PHASE_CURRENT_LIMIT_A,
                self.entry.data.get(
                    CONF_PHASE_CURRENT_LIMIT_A, PHASE_CURRENT_LIMIT_A
                ),
            )
        )

    @property
    def _phase_operating_margin_a(self) -> float:
        return float(
            self.entry.options.get(
                CONF_PHASE_OPERATING_MARGIN_A,
                self.entry.data.get(
                    CONF_PHASE_OPERATING_MARGIN_A, PHASE_OPERATING_MARGIN_A
                ),
            )
        )

    @property
    def ownership(self) -> str:
        if self._has_fault():
            return OWNERSHIP_FAULT
        if self.control_enabled:
            return OWNERSHIP_HA_CONTROL
        return OWNERSHIP_OFF_SAFE

    @property
    def command_acknowledged(self) -> bool:
        """True when the device reports back the limits we commanded."""
        if self.last_payload is None:
            return False
        normalized = self._normalized()
        for key, field in (
            ("inputLimit", "input_limit_w"),
            ("outputLimit", "output_limit_w"),
        ):
            reported = normalized.get(field)
            if reported is None:
                return False
            if abs(float(reported) - float(self.last_payload[key])) > 1:
                return False
        return True

    @property
    def command_mismatch(self) -> bool:
        """True when measured power contradicts the active command."""
        if not self.control_enabled or self.last_payload is None:
            return False
        measured = self._normalized().get("signed_ac_power_w")
        if measured is None:
            return False
        return (
            abs(float(measured) - self.applied_w)
            > COMMAND_ACK_TOLERANCE_W
        )

    async def async_enable(self, enabled: bool) -> None:
        self.control_enabled = bool(enabled)
        if enabled:
            now = dt_util.utcnow()
            self.session_started_at = now
            self.last_instruction_at = now
        else:
            self.session_started_at = None
            self.last_instruction_at = None
            self.requested_w = 0.0
        await self.async_apply()

    async def async_set_setpoint(self, watts: float) -> None:
        self.requested_w = float(watts)
        self.last_instruction_at = dt_util.utcnow()
        await self.async_apply()

    async def async_stop(self) -> None:
        """Operator emergency stop: drop ownership and clear limits."""
        self.control_enabled = False
        self.session_started_at = None
        self.last_instruction_at = None
        self.requested_w = 0.0
        await self.async_apply(force_write=True)

    async def async_set_charge_ceiling_w(self, watts: float) -> None:
        """Write the device's own persisted max-charge power ceiling.

        This is the same value the Zendure app's charge-power slider
        edits (`chargeMaxLimit`). The ZenSDK documents it read-only, but
        community testing on real hardware shows the device accepts and
        persists a written value. Unlike the runtime setpoint -- which
        always writes with smartMode=1 to spare the flash -- this
        property is believed to be written to flash on every change, so
        it must stay a rare, operator-driven edit and is never touched
        by the HEMS optimizer.
        """
        await self._async_set_device_ceiling(
            "chargeMaxLimit",
            "charge_max_limit_w",
            watts,
        )

    async def async_set_discharge_ceiling_w(self, watts: float) -> None:
        """Write the device's own persisted max-discharge (inverter) ceiling.

        Mirrors `async_set_charge_ceiling_w` for `inverseMaxPower`, the
        officially documented read/write "max inverter output" property.
        """
        await self._async_set_device_ceiling(
            "inverseMaxPower",
            "inverter_max_power_w",
            watts,
        )

    async def _async_set_device_ceiling(
        self,
        property_name: str,
        normalized_key: str,
        watts: float,
    ) -> None:
        if self._closing:
            raise ZendureLocalError("Battery adapter is shutting down")
        if not self.coordinator.profile.actuator_ready:
            raise ZendureLocalError(
                "This profile has not been qualified for writes"
            )
        if self._has_fault():
            raise ZendureLocalError(
                "Refusing to change the device power ceiling while a "
                "fault is latched"
            )
        clamped = max(0, min(int(round(watts)), COMMISSIONING_CEILING_W))
        serial = self.entry.unique_id or self.entry.data.get(
            "serial_number", ""
        )
        try:
            await self.coordinator.client.async_write_properties(
                serial,
                {property_name: clamped},
            )
        except ZendureLocalError as err:
            self.last_error = str(err)
            _LOGGER.error(
                "Zendure device ceiling write failed for %s (%s): %s",
                serial,
                property_name,
                err,
            )
            raise
        self.last_error = None
        _LOGGER.warning(
            "Wrote Zendure device ceiling %s=%s for %s; this is "
            "believed to be a flash write, so avoid repeating it often",
            property_name,
            clamped,
            serial,
        )
        data = self.coordinator.data
        if data:
            data.get("properties", {})[property_name] = clamped
            normalized = data.get("normalized", {})
            normalized[normalized_key] = float(clamped)
        self.coordinator.async_update_listeners()

    async def async_apply(self, force_write: bool = False) -> None:
        if self._closing:
            return
        target, reason = self._guard(self.requested_w)
        payload = self._build_payload(target)
        changed = payload != self.last_payload
        if changed or force_write:
            if not await self._async_write(payload):
                self._schedule_keepalive()
                return
            self.applied_w = target
        else:
            self.applied_w = target
        self.block_reason = reason
        self._schedule_keepalive()
        self.coordinator.async_update_listeners()

    async def async_close(self) -> None:
        """Return the device to a verified zero-power baseline."""
        self._cancel_timer()
        self._closing = True
        if self.last_payload is not None and self.last_payload != STOP_PAYLOAD:
            try:
                await self._async_write(STOP_PAYLOAD, raise_on_error=True)
            except ZendureLocalError as err:
                _LOGGER.error(
                    "Could not neutralize Zendure battery %s on unload: %s",
                    self.entry.unique_id,
                    err,
                )
                return
        self.control_enabled = False
        self.requested_w = 0.0
        self.applied_w = 0.0
        self.block_reason = BLOCK_CONTROL_DISABLED
        self.session_started_at = None
        self.last_instruction_at = None

    def _normalized(self) -> dict:
        data = self.coordinator.data or {}
        return data.get("normalized", {})

    def _effective_device_limit(
        self,
        configured_limit: float,
        normalized_key: str,
    ) -> float:
        """Return the lower of the commissioning and reported device caps."""
        limit = max(0.0, configured_limit)
        device_limit = self._normalized().get(normalized_key)
        if device_limit is None:
            return limit
        try:
            return min(limit, max(0.0, float(device_limit)))
        except (TypeError, ValueError):
            return limit

    def _has_fault(self) -> bool:
        normalized = self._normalized()
        return bool((normalized.get("is_error") or 0) > 0)

    def _session_expired(self) -> bool:
        """Dead-man timer: expire on silence, not on elapsed session time.

        A supervising HEMS automation re-asserts its setpoint on every
        optimizer tick, so it keeps control indefinitely. An operator who
        walks away stops instructing, and the battery neutralizes.
        """
        reference = self.last_instruction_at or self.session_started_at
        if reference is None:
            return False
        elapsed = (dt_util.utcnow() - reference).total_seconds()
        return elapsed > COMMAND_MAX_SESSION_SECONDS

    def _phase_index(self) -> int | None:
        try:
            index = int(self.phase) - 1
        except (TypeError, ValueError):
            return None
        if 0 <= index < len(GRID_PHASE_POWER):
            return index
        return None

    def _fresh_float(self, entity_id: str) -> float | None:
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        age = (
            dt_util.utcnow()
            - getattr(state, "last_reported", state.last_updated)
        ).total_seconds()
        if age > P1_MAX_AGE_SECONDS:
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    def phase_headroom(self) -> tuple[float, float] | None:
        """Charge and discharge watts still free on this battery's phase.

        Returns None when the loading cannot be established from fresh
        P1 data. The caller treats that as a hard block: without knowing
        what else is on the phase the controller cannot promise it stays
        under the fuse, so it commands nothing at all.
        """
        index = self._phase_index()
        if index is None:
            return None
        current_a = worst_case_current_a(
            self._fresh_float(self._grid_phase_power_entity(index)),
            self._fresh_float(self._grid_phase_current_entity(index)),
            NOMINAL_PHASE_VOLTAGE_V,
        )
        if current_a is None:
            return None
        return phase_headroom_w(
            current_a,
            self.applied_w,
            limit_a=self._phase_current_limit_a,
            margin_a=self._phase_operating_margin_a,
            voltage=NOMINAL_PHASE_VOLTAGE_V,
        )

    def _guard(self, requested: float) -> tuple[float, str]:
        """Reduce a requested setpoint to what is currently safe."""
        if not self.coordinator.profile.actuator_ready:
            return 0.0, BLOCK_PROFILE_NOT_ACTUATOR_READY
        if not self.control_enabled:
            return 0.0, BLOCK_CONTROL_DISABLED
        if self._session_expired():
            return 0.0, BLOCK_SESSION_EXPIRED
        if not self.coordinator.last_update_success:
            return 0.0, BLOCK_TELEMETRY_STALE
        if self._has_fault():
            return 0.0, BLOCK_FAULT_LATCHED

        if requested == 0:
            return 0.0, BLOCK_NONE

        headroom = self.phase_headroom()
        if headroom is None:
            return 0.0, BLOCK_PHASE_DATA_UNAVAILABLE
        charge_headroom, discharge_headroom = headroom

        normalized = self._normalized()
        soc = normalized.get("state_of_charge")
        minimum_soc = normalized.get("minimum_soc")

        if requested > 0:
            if (
                soc is not None
                and minimum_soc is not None
                and soc <= minimum_soc
            ):
                return 0.0, BLOCK_MINIMUM_SOC
            allowed = self._clamp_discharge(requested, discharge_headroom)
            if allowed <= 0 and discharge_headroom <= 0:
                return 0.0, BLOCK_PHASE_LIMIT_REACHED
            return allowed, BLOCK_NONE

        if soc is not None and soc >= 100:
            return 0.0, BLOCK_BATTERY_FULL
        allowed = self._clamp_charge(-requested, charge_headroom)
        if allowed <= 0 and charge_headroom <= 0:
            return 0.0, BLOCK_PHASE_LIMIT_REACHED
        return -allowed, BLOCK_NONE

    def _clamp_discharge(
        self,
        watts: float,
        headroom_w: float | None = None,
    ) -> float:
        limit = self.max_discharge_w
        if headroom_w is not None:
            limit = min(limit, float(headroom_w))
        return max(0.0, min(watts, limit))

    def _clamp_charge(
        self,
        watts: float,
        headroom_w: float | None = None,
    ) -> float:
        limit = self.max_charge_w
        if headroom_w is not None:
            limit = min(limit, float(headroom_w))
        return max(0.0, min(watts, limit))

    def _build_payload(self, target: float) -> dict:
        if target > 0:
            return {
                "smartMode": 1,
                "acMode": AC_MODE_DISCHARGE,
                "inputLimit": 0,
                "outputLimit": int(round(target)),
            }
        if target < 0:
            return {
                "smartMode": 1,
                "acMode": AC_MODE_CHARGE,
                "outputLimit": 0,
                "inputLimit": int(round(abs(target))),
            }
        return dict(STOP_PAYLOAD)

    async def _async_write(
        self,
        payload: dict,
        raise_on_error: bool = False,
    ) -> bool:
        serial = self.entry.unique_id or self.entry.data.get(
            "serial_number", ""
        )
        try:
            await self.coordinator.client.async_write_properties(
                serial,
                payload,
            )
        except ZendureLocalError as err:
            self.last_error = str(err)
            self.block_reason = BLOCK_WRITE_FAILED
            _LOGGER.error(
                "Zendure write failed for %s: %s",
                serial,
                err,
            )
            if raise_on_error:
                raise
            return False
        self.last_error = None
        self.last_payload = dict(payload)
        self.last_write_at = dt_util.utcnow()
        return True

    def _cancel_timer(self) -> None:
        if self._cancel_keepalive is not None:
            self._cancel_keepalive()
            self._cancel_keepalive = None

    def _schedule_keepalive(self) -> None:
        self._cancel_timer()
        if self._closing or self.applied_w == 0:
            return
        self._cancel_keepalive = async_call_later(
            self.hass,
            COMMAND_KEEPALIVE_SECONDS,
            self._handle_keepalive,
        )

    async def _handle_keepalive(self, _now) -> None:
        self._cancel_keepalive = None
        # Re-running the guards is what neutralizes an active command
        # once the supervised session window closes, telemetry goes
        # stale or the battery reports a fault.
        await self.async_apply(force_write=True)
