"""Firestore adapters for immutable identity binding and manual invitations."""

import secrets
from collections.abc import Callable, Mapping
from dataclasses import replace
from hashlib import sha256
from typing import Any, cast

from google.cloud import firestore
from google.cloud.firestore_v1 import Client
from google.cloud.firestore_v1.document import DocumentReference
from google.cloud.firestore_v1.transaction import Transaction
from tesla_personal_platform.auth.core import first_login_invitation_email, normalize_email
from tesla_personal_platform.auth.errors import (
    ConfigurationError,
    IdentityMismatchError,
    UserDisabledError,
    UserNotAllowedError,
)
from tesla_personal_platform.auth.models import (
    AllowedUser,
    UserContext,
    UserStatus,
    VerifiedIdentity,
)

ALLOWED_USERS_COLLECTION = "allowed_users"
OIDC_IDENTITIES_COLLECTION = "oidc_identities"


def _new_user_id() -> str:
    return f"usr_{secrets.token_hex(16)}"


def _new_dataset_id() -> str:
    return f"tesla_u_{secrets.token_hex(16)}"


def _identity_document_id(issuer: str, subject: str) -> str:
    return sha256(f"{issuer}\0{subject}".encode()).hexdigest()


def _record_from_data(email: str, data: Mapping[str, Any] | None) -> AllowedUser:
    if data is None:
        raise ConfigurationError("Allowlist record has no data")
    try:
        status = UserStatus(data["status"])
        user_id = data["user_id"]
        dataset_id = data["dataset_id"]
    except (KeyError, TypeError, ValueError) as error:
        raise ConfigurationError("Allowlist record is incomplete") from error
    if not isinstance(user_id, str) or not user_id:
        raise ConfigurationError("Allowlist record has an invalid user ID")
    if not isinstance(dataset_id, str) or not dataset_id:
        raise ConfigurationError("Allowlist record has an invalid dataset ID")

    issuer = data.get("oidc_issuer")
    subject = data.get("oidc_subject")
    if issuer is not None and not isinstance(issuer, str):
        raise ConfigurationError("Allowlist issuer has an invalid type")
    if subject is not None and not isinstance(subject, str):
        raise ConfigurationError("Allowlist subject has an invalid type")
    if (issuer is None) is not (subject is None):
        raise ConfigurationError("Allowlist immutable identity is partially bound")

    return AllowedUser(
        invitation_email=email,
        user_id=user_id,
        dataset_id=dataset_id,
        status=status,
        oidc_issuer=issuer,
        oidc_subject=subject,
    )


def _require_active(user: AllowedUser) -> None:
    if user.status is not UserStatus.ACTIVE:
        raise UserDisabledError("User is disabled")


def _context(user: AllowedUser, identity: VerifiedIdentity) -> UserContext:
    _require_active(user)
    if (user.oidc_issuer, user.oidc_subject) != (identity.issuer, identity.subject):
        raise IdentityMismatchError("Immutable identity binding is inconsistent")
    return UserContext(
        user_id=user.user_id,
        dataset_id=user.dataset_id,
        oidc_issuer=identity.issuer,
        oidc_subject=identity.subject,
    )


class FirestoreIdentityStore:
    """Atomically bind an active invitation and resolve later logins by identity."""

    def __init__(self, client: Client) -> None:
        self.client = client

    def resolve_or_bind(self, identity: VerifiedIdentity) -> UserContext:
        """Resolve an existing binding first; use verified email only for first bind."""
        transaction = self.client.transaction()
        result = _resolve_or_bind_transaction(transaction, self, identity)
        return cast(UserContext, result)

    def allowed_user(self, email: str) -> DocumentReference:
        return self.client.collection(ALLOWED_USERS_COLLECTION).document(normalize_email(email))

    def identity(self, issuer: str, subject: str) -> DocumentReference:
        document_id = _identity_document_id(issuer, subject)
        return self.client.collection(OIDC_IDENTITIES_COLLECTION).document(document_id)


