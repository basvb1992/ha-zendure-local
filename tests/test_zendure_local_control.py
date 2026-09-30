"""Behavioural tests for the Zendure command controller.

Home Assistant is not installed in this harness, so the few HA helpers
control.py needs are stubbed. The controller itself contains the whole
safety envelope, so it is worth exercising directly rather than only
through static contract assertions.
"""

import unittest
from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

component_path = (
    Path(__file__).parents[1] / "custom_components" / "zendure_local"
)
package = types.ModuleType("zendure_control_test_package")
package.__path__ = [str(component_path)]
sys.modules[package.__name__] = package

aiohttp = types.ModuleType("aiohttp")
aiohttp.ClientError = type("ClientError", (Exception,), {})
aiohttp.ClientSession = object
sys.modules.setdefault("aiohttp", aiohttp)

_NOW = [datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)]


def _install_homeassistant_stubs() -> None:
    ha = types.ModuleType("homeassistant")
    helpers = types.ModuleType("homeassistant.helpers")
    event = types.ModuleType("homeassistant.helpers.event")
    util = types.ModuleType("homeassistant.util")
    dt_util = types.ModuleType("homeassistant.util.dt")

    def async_call_later(hass, delay, action):
        return lambda: None

    event.async_call_later = async_call_later
    dt_util.utcnow = lambda: _NOW[0]
    util.dt = dt_util
    helpers.event = event
    ha.helpers = helpers
    ha.util = util
    for name, module in (
        ("homeassistant", ha),
        ("homeassistant.helpers", helpers),
        ("homeassistant.helpers.event", event),
        ("homeassistant.util", util),
        ("homeassistant.util.dt", dt_util),
    ):
        sys.modules.setdefault(name, module)


_install_homeassistant_stubs()

for _name in ("const", "profiles", "client", "control"):
    _spec = spec_from_file_location(
        f"{package.__name__}.{_name}",
        component_path / f"{_name}.py",
    )
    _module = module_from_spec(_spec)
    sys.modules[_spec.name] = _module
    _spec.loader.exec_module(_module)

const = sys.modules[f"{package.__name__}.const"]
profiles = sys.modules[f"{package.__name__}.profiles"]
client_module = sys.modules[f"{package.__name__}.client"]
control = sys.modules[f"{package.__name__}.control"]


class FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.writes = []
        self.fail = fail

    async def async_write_properties(self, serial, properties):
        if self.fail:
            raise client_module.ZendureLocalError("write refused")
        self.writes.append((serial, dict(properties)))
        return {"ok": True}


class FakeEntry:
    def __init__(self, options=None, phase="1") -> None:
        self.unique_id = "ANACVCDLP294662"
        self.data = {
            "serial_number": self.unique_id,
            const.CONF_PHASE: phase,
        }
        self.options = options or {}


class FakeState:
    def __init__(self, state, age_seconds=0.0) -> None:
        self.state = state
        self.last_reported = _NOW[0] - timedelta(seconds=age_seconds)
        self.last_updated = self.last_reported


class FakeHass:
    """Serves the P1 phase measurements the controller clamps against."""

    def __init__(self, phase_power_w=0.0, phase_current_a=0.0, age=0.0):
        self.states = self
        self._values = {}
        for index in range(3):
            if phase_power_w is not None:
                self._values[const.GRID_PHASE_POWER[index]] = FakeState(
                    phase_power_w, age
                )
            if phase_current_a is not None:
                self._values[const.GRID_PHASE_CURRENT[index]] = FakeState(
                    phase_current_a, age
                )

    def get(self, entity_id):
        return self._values.get(entity_id)

    def set_phase(self, phase, power_w=None, current_a=None, age=0.0):
        index = int(phase) - 1
        for entity_id, value in (
            (const.GRID_PHASE_POWER[index], power_w),
            (const.GRID_PHASE_CURRENT[index], current_a),
        ):
            if value is None:
                self._values.pop(entity_id, None)
            else:
                self._values[entity_id] = FakeState(value, age)

    def refresh(self):
        """Re-stamp every state to now, as a live P1 meter would."""
        for entity_id, state in self._values.items():
            self._values[entity_id] = FakeState(state.state)


