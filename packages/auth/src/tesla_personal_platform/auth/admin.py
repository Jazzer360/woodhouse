"""Idempotent user administration and active-tenant analytics reconciliation."""

from dataclasses import dataclass
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

    def reset_identity(self, email: str, expected_user_id: str) -> AllowedUser:
        """Deliberately clear one binding after confirming its opaque user ID."""
        ...


class DatasetProvisioner(Protocol):
    """Create and repair one isolated per-user analytics dataset."""

    def provision(self, user: AllowedUser) -> None:
        """Ensure the dataset, no-expiration policy, and runtime access."""
        ...


class ActiveAllowlistReader(Protocol):
    """List server-trusted active tenant mappings without caller-supplied identifiers."""

    def list_active_users(self) -> tuple[AllowedUser, ...]:
        """Return every active allowlist record."""
        ...


@dataclass(frozen=True, slots=True)
class AnalyticsViewReconciliation:
    """One dataset's deterministic managed-view reconciliation result."""

    desired_view_count: int
    removed_view_count: int


class DatasetViewReconciler(Protocol):
    """Reconcile source-defined views inside one trusted dataset identifier."""

    def reconcile(self, dataset_id: str) -> AnalyticsViewReconciliation:
        """Create/update desired views and remove stale managed views."""
        ...


@dataclass(frozen=True, slots=True)
class AnalyticsViewSyncSummary:
    """Non-sensitive aggregate outcome for one all-active-user run."""

    active_user_count: int
    desired_view_count: int
    removed_view_count: int


class AnalyticsViewSyncService:
    """Apply the source-defined analytics view set to every active tenant."""

    def __init__(
        self,
        allowlist: ActiveAllowlistReader,
        views: DatasetViewReconciler,
    ) -> None:
        self._allowlist = allowlist
        self._views = views

    def sync_active_users(self) -> AnalyticsViewSyncSummary:
        """Reconcile all active users after validating tenant identifiers are unique."""
        users = self._allowlist.list_active_users()
        dataset_ids = [user.dataset_id for user in users]
        user_ids = [user.user_id for user in users]
        if len(set(dataset_ids)) != len(dataset_ids) or len(set(user_ids)) != len(user_ids):
            raise RuntimeError("Active allowlist contains duplicate tenant identifiers")

        desired_view_count = 0
        removed_view_count = 0
        for user in users:
            try:
                result = self._views.reconcile(user.dataset_id)
            except Exception as error:
                raise RuntimeError(
                    "Analytics view reconciliation failed for one active tenant"
                ) from error
            desired_view_count += result.desired_view_count
            removed_view_count += result.removed_view_count
        return AnalyticsViewSyncSummary(
            active_user_count=len(users),
            desired_view_count=desired_view_count,
            removed_view_count=removed_view_count,
        )


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

    def reset_user_identity(self, email: str, expected_user_id: str) -> AllowedUser:
        """Clear a stale provider binding without changing the tenant identifiers."""
        return self._allowlist.reset_identity(email, expected_user_id)