@firestore.transactional
def _resolve_or_bind_transaction(
    transaction: Transaction,
    store: FirestoreIdentityStore,
    identity: VerifiedIdentity,
) -> UserContext:
    binding_reference = store.identity(identity.issuer, identity.subject)
    binding_snapshot = binding_reference.get(transaction=transaction)
    if binding_snapshot.exists:
        binding_data = binding_snapshot.to_dict()
        if binding_data is None:
            raise ConfigurationError("OIDC binding has no data")
        if (binding_data.get("oidc_issuer"), binding_data.get("oidc_subject")) != (
            identity.issuer,
            identity.subject,
        ):
            raise ConfigurationError("OIDC binding hash does not match its identity")
        allowlist_email = binding_data.get("allowlist_email")
        if not isinstance(allowlist_email, str):
            raise ConfigurationError("OIDC binding is missing its allowlist key")
        user_snapshot = store.allowed_user(allowlist_email).get(transaction=transaction)
        if not user_snapshot.exists:
            raise ConfigurationError("OIDC binding points to a missing allowlist record")
        user = _record_from_data(allowlist_email, user_snapshot.to_dict())
        if binding_data.get("user_id") != user.user_id:
            raise ConfigurationError("OIDC binding user does not match its allowlist record")
        return _context(user, identity)

    email = first_login_invitation_email(identity)
    user_reference = store.allowed_user(email)
    user_snapshot = user_reference.get(transaction=transaction)
    if not user_snapshot.exists:
        raise UserNotAllowedError("No active invitation")
    user = _record_from_data(email, user_snapshot.to_dict())
    _require_active(user)

    if user.oidc_issuer is not None or user.oidc_subject is not None:
        if (user.oidc_issuer, user.oidc_subject) != (identity.issuer, identity.subject):
            raise IdentityMismatchError("Invitation is bound to another identity")
    else:
        transaction.update(
            user_reference,
            {
                "oidc_issuer": identity.issuer,
                "oidc_subject": identity.subject,
                "bound_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
        )
        user = replace(
            user,
            oidc_issuer=identity.issuer,
            oidc_subject=identity.subject,
        )

    transaction.create(
        binding_reference,
        {
            "allowlist_email": email,
            "oidc_issuer": identity.issuer,
            "oidc_subject": identity.subject,
            "user_id": user.user_id,
            "created_at": firestore.SERVER_TIMESTAMP,
        },
    )

    return _context(user, identity)


class FirestoreAllowlistAdminStore:
    """Idempotently manage stable allowlist identifiers and access status."""

    def __init__(
        self,
        client: Client,
        user_id_factory: Callable[[], str] = _new_user_id,
        dataset_id_factory: Callable[[], str] = _new_dataset_id,
    ) -> None:
        self.client = client
        self._user_id_factory = user_id_factory
        self._dataset_id_factory = dataset_id_factory

    def ensure_invitation(self, email: str, notes: str | None = None) -> AllowedUser:
        """Allocate identifiers once and mark dataset repair as pending."""
        normalized = normalize_email(email)
        transaction = self.client.transaction()
        return cast(
            AllowedUser,
            _ensure_invitation_transaction(transaction, self, normalized, notes),
        )

    def activate(self, email: str) -> AllowedUser:
        """Activate only after dataset provisioning has succeeded."""
        return self._set_status(email, UserStatus.ACTIVE, "ready")

    def disable(self, email: str) -> AllowedUser:
        """Disable without deleting identifiers, bindings, or data."""
        return self._set_status(email, UserStatus.DISABLED, "ready")

    def reset_identity(self, email: str, expected_user_id: str) -> AllowedUser:
        """Clear a binding only after the operator confirms the opaque user ID."""
        normalized = normalize_email(email)
        transaction = self.client.transaction()
        return cast(
            AllowedUser,
            _reset_identity_transaction(transaction, self, normalized, expected_user_id),
        )

    def _set_status(
        self,
        email: str,
        status: UserStatus,
        provisioning_state: str,
    ) -> AllowedUser:
        normalized = normalize_email(email)
        transaction = self.client.transaction()
        return cast(
            AllowedUser,
            _set_status_transaction(
                transaction,
                self,
                normalized,
                status,
                provisioning_state,
            ),
        )

    def document(self, email: str) -> DocumentReference:
        return self.client.collection(ALLOWED_USERS_COLLECTION).document(normalize_email(email))


@firestore.transactional
def _ensure_invitation_transaction(
    transaction: Transaction,
    store: FirestoreAllowlistAdminStore,
    email: str,
    notes: str | None,
) -> AllowedUser:
    reference = store.document(email)
    snapshot = reference.get(transaction=transaction)
    if snapshot.exists:
        user = _record_from_data(email, snapshot.to_dict())
        updates: dict[str, object] = {
            "provisioning_state": "pending",
            "updated_at": firestore.SERVER_TIMESTAMP,
        }
        if notes is not None:
            updates["notes"] = notes
        transaction.update(reference, updates)
        return user

    user = AllowedUser(
        invitation_email=email,
        user_id=store._user_id_factory(),
        dataset_id=store._dataset_id_factory(),
        status=UserStatus.DISABLED,
    )
    transaction.create(
        reference,
        {
            "email": email,
            "user_id": user.user_id,
            "dataset_id": user.dataset_id,
            "status": user.status.value,
            "oidc_issuer": None,
            "oidc_subject": None,
            "notes": notes or "",
            "provisioning_state": "pending",
            "created_at": firestore.SERVER_TIMESTAMP,
            "updated_at": firestore.SERVER_TIMESTAMP,
        },
    )
    return user


@firestore.transactional
def _set_status_transaction(
    transaction: Transaction,
    store: FirestoreAllowlistAdminStore,
    email: str,
    status: UserStatus,
    provisioning_state: str,
) -> AllowedUser:
    reference = store.document(email)
    snapshot = reference.get(transaction=transaction)
    if not snapshot.exists:
        raise UserNotAllowedError("Allowlist record does not exist")
    user = _record_from_data(email, snapshot.to_dict())
    transaction.update(
        reference,
        {
            "status": status.value,
            "provisioning_state": provisioning_state,
            "updated_at": firestore.SERVER_TIMESTAMP,
        },
    )
    return replace(user, status=status)


@firestore.transactional
def _reset_identity_transaction(
    transaction: Transaction,
    store: FirestoreAllowlistAdminStore,
    email: str,
    expected_user_id: str,
) -> AllowedUser:
    reference = store.document(email)
    snapshot = reference.get(transaction=transaction)
    if not snapshot.exists:
        raise UserNotAllowedError("Allowlist record does not exist")
    user = _record_from_data(email, snapshot.to_dict())
    if user.user_id != expected_user_id:
        raise ValueError("Confirmed user ID does not match the allowlist record")
    if user.oidc_issuer is not None and user.oidc_subject is not None:
        identity_reference = store.client.collection(OIDC_IDENTITIES_COLLECTION).document(
            _identity_document_id(user.oidc_issuer, user.oidc_subject)
        )
        transaction.delete(identity_reference)
    transaction.update(
        reference,
        {
            "oidc_issuer": None,
            "oidc_subject": None,
            "bound_at": firestore.DELETE_FIELD,
            "updated_at": firestore.SERVER_TIMESTAMP,
        },
    )
    return replace(user, oidc_issuer=None, oidc_subject=None)
