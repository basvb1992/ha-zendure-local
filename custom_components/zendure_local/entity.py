from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_PHASE, DOMAIN


class ZendureLocalEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        serial = entry.unique_id or entry.data["serial_number"]
        phase = entry.options.get(CONF_PHASE, entry.data[CONF_PHASE])
        normalized = coordinator.data.get("normalized", {})
        self.phase = str(phase)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            manufacturer="Zendure",
            model=normalized.get("model") or coordinator.profile.name,
            name=f"Zendure batterij L{self.phase}",
            serial_number=serial,
            sw_version=normalized.get("firmware"),
            configuration_url=coordinator.client.base_url,
        )