class FakeCoordinator:
    def __init__(self, normalized=None, actuator_ready=True) -> None:
        self.client = FakeClient()
        self.profile = profiles.get_profile(
            const.PROFILE_SOLARFLOW_3000_MIX_AC_PLUS
            if actuator_ready
            else const.PROFILE_GENERIC_READ_ONLY
        )
        self.last_update_success = True
        self.data = {"normalized": normalized or _healthy()}
        self.updates = 0

    def async_update_listeners(self):
        self.updates += 1


def _healthy(**overrides) -> dict:
    normalized = {
        "state_of_charge": 80.0,
        "minimum_soc": 50.0,
        "fault_level": 0.0,
        "is_error": 0.0,
        "charge_max_limit_w": 3000.0,
        "inverter_max_power_w": 800.0,
        "input_limit_w": 0.0,
        "output_limit_w": 0.0,
        "signed_ac_power_w": 0.0,
    }
    normalized.update(overrides)
    return normalized


def _controller(coordinator=None, options=None, hass=None, phase="1"):
    coordinator = coordinator or FakeCoordinator()
    entry = FakeEntry(options, phase=phase)
    return control.ZendureCommandController(
        hass or FakeHass(),
        entry,
        coordinator,
    )


class ControllerGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_control_starts_disabled(self):
        controller = _controller()

        self.assertFalse(controller.control_enabled)
        self.assertEqual(
            controller.ownership,
            const.OWNERSHIP_OFF_SAFE,
        )

    async def test_setpoint_is_refused_while_control_is_disabled(self):
        controller = _controller()

        await controller.async_set_setpoint(400)

        self.assertEqual(controller.applied_w, 0.0)
        self.assertEqual(
            controller.block_reason,
            const.BLOCK_CONTROL_DISABLED,
        )

    async def test_read_only_profile_can_never_command(self):
        coordinator = FakeCoordinator(actuator_ready=False)
        controller = _controller(coordinator)

        await controller.async_enable(True)
        await controller.async_set_setpoint(400)

        self.assertEqual(controller.applied_w, 0.0)
        self.assertEqual(
            controller.block_reason,
            const.BLOCK_PROFILE_NOT_ACTUATOR_READY,
        )

    async def test_discharge_is_blocked_at_minimum_soc(self):
        coordinator = FakeCoordinator(
            _healthy(state_of_charge=50.0, minimum_soc=50.0)
        )
        controller = _controller(coordinator)

        await controller.async_enable(True)
        await controller.async_set_setpoint(400)

        self.assertEqual(controller.applied_w, 0.0)
        self.assertEqual(
            controller.block_reason,
            const.BLOCK_MINIMUM_SOC,
        )

    async def test_charge_is_blocked_on_a_full_battery(self):
        coordinator = FakeCoordinator(_healthy(state_of_charge=100.0))
        controller = _controller(coordinator)

        await controller.async_enable(True)
        await controller.async_set_setpoint(-400)

        self.assertEqual(controller.applied_w, 0.0)
        self.assertEqual(
            controller.block_reason,
            const.BLOCK_BATTERY_FULL,
        )

    async def test_stale_telemetry_neutralizes_the_command(self):
        coordinator = FakeCoordinator()
        controller = _controller(coordinator)
        await controller.async_enable(True)
        await controller.async_set_setpoint(400)

        coordinator.last_update_success = False
        await controller.async_apply()

        self.assertEqual(controller.applied_w, 0.0)
        self.assertEqual(
            controller.block_reason,
            const.BLOCK_TELEMETRY_STALE,
        )

    async def test_fault_neutralizes_the_command(self):
        coordinator = FakeCoordinator()
        controller = _controller(coordinator)
        await controller.async_enable(True)
        await controller.async_set_setpoint(400)

        coordinator.data = {
            "normalized": _healthy(fault_level=2.0, is_error=1.0)
        }
        await controller.async_apply()

        self.assertEqual(controller.applied_w, 0.0)
        self.assertEqual(controller.ownership, const.OWNERSHIP_FAULT)

    async def test_warning_fault_level_without_error_does_not_block_canary(self):
        coordinator = FakeCoordinator()
        coordinator.data = {
            "normalized": _healthy(fault_level=2.0, is_error=0.0)
        }
        controller = _controller(coordinator)
        await controller.async_enable(True)
        await controller.async_set_setpoint(300)

        self.assertEqual(controller.ownership, const.OWNERSHIP_HA_CONTROL)
        self.assertEqual(controller.applied_w, 300.0)
        self.assertEqual(
            coordinator.client.writes[-1][1]["outputLimit"],
            300,
        )

    async def test_session_expiry_neutralizes_the_command(self):
        controller = _controller()
        await controller.async_enable(True)
        await controller.async_set_setpoint(400)

        _NOW[0] += timedelta(
            seconds=const.COMMAND_MAX_SESSION_SECONDS + 1
        )
        try:
            await controller.async_apply()
        finally:
            _NOW[0] -= timedelta(
                seconds=const.COMMAND_MAX_SESSION_SECONDS + 1
            )

        self.assertEqual(controller.applied_w, 0.0)
        self.assertEqual(
            controller.block_reason,
            const.BLOCK_SESSION_EXPIRED,
        )

    async def test_reasserting_a_setpoint_keeps_control_alive(self):
        """A supervising automation holds control by re-instructing."""
        hass = FakeHass()
        controller = _controller(hass=hass)
        await controller.async_enable(True)
        step = timedelta(
            seconds=const.COMMAND_MAX_SESSION_SECONDS - 1
        )
        total = timedelta()
        try:
            for _ in range(3):
                await controller.async_set_setpoint(400)
                _NOW[0] += step
                total += step
                hass.refresh()
            await controller.async_set_setpoint(400)
        finally:
            _NOW[0] -= total
            hass.refresh()

        self.assertEqual(controller.applied_w, 400)
        self.assertEqual(controller.block_reason, const.BLOCK_NONE)

    async def test_the_keepalive_does_not_extend_the_deadline(self):
        """Self-refresh must not be able to hold control on its own."""
        hass = FakeHass()
        controller = _controller(hass=hass)
        await controller.async_enable(True)
        await controller.async_set_setpoint(400)

        step = timedelta(seconds=const.COMMAND_KEEPALIVE_SECONDS)
        ticks = (
            const.COMMAND_MAX_SESSION_SECONDS
            // const.COMMAND_KEEPALIVE_SECONDS
        ) + 1
        try:
            for _ in range(ticks):
                _NOW[0] += step
                hass.refresh()
                await controller._handle_keepalive(None)
        finally:
            _NOW[0] -= step * ticks
            hass.refresh()

        self.assertEqual(controller.applied_w, 0.0)
        self.assertEqual(
            controller.block_reason,
            const.BLOCK_SESSION_EXPIRED,
        )

    async def test_stopping_clears_the_dead_man_deadline(self):
        controller = _controller()
        await controller.async_enable(True)
        await controller.async_set_setpoint(400)
        await controller.async_stop()

        self.assertIsNone(controller.last_instruction_at)
        self.assertIsNone(controller.session_started_at)
        self.assertEqual(controller.applied_w, 0.0)


