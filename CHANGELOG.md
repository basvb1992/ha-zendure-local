# Changelog

All notable changes to this project are documented in this file.

## 0.2.0 - 2026-09-30

### Added
- Per-device options to configure the P1 grid phase power/current sensor
  entities and the allowed phase current limit/operating margin, instead of
  fixed constants tied to one household's own smart-meter integration and
  commissioned safety margin. Existing devices keep behaving exactly as
  before until deliberately reconfigured.

This is the first public release, extracted from a private, live
three-phase household HEMS repository.

## 0.1.2 and earlier

Pre-extraction history (Zendure Local Home Assistant control switch, command
number, phase-safety guard, dead-man timer, minimum-SOC/full-battery guards,
zeroconf discovery, battery model profiles) developed and commissioned live
against a real SolarFlow 3000 Mix AC+ three-battery installation.
