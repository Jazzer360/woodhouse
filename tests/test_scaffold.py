"""Phase 1 tests for package boundaries and harmless service health documents."""

from collections.abc import Callable
from pathlib import Path

import pytest
import tesla_personal_platform.analytics as analytics
import tesla_personal_platform.auth as auth
import tesla_personal_platform.event_schema as event_schema
import tesla_personal_platform.shared_models as shared_models
import tesla_personal_platform.tesla_client as tesla_client
from tesla_personal_platform.mcp_gateway.main import health_document as gateway_health
from tesla_personal_platform.telemetry_edge.main import health_document as edge_health
from tesla_personal_platform.telemetry_processor.main import health_document as processor_health

ROOT = Path(__file__).parents[1]


def test_documented_boundaries_exist() -> None:
    expected = {
        "infra/terraform",
        "packages/analytics",
        "packages/auth",
        "packages/event-schema",
        "packages/shared-models",
        "packages/tesla-client",
        "scripts/admin",
        "scripts/dev",
        "services/mcp-gateway",
        "services/telemetry-edge",
        "services/telemetry-processor",
    }

    missing = sorted(path for path in expected if not (ROOT / path).is_dir())

    assert not missing, f"Missing documented directories: {', '.join(missing)}"


def test_seed_documents_remain_present() -> None:
    expected = {
        "architecture.md",
        "data-and-analytics.md",
        "event-and-webhooks.md",
        "fleet-api-coverage.md",
        "implementation-roadmap.md",
        "prompt-pack.md",
        "security-model.md",
        "tesla-onboarding.md",
    }

    assert expected <= {path.name for path in (ROOT / "docs").glob("*.md")}


@pytest.mark.parametrize(
    ("health", "service"),
    [
        (gateway_health, "mcp-gateway"),
        (edge_health, "telemetry-edge"),
        (processor_health, "telemetry-processor"),
    ],
)
def test_service_health_documents_match_implemented_phase(
    health: Callable[[], dict[str, str]], service: str
) -> None:
    expected_phase = "platform-auth" if service == "mcp-gateway" else "scaffold"
    assert health() == {"phase": expected_phase, "service": service, "status": "ok"}


def test_shared_package_boundaries_are_importable() -> None:
    assert {
        analytics.COMPONENT,
        auth.COMPONENT,
        event_schema.COMPONENT,
        shared_models.COMPONENT,
        tesla_client.COMPONENT,
    } == {"analytics", "auth", "event-schema", "shared-models", "tesla-client"}
