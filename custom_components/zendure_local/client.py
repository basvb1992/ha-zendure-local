from __future__ import annotations

import asyncio

from aiohttp import ClientError, ClientSession

from .const import HTTP_TIMEOUT_SECONDS


class ZendureLocalError(Exception):
    """Base error for the local ZenSDK client."""


class ZendureLocalConnectionError(ZendureLocalError):
    """Raised when the local device cannot be reached."""


class ZendureLocalResponseError(ZendureLocalError):
    """Raised when the local device returns an invalid response."""


class ZendureLocalClient:
    def __init__(self, session: ClientSession, host: str) -> None:
        self._session = session
        self.host = host.strip().rstrip("/")

    @property
    def base_url(self) -> str:
        if self.host.startswith(("http://", "https://")):
            return self.host
        return f"http://{self.host}"

    async def async_get_properties(self) -> dict:
        try:
            async with asyncio.timeout(HTTP_TIMEOUT_SECONDS):
                async with self._session.get(
                    f"{self.base_url}/properties/report"
                ) as response:
                    if response.status != 200:
                        raise ZendureLocalResponseError(
                            f"Device returned HTTP {response.status}"
                        )
                    payload = await response.json(content_type=None)
        except ZendureLocalResponseError:
            raise
        except (TimeoutError, ClientError, ValueError) as err:
            raise ZendureLocalConnectionError(str(err)) from err
        if not isinstance(payload, dict):
            raise ZendureLocalResponseError(
                "Properties response must be a JSON object"
            )
        properties = payload.get("properties", payload)
        if not isinstance(properties, dict):
            raise ZendureLocalResponseError(
                "Properties payload must be a JSON object"
            )
        # Real devices report device identity fields (sn, product,
        # version, timestamp) as siblings of "properties" rather than
        # inside it. Merge them in without overwriting anything the
        # device may already report inside "properties" itself.
        merged = dict(properties)
        for key in ("sn", "product", "version", "timestamp"):
            if key not in merged and key in payload:
                merged[key] = payload[key]
        return merged

    async def async_write_properties(
        self,
        serial: str,
        properties: dict,
    ) -> dict:
        """Send one complete ZenSDK command.

        Callers must always pass a full directional payload that also
        clears the opposite limit. This client deliberately performs no
        merging or partial updates, so a half-applied command can never
        leave a latched limit behind.
        """
        body = {"sn": serial, "properties": dict(properties)}
        try:
            async with asyncio.timeout(HTTP_TIMEOUT_SECONDS):
                async with self._session.post(
                    f"{self.base_url}/properties/write",
                    json=body,
                ) as response:
                    if response.status != 200:
                        raise ZendureLocalResponseError(
                            f"Device returned HTTP {response.status}"
                        )
                    payload = await response.json(content_type=None)
        except ZendureLocalResponseError:
            raise
        except (TimeoutError, ClientError, ValueError) as err:
            raise ZendureLocalConnectionError(str(err)) from err
        return payload if isinstance(payload, dict) else {}
