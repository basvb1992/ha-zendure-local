from __future__ import annotations

from .const import CONF_PHASE


def phase_in_use(
    entries,
    phase: str,
    *,
    exclude_entry_id: str | None = None,
) -> bool:
    return any(
        entry.entry_id != exclude_entry_id
        and str(
            entry.options.get(
                CONF_PHASE,
                entry.data.get(CONF_PHASE),
            )
        )
        == phase
        for entry in entries
    )
