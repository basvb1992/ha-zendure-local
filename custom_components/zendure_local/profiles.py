from __future__ import annotations

from dataclasses import dataclass

from .const import (
    PROFILE_GENERIC_READ_ONLY,
    PROFILE_SOLARFLOW_3000_MIX_AC_PLUS,
)


@dataclass(frozen=True)
class BatteryModelProfile:
    profile_id: str
    version: int
    name: str
    actuator_ready: bool
    experimental: bool

    def normalize(self, properties: dict) -> dict:
        output_home = _number(properties.get("outputHomePower"))
        grid_input = _number(properties.get("gridInputPower"))
        signed_ac_power = None
        if output_home is not None and grid_input is not None:
            signed_ac_power = output_home - grid_input
        return {
            "serial_number": _text(properties.get("sn")),
            "model": _text(
                properties.get("productName")
                or properties.get("productModel")
                or properties.get("deviceModel")
                or properties.get("product")
            ),
            "firmware": _text(
                properties.get("firmwareVersion")
                or properties.get("softVersion")
            ),
            "state_of_charge": _number(
                properties.get("electricLevel")
            ),
            "home_output_power_w": output_home,
            "grid_input_power_w": grid_input,
            "signed_ac_power_w": signed_ac_power,
            "input_limit_w": _number(properties.get("inputLimit")),
            "output_limit_w": _number(properties.get("outputLimit")),
            "ac_mode": _number(properties.get("acMode")),
            "minimum_soc": _number(properties.get("minSoc")),
            "charge_max_limit_w": _number(
                properties.get("chargeMaxLimit")
            ),
            "inverter_max_power_w": _number(
                properties.get("inverseMaxPower")
            ),
            "fault_level": _number(properties.get("faultLevel")),
            "is_error": _number(properties.get("is_error")),
            "rssi": _number(properties.get("rssi")),
            "raw_property_count": len(properties),
        }


def _number(value) -> float | None:
    try:
        parsed = float(value)
        return parsed if parsed == parsed else None
    except (TypeError, ValueError):
        return None


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


PROFILES = {
    PROFILE_GENERIC_READ_ONLY: BatteryModelProfile(
        profile_id=PROFILE_GENERIC_READ_ONLY,
        version=1,
        name="Generic ZenSDK (read-only)",
        actuator_ready=False,
        experimental=False,
    ),
    PROFILE_SOLARFLOW_3000_MIX_AC_PLUS: BatteryModelProfile(
        profile_id=PROFILE_SOLARFLOW_3000_MIX_AC_PLUS,
        version=2,
        name="Zendure SolarFlow 3000 Mix AC+ (experimental)",
        actuator_ready=True,
        experimental=True,
    ),
}


def get_profile(profile_id: str) -> BatteryModelProfile:
    return PROFILES.get(
        profile_id,
        PROFILES[PROFILE_GENERIC_READ_ONLY],
    )
