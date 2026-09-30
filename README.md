# Zendure Local

A Home Assistant custom integration for **local, LAN-only** control of Zendure
SolarFlow AC+ battery systems over the device's own ZenSDK HTTP API -- no
Zendure cloud account, no internet dependency, and no vendor rate limits.

Built for a real, live three-phase household HEMS (one battery per phase,
each independently controlled from Home Assistant), and open-sourced so
anyone else running Zendure hardware can reuse the same safety-critical
control logic instead of reinventing it.

## Why local control instead of the Zendure app/cloud?

- **No cloud round-trip.** Every command is a direct HTTP request to the
  device on your own network -- no dependency on Zendure's servers being up,
  reachable, or fast enough for a real-time energy management loop.
- **A real dead-man timer.** The ZenSDK API itself has no command lease: a
  non-zero setpoint can survive a Home Assistant restart or a LAN failure
  indefinitely. This integration adds its own -- a battery HA is actively
  controlling neutralizes itself if no fresh instruction arrives within a
  bounded window (see "Safety model" below).
- **Phase-current-aware.** Every command is clamped against your own live
  smart-meter (P1) reading for that battery's phase before it is ever sent,
  so the integration can never push a phase over its safe current limit --
  regardless of what any higher-level automation asks for.

## What this integration does *not* do

This integration only ever executes a signed power setpoint you (or your own
automation/HEMS) give it, subject to its own safety guards. It contains **no**
economic/trading logic, **no** solar-surplus detection, and **no** EV-charging
awareness of its own -- that orchestration belongs in your own automations or
a HEMS package (see [home-assistant-hems-integration](https://github.com/basvb1992/HomeAssistant)
for the author's own, more opinionated three-battery trading/solar-surplus
HEMS built on top of this integration).

## Requirements

- A Zendure SolarFlow AC+ device (developed against and verified live with a
  **SolarFlow 3000 Mix AC+**; the generic read-only profile below should work
  for any device exposing the same ZenSDK HTTP API, but only the 3000 Mix
  AC+ profile currently sends commands).
- The device reachable on your LAN with the ZenSDK local API enabled.
- A smart-meter (P1) integration already reporting live per-phase power and
  current sensors (e.g. HomeWizard Instant, DSMR reader, etc.) -- required
  for the phase-safety guard; without fresh phase data, no battery command
  is ever allowed in either direction (see "Safety model").

## Installation

### HACS (recommended)

1. In Home Assistant, open **HACS -> Integrations -> ⋮ -> Custom
   repositories**.
2. Add `https://github.com/basvb1992/ha-zendure-local` as an **Integration**.
3. Search for "Zendure Local" in HACS and install it.
4. Restart Home Assistant.

### Manual

Copy `custom_components/zendure_local` into your Home Assistant `config/custom_components/` directory and restart.

## Configuration

Add via **Settings -> Devices & services -> Add integration -> Zendure
Local**. The device is auto-discovered via zeroconf on your LAN if
reachable; otherwise enter its IP address/hostname manually.

Per device you choose:

| Field | Description |
| --- | --- |
| Host | IP address or hostname of the battery on your LAN |
| Connected phase | Which of your 3 phases (L1/L2/L3) this battery is wired to -- only one battery per phase is supported |
| Battery model profile | See "Battery model profiles" below |

After setup, open the integration's **Configure** (options) to set:

| Option | Default | Description |
| --- | --- | --- |
| Maximum charge power (W) | 500 | Commissioning ceiling for charge commands (hard-capped at 3000 W) |
| Maximum discharge power (W) | 500 | Commissioning ceiling for discharge commands (hard-capped at 3000 W) |
| P1 sensor: power on this phase | *(your own P1 integration's entity)* | The live power sensor for this battery's phase, used for the phase-safety clamp |
| P1 sensor: current on this phase | *(your own P1 integration's entity)* | The live current sensor for this battery's phase |
| Allowed phase current (A) | 22.5 | Your own commissioned/fused safe current limit for this phase |
| Safety margin on the phase current (A) | 2.5 | Extra headroom subtracted from the limit above before any command is allowed |

**Start conservative.** Keep the power caps at 300-500 W and the phase limit
comfortably below your actual fuse rating until you have verified the
integration's behavior with your own electrician/installation in mind. This
integration clamps against live phase current, but it cannot know your
household's actual fuse rating or wiring -- that is your responsibility to
configure correctly.

## Battery model profiles

| Profile | Sends commands? | Notes |
| --- | --- | --- |
| Generic ZenSDK (read-only) | No | Telemetry/sensors only, safe default for any unverified device |
| Zendure SolarFlow 3000 Mix AC+ (experimental) | Yes | The only profile currently able to charge/discharge on command |

## Entities

- `sensor.<name>_*` -- state of charge, model/firmware, raw power/limit
  telemetry, and a computed signed AC power value.
- `sensor.<name>_status` -- ownership (`OFF_SAFE` / `HA_CONTROL` / `FAULT`)
  and the current `block_reason` (why a command was refused, if any) as
  attributes.
- `switch.<name>_ha_aansturing` -- master per-battery "Home Assistant
  control" switch. Off means the device is left entirely alone (its own
  native behavior, whatever that is, is unaffected).
- `number.<name>_commando_vermogen` -- the signed power setpoint (positive =
  discharge, negative = charge) your own automation/HEMS writes to.
- `binary_sensor.<name>_*` -- command acknowledgement / mismatch indicators.
- `button.<name>_noodstop` -- emergency stop (writes zero and disables
  control immediately).

## Safety model

- **Every command is clamped against live phase data.** No fresh P1
  power/current reading for this battery's phase within the freshness
  window means zero power is allowed, in either direction, full stop.
- **Dead-man timer.** Control neutralizes itself if no external instruction
  refreshes it within a bounded window -- a supervising automation that
  writes a setpoint on every cycle keeps control indefinitely; one that
  stops running (crashed, disabled, HA restarted) loses control
  automatically, it is never a manual, remembered state.
- **Never persisted.** `control_enabled` always starts `False` after a
  Home Assistant restart. The device does not resume whatever it was doing
  before -- you must re-enable control explicitly (or let your own
  automation do so).
- **Minimum SOC / full-battery guards** independently block discharge below
  the device's own reported minimum SOC and charge at/above 100%, regardless
  of what is requested.
- **No service call surface.** There is no `zendure_local.set_power` service
  to invoke from a script; the only way to command the battery is the
  guarded `number` entity, which goes through every guard above on every
  write.

### Brand assets

This repository is not (yet) listed in Home Assistant's official
[brands repository](https://github.com/home-assistant/brands), so it shows
a generic icon and will fail HACS's own "brands" validation check. This does
**not** block installation via "Add custom repository" above -- it only
affects eventual inclusion in HACS's default store and a proper Zendure icon
in the UI. A PR to `home-assistant/brands` adding
`custom_integrations/zendure_local/{icon,logo}.png` is a welcome contribution.

## Contributing

Issues and pull requests welcome. This integration is safety-critical (it
can genuinely draw real, sustained current on your home's wiring) -- please
include the reasoning/evidence behind any change to `control.py`'s guard
logic, not just the diff.

## License

MIT -- see [LICENSE](LICENSE).
