from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

if TYPE_CHECKING:
    # Imported for typing only. The runtime location of this class has
    # moved between Home Assistant releases, and a module-level import
    # would make the whole config flow unloadable ("Invalid handler
    # specified") on versions where the old path no longer exists.
    from homeassistant.helpers.service_info.zeroconf import (
        ZeroconfServiceInfo,
    )

from .client import ZendureLocalClient, ZendureLocalError
from .const import (
    COMMISSIONING_CEILING_W,
    CONF_GRID_PHASE_CURRENT_ENTITY,
    CONF_GRID_PHASE_POWER_ENTITY,
    CONF_MAX_CHARGE_W,
    CONF_MAX_DISCHARGE_W,
    CONF_PHASE,
    CONF_PHASE_CURRENT_LIMIT_A,
    CONF_PHASE_OPERATING_MARGIN_A,
    CONF_PROFILE,
    DEFAULT_MAX_CHARGE_W,
    DEFAULT_MAX_DISCHARGE_W,
    DOMAIN,
    GRID_PHASE_CURRENT,
    GRID_PHASE_POWER,
    PHASE_CURRENT_LIMIT_A,
    PHASE_OPERATING_MARGIN_A,
    PROFILE_GENERIC_READ_ONLY,
    PROFILE_SOLARFLOW_3000_MIX_AC_PLUS,
)
from .profiles import PROFILES
from .validation import phase_in_use


def _phase_selector() -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value="1", label="L1"),
                SelectOptionDict(value="2", label="L2"),
                SelectOptionDict(value="3", label="L3"),
            ]
        )
    )


def _profile_selector() -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(
                    value=profile_id,
                    label=profile.name,
                )
                for profile_id, profile in PROFILES.items()
            ]
        )
    )


def _sensor_entity_selector() -> EntitySelector:
    return EntitySelector(EntitySelectorConfig(domain="sensor"))


def _default_phase_index(entry_phase) -> int:
    """Best-effort 0-based index for pre-filling the P1 sensor pickers.

    Falls back to phase 1's defaults if the entry's own phase is
    somehow missing or invalid -- these are only a starting suggestion
    the owner can freely change, never a safety-relevant value.
    """
    try:
        index = int(entry_phase) - 1
    except (TypeError, ValueError):
        return 0
    return index if 0 <= index < len(GRID_PHASE_POWER) else 0


class ZendureLocalConfigFlow(
    config_entries.ConfigFlow,
    domain=DOMAIN,
):
    VERSION = 1

    @staticmethod
    def async_get_options_flow(entry):
        return ZendureLocalOptionsFlow()

    def __init__(self) -> None:
        self._discovered_host = None

    async def _probe(self, host: str) -> dict:
        return await ZendureLocalClient(
            async_get_clientsession(self.hass),
            host,
        ).async_get_properties()

    def _phase_in_use(
        self,
        phase: str,
        *,
        exclude_entry_id: str | None = None,
    ) -> bool:
        return phase_in_use(
            self._async_current_entries(),
            phase,
            exclude_entry_id=exclude_entry_id,
        )

    async def async_step_zeroconf(
        self,
        discovery_info: ZeroconfServiceInfo,
    ):
        self._discovered_host = discovery_info.host
        try:
            properties = await self._probe(self._discovered_host)
        except ZendureLocalError:
            return self.async_abort(reason="cannot_connect")
        serial = str(properties.get("sn", "")).strip()
        if not serial:
            return self.async_abort(reason="identity_missing")
        await self.async_set_unique_id(serial)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: self._discovered_host}
        )
        return await self.async_step_user()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            phase = user_input[CONF_PHASE]
            if self._phase_in_use(phase):
                errors[CONF_PHASE] = "phase_in_use"
            else:
                try:
                    properties = await self._probe(host)
                except ZendureLocalError:
                    errors["base"] = "cannot_connect"
                else:
                    serial = str(properties.get("sn", "")).strip()
                    if not serial:
                        errors["base"] = "identity_missing"
                    else:
                        await self.async_set_unique_id(serial)
                        self._abort_if_unique_id_configured()
                        return self.async_create_entry(
                            title=f"Zendure L{phase}",
                            data={
                                CONF_HOST: host,
                                CONF_PHASE: phase,
                                CONF_PROFILE: user_input[CONF_PROFILE],
                                "serial_number": serial,
                            },
                        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST,
                    default=self._discovered_host or "",
                ): str,
                vol.Required(CONF_PHASE): _phase_selector(),
                vol.Required(
                    CONF_PROFILE,
                    default=PROFILE_SOLARFLOW_3000_MIX_AC_PLUS,
                ): _profile_selector(),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            phase = user_input[CONF_PHASE]
            if self._phase_in_use(
                phase,
                exclude_entry_id=entry.entry_id,
            ):
                errors[CONF_PHASE] = "phase_in_use"
            else:
                try:
                    properties = await self._probe(host)
                except ZendureLocalError:
                    errors["base"] = "cannot_connect"
                else:
                    serial = str(properties.get("sn", "")).strip()
                    if serial != entry.unique_id:
                        errors["base"] = "identity_mismatch"
                    else:
                        return self.async_update_reload_and_abort(
                            entry,
                            data_updates={
                                **entry.data,
                                CONF_HOST: host,
                                CONF_PHASE: phase,
                                CONF_PROFILE: user_input[CONF_PROFILE],
                            },
                        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST,
                    default=entry.data[CONF_HOST],
                ): str,
                vol.Required(
                    CONF_PHASE,
                    default=str(entry.data[CONF_PHASE]),
                ): _phase_selector(),
                vol.Required(
                    CONF_PROFILE,
                    default=entry.data.get(
                        CONF_PROFILE,
                        PROFILE_GENERIC_READ_ONLY,
                    ),
                ): _profile_selector(),
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )


