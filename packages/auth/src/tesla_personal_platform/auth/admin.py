"""Idempotent manual user-administration orchestration."""

from typing import Protocol

from tesla_personal_platform.auth.models import AllowedUser


class AllowlistAdminStore(Protocol):
    """Trusted administrative writes to the platform allowlist."""

    def ensure_invitation(self, email: str, notes: str | None = None) -> AllowedUser:
        """Create or return stable opaque identifiers for one invitation."""
        ...

    def activate(self, email: str) -> AllowedUser:
        """Mark a fully provisioned invitation active."""
        ...

    def disable(self, email: str) -> AllowedUser:
        """Idempotently block a user without deleting data or identity state."""
        ...


class DatasetProvisioner(Protocol):
    """Create and repair one isolated per-user analytics dataset."""

    def provision(self, user: AllowedUser) -> None:
        """Ensure the dataset, no-expiration policy, and runtime access."""
        ...


class UserAdminService:
    """Coordinate recoverable, idempotent manual user operations."""

    def __init__(self, allowlist: AllowlistAdminStore, datasets: DatasetProvisioner) -> None:
        self._allowlist = allowlist
        self._datasets = datasets

    def add_user(self, email: str, notes: str | None = None) -> AllowedUser:
        """Allocate once, repair the dataset on every run, then activate access."""
        user = self._allowlist.ensure_invitation(email, notes)
        self._datasets.provision(user)
        return self._allowlist.activate(user.invitation_email)

    def disable_user(self, email: str) -> AllowedUser:
        """Block platform access while preserving bindings and historical data."""
        return self._allowlist.disable(email)
