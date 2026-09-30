from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .client import ZendureLocalClient, ZendureLocalError
from .const import DEFAULT_SCAN_INTERVAL_SECONDS, DOMAIN
from .profiles import BatteryModelProfile

_LOGGER = logging.getLogger(__name__)


class ZendureLocalCoordinator(DataUpdateCoordinator[dict]):
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ZendureLocalClient,
        profile: BatteryModelProfile,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            config_entry=entry,
            update_interval=timedelta(
                seconds=DEFAULT_SCAN_INTERVAL_SECONDS
            ),
        )
        self.entry = entry
        self.client = client
        self.profile = profile
        self.last_seen = None
        # Assigned during setup; the coordinator never writes by itself.
        self.controller = None

    async def _async_update_data(self) -> dict:
        try:
            properties = await self.client.async_get_properties()
        except ZendureLocalError as err:
            raise UpdateFailed(str(err)) from err
        self.last_seen = dt_util.utcnow()
        return {
            "properties": properties,
            "normalized": self.profile.normalize(properties),
        }