class ZendureLocalOptionsFlow(config_entries.OptionsFlow):
    """Sets the commissioning power caps for one battery.

    The caps bound every command the controller can ever issue, so they
    are deliberately capped again at the commissioning ceiling rather
    than at the device nameplate.

    Added 2026-09-30: the grid phase power/current sensors and the
    phase current limit/operating margin are now also configurable here
    instead of being fixed, one-household constants -- see const.py's
    CONF_GRID_PHASE_*_ENTITY / CONF_PHASE_CURRENT_LIMIT_A /
    CONF_PHASE_OPERATING_MARGIN_A docstrings. Every field still defaults
    to this author's own values, so accepting the form unchanged is a
    no-op for an existing install.
    """

    async def async_step_init(self, user_input=None):
        entry = self.config_entry
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_PROFILE: entry.options.get(
                        CONF_PROFILE,
                        entry.data[CONF_PROFILE],
                    ),
                    CONF_MAX_CHARGE_W: user_input[CONF_MAX_CHARGE_W],
                    CONF_MAX_DISCHARGE_W: user_input[CONF_MAX_DISCHARGE_W],
                    CONF_GRID_PHASE_POWER_ENTITY: user_input[
                        CONF_GRID_PHASE_POWER_ENTITY
                    ],
                    CONF_GRID_PHASE_CURRENT_ENTITY: user_input[
                        CONF_GRID_PHASE_CURRENT_ENTITY
                    ],
                    CONF_PHASE_CURRENT_LIMIT_A: user_input[
                        CONF_PHASE_CURRENT_LIMIT_A
                    ],
                    CONF_PHASE_OPERATING_MARGIN_A: user_input[
                        CONF_PHASE_OPERATING_MARGIN_A
                    ],
                },
            )
        cap = vol.All(
            vol.Coerce(int),
            vol.Range(min=0, max=COMMISSIONING_CEILING_W),
        )
        default_index = _default_phase_index(
            entry.options.get(CONF_PHASE, entry.data.get(CONF_PHASE))
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_MAX_CHARGE_W,
                    default=entry.options.get(
                        CONF_MAX_CHARGE_W,
                        DEFAULT_MAX_CHARGE_W,
                    ),
                ): cap,
                vol.Required(
                    CONF_MAX_DISCHARGE_W,
                    default=entry.options.get(
                        CONF_MAX_DISCHARGE_W,
                        DEFAULT_MAX_DISCHARGE_W,
                    ),
                ): cap,
                vol.Required(
                    CONF_GRID_PHASE_POWER_ENTITY,
                    default=entry.options.get(
                        CONF_GRID_PHASE_POWER_ENTITY,
                        entry.data.get(
                            CONF_GRID_PHASE_POWER_ENTITY,
                            GRID_PHASE_POWER[default_index],
                        ),
                    ),
                ): _sensor_entity_selector(),
                vol.Required(
                    CONF_GRID_PHASE_CURRENT_ENTITY,
                    default=entry.options.get(
                        CONF_GRID_PHASE_CURRENT_ENTITY,
                        entry.data.get(
                            CONF_GRID_PHASE_CURRENT_ENTITY,
                            GRID_PHASE_CURRENT[default_index],
                        ),
                    ),
                ): _sensor_entity_selector(),
                vol.Required(
                    CONF_PHASE_CURRENT_LIMIT_A,
                    default=entry.options.get(
                        CONF_PHASE_CURRENT_LIMIT_A,
                        entry.data.get(
                            CONF_PHASE_CURRENT_LIMIT_A,
                            PHASE_CURRENT_LIMIT_A,
                        ),
                    ),
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=63)),
                vol.Required(
                    CONF_PHASE_OPERATING_MARGIN_A,
                    default=entry.options.get(
                        CONF_PHASE_OPERATING_MARGIN_A,
                        entry.data.get(
                            CONF_PHASE_OPERATING_MARGIN_A,
                            PHASE_OPERATING_MARGIN_A,
                        ),
                    ),
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=20)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
