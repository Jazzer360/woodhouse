"""Keep the endpoint-grade MCP reference synchronized with the live registry."""

import runpy
from collections.abc import Callable
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "docs" / "mcp-tool-reference.md"
GENERATOR = ROOT / "scripts" / "dev" / "generate-mcp-tool-reference.py"


def test_reference_exactly_matches_the_live_registry_generator() -> None:
    namespace = runpy.run_path(str(GENERATOR))
    render_reference = cast(Callable[[], str], namespace["render_reference"])

    assert REFERENCE.read_text(encoding="utf-8") == render_reference()


def test_reference_documents_the_non_passthrough_safety_contract() -> None:
    document = REFERENCE.read_text(encoding="utf-8")

    assert "call_tesla_api" not in document
    assert "callers cannot supply VINs, paths, methods" in document
    assert "indeterminate and is never retried automatically" in document
    assert "explicit_current_turn_intent=true" in document
