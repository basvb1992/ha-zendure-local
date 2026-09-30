DOMAIN = "zendure_local"
PLATFORMS = [
    "sensor",
    "binary_sensor",
    "number",
    "switch",
    "button",
]

CONF_PHASE = "phase"
CONF_PROFILE = "profile"
CONF_MAX_CHARGE_W = "max_charge_w"
CONF_MAX_DISCHARGE_W = "max_discharge_w"
# Added so this integration can be published as a standalone, P1-brand-
# agnostic HACS component: every household's P1/smart-meter integration
# names its phase power/current sensors differently, and 22.5 A is this
# author's own commissioned safety margin, not a universal default. These
# let each device override the DEFAULT_* fallbacks below (see
# config_flow.py); a config entry created before this option existed
# simply keeps using those same fallbacks, so no existing install is
# affected until the owner deliberately reconfigures it.
CONF_GRID_PHASE_POWER_ENTITY = "grid_phase_power_entity"
CONF_GRID_PHASE_CURRENT_ENTITY = "grid_phase_current_entity"
CONF_PHASE_CURRENT_LIMIT_A = "phase_current_limit_a"
CONF_PHASE_OPERATING_MARGIN_A = "phase_operating_margin_a"

PROFILE_GENERIC_READ_ONLY = "generic_zensdk_read_only"
PROFILE_SOLARFLOW_3000_MIX_AC_PLUS = (
    "zendure_solarflow_3000_mix_ac_plus"
)

DEFAULT_SCAN_INTERVAL_SECONDS = 5
HTTP_TIMEOUT_SECONDS = 2

# Gate 2 of the commissioning runbook requires the canary to start at
# 300-500 W. These are the defaults for a freshly added battery; the
# operator may raise them up to the commissioning ceiling only after the
# supervised zero/charge/zero/discharge/zero tests have passed.
DEFAULT_MAX_CHARGE_W = 500
DEFAULT_MAX_DISCHARGE_W = 500
COMMISSIONING_CEILING_W = 3000
SETPOINT_STEP_W = 50

# The ZenSDK exposes no command lease, so a non-zero setpoint can
# survive a Home Assistant or LAN failure. The adapter therefore keeps
# its own dead-man timer: it refreshes an active command periodically
# and forces OFF_SAFE once no new instruction has arrived for this long.
# A supervising HEMS automation re-asserts its setpoint every optimizer
# tick and so holds control indefinitely; a human operator who walks
# away stops instructing and the battery neutralizes itself.
COMMAND_KEEPALIVE_SECONDS = 60
COMMAND_MAX_SESSION_SECONDS = 1800
COMMAND_ACK_TOLERANCE_W = 60

AC_MODE_CHARGE = 1
AC_MODE_DISCHARGE = 2

# The battery shares its phase with the house and possibly an EV
# charger, so the controller clamps against the live P1 measurement of
# its own phase. Mirrored from home_energy_accounting rather than
# imported: one custom component must not depend on another loading.
#
# These four are now only the DEFAULT/fallback values, pre-filled in the
# config flow and used as-is by any device that has never been
# reconfigured with the newer per-device CONF_GRID_PHASE_*_ENTITY /
# CONF_PHASE_CURRENT_LIMIT_A / CONF_PHASE_OPERATING_MARGIN_A options
# above -- kept exactly as this author's own commissioned values so nothing
# changes for an existing install. A different household's P1 integration
# almost certainly names its sensors differently and may have a different
# commissioned phase limit; that is exactly what those per-device options
# are for.
GRID_PHASE_CURRENT = (
    "sensor.p1_meter_current_phase_1",
    "sensor.p1_meter_current_phase_2",
    "sensor.p1_meter_current_phase_3",
)
GRID_PHASE_POWER = (
    "sensor.p1_meter_power_phase_1",
    "sensor.p1_meter_power_phase_2",
    "sensor.p1_meter_power_phase_3",
)
PHASE_CURRENT_LIMIT_A = 22.5
PHASE_OPERATING_MARGIN_A = 2.5
NOMINAL_PHASE_VOLTAGE_V = 230.0
# Switched 2026-09-28 from the old third-party WiFi DSMR reader (which
# only pushed a fresh per-phase POWER reading on value change, causing
# the 15->90s raise below that this comment used to explain) to the
# HomeWizard Instant P1 integration (model HWE-P1, platform
# homewizard_instant). Verified live: 8 samples of
# sensor.p1_meter_power_phase_1 taken 1.5s apart all showed last_updated
# advancing by ~1s even while the value itself barely changed -- a
# genuine, reliable ~1s poll cycle, unlike the old reader.
# P1_MAX_AGE_SECONDS is retightened accordingly (was 90, matching the old
# reader's up-to-60s observed reporting gaps; now 12, ~12x the new
# reader's actual cadence). A genuine P1 disconnect is still caught
# independently and immediately by the "unknown"/"unavailable" state
# check in _fresh_float()/_entity_fresh() regardless of this age bound.
P1_MAX_AGE_SECONDS = 12

OWNERSHIP_OFF_SAFE = "OFF_SAFE"
OWNERSHIP_HA_CONTROL = "HA_CONTROL"
OWNERSHIP_FAULT = "FAULT"

BLOCK_NONE = "none"
BLOCK_CONTROL_DISABLED = "control_disabled"
BLOCK_PROFILE_NOT_ACTUATOR_READY = "profile_not_actuator_ready"
BLOCK_TELEMETRY_STALE = "telemetry_stale"
BLOCK_FAULT_LATCHED = "fault_latched"
BLOCK_MINIMUM_SOC = "minimum_soc_reached"
BLOCK_BATTERY_FULL = "battery_full"
BLOCK_SESSION_EXPIRED = "session_expired"
BLOCK_WRITE_FAILED = "write_failed"
BLOCK_PHASE_DATA_UNAVAILABLE = "phase_data_unavailable"
BLOCK_PHASE_LIMIT_REACHED = "phase_limit_reached"