class ControllerClampTests(unittest.IsolatedAsyncioTestCase):
    async def test_discharge_is_clamped_to_the_configured_cap(self):
        controller = _controller(
            options={const.CONF_MAX_DISCHARGE_W: 400}
        )

        await controller.async_enable(True)
        await controller.async_set_setpoint(2500)

        self.assertEqual(controller.applied_w, 400)

    async def test_discharge_is_clamped_to_the_device_inverter_limit(self):
        controller = _controller(
            options={const.CONF_MAX_DISCHARGE_W: 2000}
        )

        await controller.async_enable(True)
        await controller.async_set_setpoint(2000)

        self.assertEqual(controller.applied_w, 800)

    async def test_charge_is_clamped_to_the_configured_cap(self):
        controller = _controller(options={const.CONF_MAX_CHARGE_W: 300})

        await controller.async_enable(True)
        await controller.async_set_setpoint(-2500)

        self.assertEqual(controller.applied_w, -300)

    async def test_default_caps_stay_within_the_canary_envelope(self):
        controller = _controller()

        self.assertLessEqual(controller.max_charge_w, 500)
        self.assertLessEqual(controller.max_discharge_w, 500)

    async def test_effective_limits_include_reported_device_ceilings(self):
        controller = _controller(
            options={
                const.CONF_MAX_CHARGE_W: 3000,
                const.CONF_MAX_DISCHARGE_W: 3000,
            }
        )

        self.assertEqual(controller.max_charge_w, 3000)
        self.assertEqual(controller.max_discharge_w, 800)

    async def test_zero_device_ceiling_blocks_the_matching_direction(self):
        coordinator = FakeCoordinator(
            _healthy(
                charge_max_limit_w=0.0,
                inverter_max_power_w=0.0,
            )
        )
        controller = _controller(
            coordinator,
            options={
                const.CONF_MAX_CHARGE_W: 3000,
                const.CONF_MAX_DISCHARGE_W: 3000,
            },
        )

        await controller.async_enable(True)
        await controller.async_set_setpoint(-500)
        self.assertEqual(controller.applied_w, 0.0)

        await controller.async_set_setpoint(500)
        self.assertEqual(controller.applied_w, 0.0)


class ControllerPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_discharge_payload_clears_the_input_limit(self):
        coordinator = FakeCoordinator()
        controller = _controller(coordinator)

        await controller.async_enable(True)
        await controller.async_set_setpoint(300)

        _, payload = coordinator.client.writes[-1]
        self.assertEqual(payload["acMode"], const.AC_MODE_DISCHARGE)
        self.assertEqual(payload["outputLimit"], 300)
        self.assertEqual(payload["inputLimit"], 0)

    async def test_charge_payload_clears_the_output_limit(self):
        coordinator = FakeCoordinator()
        controller = _controller(coordinator)

        await controller.async_enable(True)
        await controller.async_set_setpoint(-300)

        _, payload = coordinator.client.writes[-1]
        self.assertEqual(payload["acMode"], const.AC_MODE_CHARGE)
        self.assertEqual(payload["inputLimit"], 300)
        self.assertEqual(payload["outputLimit"], 0)

    async def test_direction_reversal_never_latches_a_stale_limit(self):
        coordinator = FakeCoordinator()
        controller = _controller(coordinator)

        await controller.async_enable(True)
        await controller.async_set_setpoint(300)
        await controller.async_set_setpoint(-300)

        _, payload = coordinator.client.writes[-1]
        self.assertEqual(payload["outputLimit"], 0)

    async def test_emergency_stop_clears_both_limits_and_ownership(self):
        coordinator = FakeCoordinator()
        controller = _controller(coordinator)
        await controller.async_enable(True)
        await controller.async_set_setpoint(300)

        await controller.async_stop()

        _, payload = coordinator.client.writes[-1]
        self.assertEqual(payload["inputLimit"], 0)
        self.assertEqual(payload["outputLimit"], 0)
        self.assertFalse(controller.control_enabled)
        self.assertEqual(
            controller.ownership,
            const.OWNERSHIP_OFF_SAFE,
        )

    async def test_disabling_control_writes_the_stop_payload(self):
        coordinator = FakeCoordinator()
        controller = _controller(coordinator)
        await controller.async_enable(True)
        await controller.async_set_setpoint(300)

        await controller.async_enable(False)

        self.assertEqual(
            coordinator.client.writes[-1][1],
            control.STOP_PAYLOAD,
        )

    async def test_unload_returns_the_device_to_zero(self):
        coordinator = FakeCoordinator()
        controller = _controller(coordinator)
        await controller.async_enable(True)
        await controller.async_set_setpoint(300)

        await controller.async_close()

        self.assertEqual(
            coordinator.client.writes[-1][1],
            control.STOP_PAYLOAD,
        )
        self.assertFalse(controller.control_enabled)
        self.assertEqual(controller.requested_w, 0.0)
        self.assertEqual(controller.applied_w, 0.0)
        self.assertEqual(
            controller.block_reason,
            const.BLOCK_CONTROL_DISABLED,
        )

    async def test_repeated_close_only_neutralizes_once(self):
        coordinator = FakeCoordinator()
        controller = _controller(coordinator)
        await controller.async_enable(True)
        await controller.async_set_setpoint(300)

        await controller.async_close()
        writes_after_first_close = len(coordinator.client.writes)
        await controller.async_close()

        self.assertEqual(
            len(coordinator.client.writes),
            writes_after_first_close,
        )

    async def test_write_failure_does_not_claim_an_applied_setpoint(self):
        coordinator = FakeCoordinator()
        coordinator.client.fail = True
        controller = _controller(coordinator)

        await controller.async_enable(True)
        await controller.async_set_setpoint(300)

        self.assertEqual(controller.applied_w, 0.0)
        self.assertEqual(
            controller.block_reason,
            const.BLOCK_WRITE_FAILED,
        )


class ControllerReadbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_acknowledged_requires_matching_readback(self):
        coordinator = FakeCoordinator()
        controller = _controller(coordinator)
        await controller.async_enable(True)
        await controller.async_set_setpoint(300)

        self.assertFalse(controller.command_acknowledged)

        coordinator.data = {
            "normalized": _healthy(
                output_limit_w=300.0,
                signed_ac_power_w=300.0,
            )
        }

        self.assertTrue(controller.command_acknowledged)

    async def test_mismatch_flags_a_battery_ignoring_the_command(self):
        coordinator = FakeCoordinator()
        controller = _controller(coordinator)
        await controller.async_enable(True)
        await controller.async_set_setpoint(300)

        coordinator.data = {
            "normalized": _healthy(
                output_limit_w=300.0,
                signed_ac_power_w=0.0,
            )
        }

        self.assertTrue(controller.command_mismatch)

    async def test_no_mismatch_while_control_is_disabled(self):
        coordinator = FakeCoordinator()
        controller = _controller(coordinator)

        self.assertFalse(controller.command_mismatch)


class ControllerPhaseSafetyTests(unittest.IsolatedAsyncioTestCase):
    """The phase fuse is shared with the house and the EV chargers.

    22.5 A minus a 2.5 A margin leaves 20 A, so at 230 V a phase can
    carry 4600 W in either direction before the controller has to give
    way. These tests pin the arithmetic and, more importantly, the rule
    that no P1 data means no command at all.
    """

    async def test_missing_p1_data_blocks_discharge(self):
        controller = _controller(
            hass=FakeHass(phase_power_w=None, phase_current_a=None)
        )

        await controller.async_enable(True)
        await controller.async_set_setpoint(400)

        self.assertEqual(controller.applied_w, 0.0)
        self.assertEqual(
            controller.block_reason,
            const.BLOCK_PHASE_DATA_UNAVAILABLE,
        )

    async def test_missing_p1_data_blocks_charge(self):
        controller = _controller(
            hass=FakeHass(phase_power_w=None, phase_current_a=None)
        )

        await controller.async_enable(True)
        await controller.async_set_setpoint(-400)

        self.assertEqual(controller.applied_w, 0.0)
        self.assertEqual(
            controller.block_reason,
            const.BLOCK_PHASE_DATA_UNAVAILABLE,
        )

    async def test_stale_p1_data_is_treated_as_missing(self):
        controller = _controller(
            hass=FakeHass(
                phase_power_w=0.0,
                phase_current_a=0.0,
                age=const.P1_MAX_AGE_SECONDS + 5,
            )
        )

        await controller.async_enable(True)
        await controller.async_set_setpoint(400)

        self.assertEqual(controller.applied_w, 0.0)
        self.assertEqual(
            controller.block_reason,
            const.BLOCK_PHASE_DATA_UNAVAILABLE,
        )

    async def test_discharge_is_clamped_by_remaining_export_headroom(self):
        # Already exporting 4000 W, so only 600 W of the 4600 W export
        # budget is left on this phase.
        hass = FakeHass(phase_power_w=-4000.0, phase_current_a=0.0)
        controller = _controller(
            options={const.CONF_MAX_DISCHARGE_W: 3000},
            hass=hass,
        )

        await controller.async_enable(True)
        await controller.async_set_setpoint(3000)

        self.assertAlmostEqual(controller.applied_w, 600.0, places=6)

    async def test_charge_is_clamped_by_remaining_import_headroom(self):
        hass = FakeHass(phase_power_w=4200.0, phase_current_a=0.0)
        controller = _controller(
            options={const.CONF_MAX_CHARGE_W: 3000},
            hass=hass,
        )

        await controller.async_enable(True)
        await controller.async_set_setpoint(-3000)

        self.assertAlmostEqual(controller.applied_w, -400.0, places=6)

    async def test_saturated_phase_blocks_the_command(self):
        hass = FakeHass(phase_power_w=5000.0, phase_current_a=0.0)
        controller = _controller(
            options={const.CONF_MAX_CHARGE_W: 3000},
            hass=hass,
        )

        await controller.async_enable(True)
        await controller.async_set_setpoint(-3000)

        self.assertEqual(controller.applied_w, 0.0)
        self.assertEqual(
            controller.block_reason,
            const.BLOCK_PHASE_LIMIT_REACHED,
        )

    async def test_the_battery_does_not_clamp_against_itself(self):
        # The meter already contains the battery's own 600 W discharge,
        # so a naive clamp would read zero headroom and cut the command
        # it had just issued.
        hass = FakeHass(phase_power_w=-4600.0, phase_current_a=0.0)
        controller = _controller(hass=hass)
        controller.applied_w = 600.0

        _charge, discharge = controller.phase_headroom()

        self.assertAlmostEqual(discharge, 600.0, places=6)

    async def test_current_reading_overrides_a_flattering_power_reading(self):
        hass = FakeHass(phase_power_w=0.0, phase_current_a=19.0)
        controller = _controller(hass=hass)

        charge, _discharge = controller.phase_headroom()

        self.assertAlmostEqual(charge, 230.0, places=6)

    async def test_the_configured_phase_selects_the_measurement(self):
        hass = FakeHass(phase_power_w=0.0, phase_current_a=0.0)
        hass.set_phase(2, power_w=4600.0, current_a=20.0)
        controller = _controller(hass=hass, phase="2")

        charge, _discharge = controller.phase_headroom()

        self.assertEqual(charge, 0.0)

    async def test_other_phases_do_not_constrain_this_battery(self):
        hass = FakeHass(phase_power_w=0.0, phase_current_a=0.0)
        hass.set_phase(3, power_w=4600.0, current_a=20.0)
        controller = _controller(hass=hass, phase="1")

        charge, _discharge = controller.phase_headroom()

        self.assertAlmostEqual(charge, 4600.0, places=6)

    async def test_configured_grid_phase_entities_override_the_defaults(self):
        """Added 2026-09-30: publishing this as a standalone, P1-brand-
        agnostic integration means the phase power/current entity ids
        must be overridable per device, not fixed to this author's own
        HomeWizard Instant entity names."""
        hass = FakeHass(phase_power_w=0.0, phase_current_a=0.0)
        hass.states._values["sensor.my_own_p1_power_l1"] = FakeState(4600.0)
        hass.states._values["sensor.my_own_p1_current_l1"] = FakeState(20.0)
        controller = _controller(
            hass=hass,
            phase="1",
            options={
                const.CONF_GRID_PHASE_POWER_ENTITY: "sensor.my_own_p1_power_l1",
                const.CONF_GRID_PHASE_CURRENT_ENTITY: "sensor.my_own_p1_current_l1",
            },
        )

        charge, _discharge = controller.phase_headroom()

        self.assertEqual(charge, 0.0)

    async def test_unconfigured_grid_phase_entities_keep_using_the_defaults(self):
        """An entry created before this option existed has no override in
        either `options` or `data` -- it must keep reading the author's
        own default entity for its phase (fully loaded here, so no
        headroom is left -- proving the default entity, not a stale/
        missing one, is actually being read)."""
        hass = FakeHass(phase_power_w=0.0, phase_current_a=0.0)
        hass.set_phase(1, power_w=4600.0, current_a=20.0)
        controller = _controller(hass=hass, phase="1")

        charge, _discharge = controller.phase_headroom()

        self.assertAlmostEqual(charge, 0.0, places=6)

    async def test_configured_phase_current_limit_overrides_the_default(self):
        hass = FakeHass(phase_power_w=0.0, phase_current_a=0.0)
        controller = _controller(
            hass=hass,
            options={const.CONF_PHASE_CURRENT_LIMIT_A: 16.0},
        )

        charge, _discharge = controller.phase_headroom()

        # (16.0 - 2.5 margin) * 230V, versus the default 22.5 A ceiling.
        self.assertAlmostEqual(charge, 13.5 * 230.0, places=6)

    async def test_configured_phase_operating_margin_overrides_the_default(self):
        hass = FakeHass(phase_power_w=0.0, phase_current_a=0.0)
        controller = _controller(
            hass=hass,
            options={const.CONF_PHASE_OPERATING_MARGIN_A: 0.0},
        )

        charge, _discharge = controller.phase_headroom()

        self.assertAlmostEqual(charge, 22.5 * 230.0, places=6)

    async def test_unconfigured_phase_limit_and_margin_keep_using_the_defaults(self):
        hass = FakeHass(phase_power_w=0.0, phase_current_a=0.0)
        controller = _controller(hass=hass)

        charge, _discharge = controller.phase_headroom()

        self.assertAlmostEqual(charge, 20.0 * 230.0, places=6)


