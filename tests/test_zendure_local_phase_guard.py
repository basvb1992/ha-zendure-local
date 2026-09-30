"""Unit tests for the Zendure per-phase headroom maths.

phase_guard.py has no Home Assistant imports, so it can be loaded and
exercised directly. The arithmetic here decides how much power may be
pushed onto a shared phase, which makes it worth testing on its own
rather than only through the controller.
"""

import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
COMPONENT = REPO_ROOT / "custom_components" / "zendure_local"

_spec = spec_from_file_location(
    "zendure_phase_guard",
    COMPONENT / "phase_guard.py",
)
phase_guard = module_from_spec(_spec)
_spec.loader.exec_module(phase_guard)

LIMIT_A = 22.5
MARGIN_A = 2.5
VOLTAGE = 230.0
# 22.5 A minus the 2.5 A operating margin, at 230 V.
USABLE_W = (LIMIT_A - MARGIN_A) * VOLTAGE


def _headroom(current_a, battery_w=0.0):
    return phase_guard.phase_headroom_w(
        current_a,
        battery_w,
        limit_a=LIMIT_A,
        margin_a=MARGIN_A,
        voltage=VOLTAGE,
    )


class PhaseHeadroomTests(unittest.TestCase):
    def test_idle_phase_offers_the_full_budget_both_ways(self):
        charge, discharge = _headroom(0.0)

        self.assertAlmostEqual(charge, USABLE_W, places=6)
        self.assertAlmostEqual(discharge, USABLE_W, places=6)

    def test_import_consumes_charge_headroom(self):
        charge, _discharge = _headroom(10.0)

        self.assertAlmostEqual(charge, (20.0 - 10.0) * VOLTAGE, places=6)

    def test_import_creates_discharge_headroom(self):
        _charge, discharge = _headroom(10.0)

        self.assertAlmostEqual(discharge, (20.0 + 10.0) * VOLTAGE, places=6)

    def test_export_is_bounded_too(self):
        # The fuse does not care which way the current flows, so a
        # heavily exporting phase must not accept more discharge.
        _charge, discharge = _headroom(-20.0)

        self.assertAlmostEqual(discharge, 0.0, places=6)

    def test_headroom_never_goes_negative(self):
        charge, discharge = _headroom(40.0)

        self.assertEqual(charge, 0.0)
        self.assertGreater(discharge, 0.0)

    def test_battery_contribution_is_removed_before_clamping(self):
        # Meter reads -20 A only because the battery is discharging
        # 4600 W; neutrally the phase sits at 0 A and still has its
        # full budget.
        charge, discharge = _headroom(-20.0, battery_w=4600.0)

        self.assertAlmostEqual(charge, USABLE_W, places=6)
        self.assertAlmostEqual(discharge, USABLE_W, places=6)

    def test_charging_battery_is_removed_before_clamping(self):
        charge, _discharge = _headroom(20.0, battery_w=-4600.0)

        self.assertAlmostEqual(charge, USABLE_W, places=6)

    def test_zero_voltage_is_rejected(self):
        with self.assertRaises(ValueError):
            _headroom_zero_voltage()


def _headroom_zero_voltage():
    return phase_guard.phase_headroom_w(
        0.0,
        0.0,
        limit_a=LIMIT_A,
        margin_a=MARGIN_A,
        voltage=0.0,
    )


class WorstCaseCurrentTests(unittest.TestCase):
    def test_missing_power_yields_no_reading(self):
        self.assertIsNone(
            phase_guard.worst_case_current_a(None, 12.0, VOLTAGE)
        )

    def test_power_alone_is_sufficient(self):
        self.assertAlmostEqual(
            phase_guard.worst_case_current_a(2300.0, None, VOLTAGE),
            10.0,
            places=6,
        )

    def test_larger_current_reading_wins(self):
        self.assertAlmostEqual(
            phase_guard.worst_case_current_a(2300.0, 14.0, VOLTAGE),
            14.0,
            places=6,
        )

    def test_direction_comes_from_the_signed_power(self):
        self.assertAlmostEqual(
            phase_guard.worst_case_current_a(-2300.0, 14.0, VOLTAGE),
            -14.0,
            places=6,
        )

    def test_smaller_current_reading_is_ignored(self):
        self.assertAlmostEqual(
            phase_guard.worst_case_current_a(2300.0, 3.0, VOLTAGE),
            10.0,
            places=6,
        )


if __name__ == "__main__":
    unittest.main()
