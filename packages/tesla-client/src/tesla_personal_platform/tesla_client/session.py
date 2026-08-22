"""Per-user credential selection around the typed Fleet API client."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, TypeVar

from tesla_personal_platform.tesla_client.errors import TeslaReauthorizationRequired

ResultT = TypeVar("ResultT")
FleetT = TypeVar("FleetT")


@dataclass(frozen=True, slots=True)
class TeslaAccessContext:
    """One server-selected user's short-lived access context."""

    access_token: str = field(repr=False)
    base_url: str


class TeslaAccessProvider(Protocol):
    """Gateway-owned provider that selects and atomically rotates user credentials."""

    def access_for_user(
        self,
        owner_user_id: str,
        *,
        force_refresh: bool = False,
        now: datetime | None = None,
    ) -> TeslaAccessContext: ...


class PerUserTeslaClient[FleetT]:
    """Execute only typed client calls with credentials selected by internal user ID."""

    def __init__(self, fleet: FleetT, credentials: TeslaAccessProvider) -> None:
        self._fleet = fleet
        self._credentials = credentials

    def execute(
        self,
        owner_user_id: str,
        operation: Callable[[FleetT, str, str], ResultT],
        *,
        now: datetime | None = None,
    ) -> ResultT:
        context = self._credentials.access_for_user(owner_user_id, now=now)
        try:
            return operation(self._fleet, context.access_token, context.base_url)
        except TeslaReauthorizationRequired:
            # A 401 means Tesla rejected the credential before authorizing the operation.
            refreshed = self._credentials.access_for_user(
                owner_user_id,
                force_refresh=True,
                now=now,
            )
            return operation(self._fleet, refreshed.access_token, refreshed.base_url)