class ControllerDeviceCeilingTests(unittest.IsolatedAsyncioTestCase):
    """Writing chargeMaxLimit/inverseMaxPower replaces the Zendure app.

    These ceilings are config-style values, not runtime power, so they
    must be writable regardless of control_enabled/session state, but
    still refuse to touch a battery that is unqualified or faulted.
    """

    async def test_charge_ceiling_write_reaches_the_client(self):
        coordinator = FakeCoordinator()
        controller = _controller(coordinator)

        await controller.async_set_charge_ceiling_w(1200)

        serial, payload = coordinator.client.writes[-1]
        self.assertEqual(serial, "ANACVCDLP294662")
        self.assertEqual(payload, {"chargeMaxLimit": 1200})

    async def test_ceiling_write_updates_the_effective_limit_immediately(self):
        coordinator = FakeCoordinator()
        controller = _controller(
            coordinator,
            options={const.CONF_MAX_CHARGE_W: 3000},
        )

        await controller.async_set_charge_ceiling_w(1200)

        self.assertEqual(
            coordinator.data["normalized"]["charge_max_limit_w"],
            1200.0,
        )
        self.assertEqual(controller.max_charge_w, 1200.0)
        self.assertEqual(coordinator.updates, 1)

    async def test_discharge_ceiling_write_reaches_the_client(self):
        coordinator = FakeCoordinator()
        controller = _controller(coordinator)

        await controller.async_set_discharge_ceiling_w(900)

        serial, payload = coordinator.client.writes[-1]
        self.assertEqual(serial, "ANACVCDLP294662")
        self.assertEqual(payload, {"inverseMaxPower": 900})

    async def test_ceiling_write_does_not_require_control_enabled(self):
        """A config-style ceiling is independent of runtime ownership."""
        coordinator = FakeCoordinator()
        controller = _controller(coordinator)

        await controller.async_set_charge_ceiling_w(1000)

        self.assertFalse(controller.control_enabled)
        self.assertEqual(
            coordinator.client.writes[-1][1],
            {"chargeMaxLimit": 1000},
        )

    async def test_ceiling_write_is_clamped_to_the_commissioning_ceiling(
        self,
    ):
        coordinator = FakeCoordinator()
        controller = _controller(coordinator)

        await controller.async_set_charge_ceiling_w(50000)

        self.assertEqual(
            coordinator.client.writes[-1][1],
            {"chargeMaxLimit": const.COMMISSIONING_CEILING_W},
        )

    async def test_ceiling_write_is_refused_while_faulted(self):
        coordinator = FakeCoordinator(
            _healthy(fault_level=2.0, is_error=1.0)
        )
        controller = _controller(coordinator)

        with self.assertRaises(client_module.ZendureLocalError):
            await controller.async_set_discharge_ceiling_w(900)

        self.assertEqual(coordinator.client.writes, [])

    async def test_ceiling_write_is_refused_for_read_only_profiles(self):
        coordinator = FakeCoordinator(actuator_ready=False)
        controller = _controller(coordinator)

        with self.assertRaises(client_module.ZendureLocalError):
            await controller.async_set_charge_ceiling_w(900)

        self.assertEqual(coordinator.client.writes, [])

    async def test_ceiling_write_failure_surfaces_the_client_error(self):
        coordinator = FakeCoordinator()
        coordinator.client.fail = True
        controller = _controller(coordinator)

        with self.assertRaises(client_module.ZendureLocalError):
            await controller.async_set_charge_ceiling_w(900)

        self.assertIn("write refused", controller.last_error)

    async def test_ceiling_write_never_touches_the_setpoint_payload(self):
        """The ceiling write must not corrupt the runtime ack tracking."""
        coordinator = FakeCoordinator()
        controller = _controller(coordinator)
        await controller.async_enable(True)
        await controller.async_set_setpoint(300)
        setpoint_payload = controller.last_payload

        await controller.async_set_charge_ceiling_w(1200)

        self.assertEqual(controller.last_payload, setpoint_payload)


if __name__ == "__main__":
    unittest.main()
