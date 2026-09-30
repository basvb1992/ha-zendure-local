"""Static, import-free contract tests for zendure_local.

These read source/JSON directly (no Home Assistant test harness needed)
so they run fast and never depend on the real ZenSDK device or a live
Home Assistant core. They pin the safety-critical invariants that must
never regress: the controller is the only write path, control never
persists across a restart, a stop always clears both directional
limits, the dead-man timer can only be refreshed by an external
instruction, and a battery with stale/missing phase data is blocked in
both directions.
"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "zendure_local"


class ZendureLocalContractTests(unittest.TestCase):
    def test_shutdown_and_unload_both_neutralize_before_teardown(self):
        source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")

        self.assertIn("EVENT_HOMEASSISTANT_STOP", source)
        self.assertIn("hass.bus.async_listen_once", source)
        unload = source.split("async def async_unload_entry", 1)[1]
        self.assertLess(
            unload.index("await coordinator.controller.async_close()"),
            unload.index("async_unload_platforms"),
        )

    def test_manifest_registers_local_polling_and_discovery(self):
        manifest = json.loads(
            (COMPONENT / "manifest.json").read_text(encoding="utf-8")
        )

        self.assertEqual(manifest["domain"], "zendure_local")
        self.assertEqual(manifest["iot_class"], "local_polling")
        self.assertTrue(manifest["config_flow"])
        self.assertIn("_zendure._tcp.local.", manifest["zeroconf"])

    def test_config_flow_avoids_relocated_runtime_imports(self):
        # Importing ZeroconfServiceInfo from homeassistant.components
        # .zeroconf raises ImportError on modern Home Assistant cores,
        # which makes the whole handler unloadable and surfaces in the
        # UI as: Config flow could not be loaded: "Invalid handler
        # specified". The annotation-only import must stay behind
        # TYPE_CHECKING so no runtime import can ever break the flow.
        source = (COMPONENT / "config_flow.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn(
            "from homeassistant.components.zeroconf import",
            source,
        )
        self.assertIn("if TYPE_CHECKING:", source)
        self.assertIn("from __future__ import annotations", source)

    def test_coordinator_binds_to_its_config_entry(self):
        source = (COMPONENT / "coordinator.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("config_entry=entry", source)

    def test_control_is_the_only_write_path(self):
        """Every write must funnel through the guarded controller."""
        offenders = [
            path.name
            for path in COMPONENT.glob("*.py")
            if path.name not in {"client.py", "control.py"}
            and "async_write_properties" in path.read_text(
                encoding="utf-8"
            )
        ]

        self.assertEqual([], offenders)

    def test_control_starts_disabled_and_is_never_persisted(self):
        """An HA restart must return the battery to OFF_SAFE."""
        source = (COMPONENT / "control.py").read_text(encoding="utf-8")

        self.assertIn("self.control_enabled = False", source)
        self.assertNotIn("async_update_entry", source)

    def test_stop_payload_clears_both_directional_limits(self):
        source = (COMPONENT / "control.py").read_text(encoding="utf-8")
        stop = source.split("STOP_PAYLOAD = {", 1)[1].split("}", 1)[0]

        self.assertIn('"inputLimit": 0', stop)
        self.assertIn('"outputLimit": 0', stop)

    def test_directional_payloads_clear_the_opposite_limit(self):
        """A direction change must never leave a stale limit latched."""
        source = (COMPONENT / "control.py").read_text(encoding="utf-8")
        body = source.split("def _build_payload", 1)[1]

        self.assertEqual(1, body.count('"inputLimit": 0'))
        self.assertEqual(1, body.count('"outputLimit": 0'))
        self.assertIn("STOP_PAYLOAD", body)

    def test_command_session_and_keepalive_stay_bounded(self):
        source = (COMPONENT / "const.py").read_text(encoding="utf-8")
        values = {}
        for line in source.splitlines():
            for name in (
                "COMMAND_KEEPALIVE_SECONDS",
                "COMMAND_MAX_SESSION_SECONDS",
                "COMMISSIONING_CEILING_W",
                "DEFAULT_MAX_CHARGE_W",
                "DEFAULT_MAX_DISCHARGE_W",
            ):
                if line.startswith(f"{name} ="):
                    values[name] = int(line.split("=", 1)[1].strip())

        self.assertLessEqual(values["COMMAND_KEEPALIVE_SECONDS"], 120)
        self.assertLessEqual(values["COMMAND_MAX_SESSION_SECONDS"], 3600)
        self.assertLessEqual(values["COMMISSIONING_CEILING_W"], 3000)
        self.assertLessEqual(values["DEFAULT_MAX_CHARGE_W"], 500)
        self.assertLessEqual(values["DEFAULT_MAX_DISCHARGE_W"], 500)

    def test_the_keepalive_cannot_refresh_the_dead_man_deadline(self):
        """The dead-man timer must only be reset by external instructions.

        If the periodic self-refresh were to stamp last_instruction_at
        the controller would renew its own lease forever, and a battery
        left charging by an operator who walked away would never
        neutralize.
        """
        source = (COMPONENT / "control.py").read_text(encoding="utf-8")
        body = source.split("async def _handle_keepalive", 1)[1]

        self.assertNotIn("last_instruction_at", body)
        self.assertIn("last_instruction_at", source)

    def test_controller_blocks_commands_without_fresh_phase_data(self):
        """No P1 data must mean no battery power in either direction."""
        source = (COMPONENT / "control.py").read_text(encoding="utf-8")
        guard = source.split("def _guard", 1)[1].split("def _clamp", 1)[0]

        self.assertIn("phase_headroom()", guard)
        self.assertIn("BLOCK_PHASE_DATA_UNAVAILABLE", guard)

    def test_generic_profile_stays_read_only(self):
        source = (COMPONENT / "profiles.py").read_text(encoding="utf-8")
        generic = source.split("PROFILE_GENERIC_READ_ONLY:", 1)[1].split(
            "),", 1
        )[0]

        self.assertIn("actuator_ready=False", generic)

    def test_component_exposes_no_service_call_surface(self):
        """Control stays operator-driven; there is no service to invoke
        an unattended charge/discharge outside the guarded number/switch
        entities."""
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in COMPONENT.glob("*.py")
        )

        self.assertNotIn("async_register", source)
        self.assertNotIn("hass.services", source)

    def test_grid_phase_entities_and_limits_are_configurable_per_device(self):
        """The phase power/current sensors and the phase current
        limit/margin must be overridable per device via the options
        flow -- every household's P1/smart-meter integration names its
        sensors differently and may have a different commissioned
        phase current limit, so this can never be a fixed constant in
        a publicly-installable integration."""
        options_source = (COMPONENT / "config_flow.py").read_text(
            encoding="utf-8"
        )
        control_source = (COMPONENT / "control.py").read_text(
            encoding="utf-8"
        )

        for name in (
            "CONF_GRID_PHASE_POWER_ENTITY",
            "CONF_GRID_PHASE_CURRENT_ENTITY",
            "CONF_PHASE_CURRENT_LIMIT_A",
            "CONF_PHASE_OPERATING_MARGIN_A",
        ):
            self.assertIn(
                name,
                options_source,
                f"{name} missing from the options flow schema",
            )
            self.assertIn(
                name,
                control_source,
                f"{name} missing from control.py -- options flow would "
                "have no effect on the actual guard",
            )
        headroom = control_source.split("def phase_headroom", 1)[1].split(
            "def _guard", 1
        )[0]
        self.assertIn("self._grid_phase_power_entity(", headroom)
        self.assertIn("self._grid_phase_current_entity(", headroom)
        self.assertIn("self._phase_current_limit_a", headroom)
        self.assertIn("self._phase_operating_margin_a", headroom)


if __name__ == "__main__":
    unittest.main()
