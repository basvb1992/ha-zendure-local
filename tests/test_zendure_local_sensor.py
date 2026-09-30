"""Tests for the derived charge/discharge power sensors in zendure_local.

Zendure only exposes a single signed AC power value, but the HEMS battery
topology (home_energy_accounting.const.BATTERIES) expects separate,
always-non-negative charge/discharge power entities. sensor.py derives
those two values; this test exercises the pure helper functions plus a
minimal end-to-end read through the sensor classes.
"""

import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

component_path = (
    Path(__file__).parents[1] / "custom_components" / "zendure_local"
)
package = types.ModuleType("zendure_sensor_test_package")
package.__path__ = [str(component_path)]
sys.modules[package.__name__] = package


def _install_homeassistant_stubs() -> None:
    ha = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    sensor_component = types.ModuleType("homeassistant.components.sensor")
    number_component = types.ModuleType("homeassistant.components.number")
    helpers = types.ModuleType("homeassistant.helpers")
    device_registry = types.ModuleType("homeassistant.helpers.device_registry")
    entity = types.ModuleType("homeassistant.helpers.entity")
    update_coordinator = types.ModuleType(
        "homeassistant.helpers.update_coordinator"
    )
    util = types.ModuleType("homeassistant.util")
    dt_util = types.ModuleType("homeassistant.util.dt")

    class SensorEntity:
        """Minimal stand-in: real HA isn't installed in this harness."""

    class NumberEntity:
        """Minimal stand-in: real HA isn't installed in this harness."""

    class NumberMode:
        SLIDER = "slider"
        BOX = "box"

    class EntityCategory:
        CONFIG = "config"

    class DeviceInfo(dict):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

    class CoordinatorEntity:
        def __init__(self, coordinator) -> None:
            self.coordinator = coordinator

    sensor_component.SensorEntity = SensorEntity
    number_component.NumberEntity = NumberEntity
    number_component.NumberMode = NumberMode
    device_registry.DeviceInfo = DeviceInfo
    entity.EntityCategory = EntityCategory
    update_coordinator.CoordinatorEntity = CoordinatorEntity
    dt_util.utcnow = lambda: None

    components.sensor = sensor_component
    components.number = number_component
    helpers.device_registry = device_registry
    helpers.entity = entity
    helpers.update_coordinator = update_coordinator
    util.dt = dt_util
    ha.components = components
    ha.helpers = helpers
    ha.util = util

    for name, module in (
        ("homeassistant", ha),
        ("homeassistant.components", components),
        ("homeassistant.components.sensor", sensor_component),
        ("homeassistant.components.number", number_component),
        ("homeassistant.helpers", helpers),
        ("homeassistant.helpers.device_registry", device_registry),
        ("homeassistant.helpers.entity", entity),
        ("homeassistant.helpers.update_coordinator", update_coordinator),
        ("homeassistant.util", util),
        ("homeassistant.util.dt", dt_util),
    ):
        sys.modules.setdefault(name, module)


_install_homeassistant_stubs()

for _name in ("const", "entity", "sensor", "number"):
    _spec = spec_from_file_location(
        f"{package.__name__}.{_name}",
        component_path / f"{_name}.py",
    )
    _module = module_from_spec(_spec)
    sys.modules[_spec.name] = _module
    _spec.loader.exec_module(_module)

sensor_module = sys.modules[f"{package.__name__}.sensor"]
number_module = sys.modules[f"{package.__name__}.number"]


class FakeCoordinator:
    def __init__(self, signed_ac_power_w) -> None:
        self.data = {"normalized": {"signed_ac_power_w": signed_ac_power_w}}
        self.profile = types.SimpleNamespace(name="Zendure")
        self.client = types.SimpleNamespace(base_url="http://192.168.1.49")


class FakeEntry:
    def __init__(self) -> None:
        self.unique_id = "ABC123"
        self.data = {"serial_number": "ABC123", "phase": 1}
        self.options = {}


class DerivedPowerHelperTests(unittest.TestCase):
    def test_charge_power_is_positive_magnitude_of_negative_signed_power(self):
        self.assertEqual(sensor_module.charge_power_w(-1200.0), 1200.0)

    def test_charge_power_is_zero_while_discharging(self):
        self.assertEqual(sensor_module.charge_power_w(800.0), 0.0)

    def test_charge_power_is_none_when_unavailable(self):
        self.assertIsNone(sensor_module.charge_power_w(None))

    def test_discharge_power_is_signed_power_when_positive(self):
        self.assertEqual(sensor_module.discharge_power_w(800.0), 800.0)

    def test_discharge_power_is_zero_while_charging(self):
        self.assertEqual(sensor_module.discharge_power_w(-1200.0), 0.0)

    def test_discharge_power_is_none_when_unavailable(self):
        self.assertIsNone(sensor_module.discharge_power_w(None))


class DerivedPowerSensorTests(unittest.TestCase):
    def test_charge_power_sensor_entity_id_and_value(self):
        coordinator = FakeCoordinator(signed_ac_power_w=-1500.0)
        entry = FakeEntry()
        sensor = sensor_module.ZendureChargePowerSensor(coordinator, entry)
        self.assertEqual(
            sensor.entity_id, "sensor.zendure_batterij_l1_laadvermogen"
        )
        self.assertEqual(sensor.native_value, 1500.0)

    def test_discharge_power_sensor_entity_id_and_value(self):
        coordinator = FakeCoordinator(signed_ac_power_w=900.0)
        entry = FakeEntry()
        sensor = sensor_module.ZendureDischargePowerSensor(coordinator, entry)
        self.assertEqual(
            sensor.entity_id, "sensor.zendure_batterij_l1_ontlaadvermogen"
        )
        self.assertEqual(sensor.native_value, 900.0)


class SetpointSliderBoundsTests(unittest.TestCase):
    def test_slider_reflects_current_effective_import_export_limits(self):
        coordinator = FakeCoordinator(signed_ac_power_w=0.0)
        coordinator.controller = types.SimpleNamespace(
            max_charge_w=1200.0,
            max_discharge_w=900.0,
            requested_w=0.0,
        )
        slider = number_module.ZendureSetpointNumber(
            coordinator,
            FakeEntry(),
        )

        self.assertEqual(slider.native_min_value, -1200.0)
        self.assertEqual(slider.native_max_value, 900.0)


if __name__ == "__main__":
    unittest.main()
