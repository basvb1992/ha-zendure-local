import unittest
from dataclasses import dataclass, field
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

component_path = (
    Path(__file__).parents[1]
    / "custom_components"
    / "zendure_local"
)
package = types.ModuleType("zendure_local_test_package")
package.__path__ = [str(component_path)]
sys.modules[package.__name__] = package
aiohttp = types.ModuleType("aiohttp")
aiohttp.ClientError = type("ClientError", (Exception,), {})
aiohttp.ClientSession = object
sys.modules.setdefault("aiohttp", aiohttp)
for name in ("const", "profiles", "client", "validation"):
    spec = spec_from_file_location(
        f"{package.__name__}.{name}",
        component_path / f"{name}.py",
    )
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

profiles = sys.modules[f"{package.__name__}.profiles"]
client = sys.modules[f"{package.__name__}.client"]
validation = sys.modules[f"{package.__name__}.validation"]


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self, content_type=None):
        return self.payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.requested_url = None

    def get(self, url):
        self.requested_url = url
        return self.response


@dataclass
class FakeEntry:
    entry_id: str
    data: dict
    options: dict = field(default_factory=dict)


class ZendureLocalProfileTests(unittest.TestCase):
    def test_generic_profile_normalizes_documented_properties(self):
        profile = profiles.get_profile("generic_zensdk_read_only")

        result = profile.normalize(
            {
                "sn": "SERIAL1234",
                "electricLevel": 64,
                "outputHomePower": 700,
                "gridInputPower": 100,
                "inputLimit": 800,
                "outputLimit": 3000,
                "faultLevel": 0,
            }
        )

        self.assertEqual(result["serial_number"], "SERIAL1234")
        self.assertEqual(result["state_of_charge"], 64)
        self.assertEqual(result["signed_ac_power_w"], 600)
        self.assertEqual(result["raw_property_count"], 7)

    def test_generic_profile_uses_product_field_as_model_fallback(self):
        profile = profiles.get_profile("generic_zensdk_read_only")

        result = profile.normalize(
            {
                "sn": "ANACVCDLP294662",
                "product": "solarFlow3000MixAC+",
                "electricLevel": 95,
            }
        )

        self.assertEqual(result["model"], "solarFlow3000MixAC+")

    def test_mix_profile_is_actuator_ready_but_still_experimental(self):
        profile = profiles.get_profile(
            "zendure_solarflow_3000_mix_ac_plus"
        )

        self.assertTrue(profile.experimental)
        self.assertTrue(profile.actuator_ready)

    def test_mix_profile_exposes_device_side_power_limits(self):
        """Clamping to the device limits needs these normalized keys."""
        profile = profiles.get_profile(
            "zendure_solarflow_3000_mix_ac_plus"
        )

        result = profile.normalize(
            {"chargeMaxLimit": 3000, "inverseMaxPower": 800}
        )

        self.assertEqual(result["charge_max_limit_w"], 3000)
        self.assertEqual(result["inverter_max_power_w"], 800)

    def test_unknown_profile_falls_back_to_generic_read_only(self):
        profile = profiles.get_profile("not_a_real_profile")

        self.assertEqual(
            profile.profile_id,
            "generic_zensdk_read_only",
        )
        self.assertFalse(profile.actuator_ready)


class ZendureLocalClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_accepts_wrapped_properties_response(self):
        session = FakeSession(
            FakeResponse(
                200,
                {"properties": {"sn": "SERIAL1234", "electricLevel": 64}},
            )
        )
        api = client.ZendureLocalClient(session, "192.168.1.66")

        result = await api.async_get_properties()

        self.assertEqual(result["sn"], "SERIAL1234")
        self.assertEqual(
            session.requested_url,
            "http://192.168.1.66/properties/report",
        )

    async def test_client_merges_top_level_identity_into_properties(self):
        # Real SolarFlow 3000 Mix AC+ devices report "sn"/"product"/
        # "version"/"timestamp" as siblings of "properties", not
        # nested inside it. The client must surface them so the
        # config flow can read the serial number.
        session = FakeSession(
            FakeResponse(
                200,
                {
                    "timestamp": 1790136836,
                    "messageId": 2,
                    "sn": "ANACVCDLP294662",
                    "version": 3,
                    "product": "solarFlow3000MixAC+",
                    "properties": {
                        "electricLevel": 95,
                        "outputHomePower": 0,
                        "gridInputPower": 0,
                    },
                    "packData": [{"sn": "BEAAVCDLA294663"}],
                },
            )
        )
        api = client.ZendureLocalClient(session, "192.168.1.49")

        result = await api.async_get_properties()

        self.assertEqual(result["sn"], "ANACVCDLP294662")
        self.assertEqual(result["product"], "solarFlow3000MixAC+")
        self.assertEqual(result["version"], 3)
        self.assertEqual(result["electricLevel"], 95)

    async def test_client_does_not_overwrite_nested_identity_fields(self):
        session = FakeSession(
            FakeResponse(
                200,
                {
                    "sn": "TOP_LEVEL_SERIAL",
                    "properties": {"sn": "NESTED_SERIAL"},
                },
            )
        )
        api = client.ZendureLocalClient(session, "192.168.1.49")

        result = await api.async_get_properties()

        self.assertEqual(result["sn"], "NESTED_SERIAL")

    async def test_client_rejects_non_object_properties(self):
        session = FakeSession(
            FakeResponse(200, {"properties": ["invalid"]})
        )
        api = client.ZendureLocalClient(session, "192.168.1.66")

        with self.assertRaises(client.ZendureLocalResponseError):
            await api.async_get_properties()

    async def test_client_rejects_http_error(self):
        session = FakeSession(FakeResponse(503, {}))
        api = client.ZendureLocalClient(session, "192.168.1.66")

        with self.assertRaises(client.ZendureLocalResponseError):
            await api.async_get_properties()


class ZendureLocalValidationTests(unittest.TestCase):
    def test_phase_may_only_be_assigned_once(self):
        entries = [
            FakeEntry("battery-1", {"phase": "1"}),
            FakeEntry("battery-2", {"phase": "2"}),
        ]

        self.assertTrue(validation.phase_in_use(entries, "1"))
        self.assertFalse(validation.phase_in_use(entries, "3"))

    def test_reconfigure_excludes_current_entry(self):
        entries = [FakeEntry("battery-1", {"phase": "1"})]

        self.assertFalse(
            validation.phase_in_use(
                entries,
                "1",
                exclude_entry_id="battery-1",
            )
        )


if __name__ == "__main__":
    unittest.main()
