"""Per-phase headroom maths for the Zendure command controller.

This module is deliberately free of Home Assistant imports so the
safety maths can be unit tested directly. The controller reads the P1
measurement for the phase its battery is bound to and asks here how
much charge and discharge power still fits inside the phase budget.

Sign convention matches the rest of the HEMS: a positive current or
power imports from the grid, a negative one exports to it. Battery
power is signed the same way as the setpoint, so positive discharges
into the house and negative charges from the grid or from PV.
"""

from __future__ import annotations


def battery_neutral_current_a(
    measured_current_a: float,
    battery_power_w: float,
    voltage: float,
) -> float:
    """Remove the battery's own contribution from a phase measurement.

    The P1 meter already sees whatever the battery is doing right now,
    so clamping against the raw measurement would let the controller
    chase its own tail: a discharge lowers the measured current, which
    would then look like room for even more discharge.
    """
    if voltage <= 0:
        raise ValueError("voltage must be positive")
    return measured_current_a + battery_power_w / voltage


def phase_headroom_w(
    measured_current_a: float,
    battery_power_w: float,
    *,
    limit_a: float,
    margin_a: float,
    voltage: float,
) -> tuple[float, float]:
    """Return the (charge, discharge) headroom in watts for one phase.

    Both directions are bounded: charging pushes the phase further into
    import, discharging pushes it further into export, and the fuse
    does not care which way the current flows.
    """
    usable_a = max(0.0, float(limit_a) - float(margin_a))
    neutral_a = battery_neutral_current_a(
        measured_current_a,
        battery_power_w,
        voltage,
    )
    charge_w = max(0.0, (usable_a - neutral_a) * voltage)
    discharge_w = max(0.0, (usable_a + neutral_a) * voltage)
    return charge_w, discharge_w


def worst_case_current_a(
    phase_power_w: float | None,
    phase_current_a: float | None,
    voltage: float,
) -> float | None:
    """Combine the signed power and unsigned current readings.

    The phase power sensor is signed and therefore carries the
    direction, while the current sensor is the one that actually
    reflects what the fuse sees. Where they disagree the larger
    magnitude wins, so an unbalanced or reactive load cannot hide
    behind a flattering power reading.
    """
    if phase_power_w is None:
        return None
    if voltage <= 0:
        raise ValueError("voltage must be positive")
    implied_a = float(phase_power_w) / voltage
    if phase_current_a is None:
        return implied_a
    magnitude_a = max(abs(implied_a), abs(float(phase_current_a)))
    if implied_a < 0:
        return -magnitude_a
    return magnitude_a
