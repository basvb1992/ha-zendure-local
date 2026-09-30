from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import ZendureLocalClient
from .const import CONF_PROFILE, DOMAIN, PLATFORMS
from .control import ZendureCommandController
from .coordinator import ZendureLocalCoordinator
from .profiles import get_profile


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    client = ZendureLocalClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
    )
    coordinator = ZendureLocalCoordinator(
        hass,
        entry,
        client,
        get_profile(entry.options.get(CONF_PROFILE, entry.data[CONF_PROFILE])),
    )
    await coordinator.async_config_entry_first_refresh()
    coordinator.controller = ZendureCommandController(hass, entry, coordinator)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    async def _async_shutdown(_event: Event) -> None:
        await coordinator.controller.async_close()

    entry.async_on_unload(
        hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP,
            _async_shutdown,
        )
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_reload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.controller.async_close()
    if not await hass.config_entries.async_unload_platforms(
        entry,
        PLATFORMS,
    ):
        return False
    hass.data[DOMAIN].pop(entry.entry_id)
    return True
