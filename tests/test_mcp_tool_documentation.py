"""Keep the endpoint-grade MCP reference synchronized with the live registry."""

from pathlib import Path

from tesla_personal_platform.mcp_gateway.mcp_tools import MCP_TOOL_SPECS

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "docs" / "mcp-tool-reference.md"


def test_every_live_tool_and_argument_is_in_the_generated_reference() -> None:
    document = REFERENCE.read_text(encoding="utf-8")

    assert f"## Tool index ({len(MCP_TOOL_SPECS)})" in document
    for spec in MCP_TOOL_SPECS:
        assert f"### `{spec.name}`" in document
        assert f"- Tesla scope: `{spec.required_scope}`" in document
        assert f"- Vehicle wake: `{spec.wake_behavior}`" in document
        assert f"- Risk: `{spec.risk}`" in document
        for argument in spec.input_schema()["properties"]:
            assert f"| `{argument}` |" in document


def test_reference_documents_the_non_passthrough_safety_contract() -> None:
    document = REFERENCE.read_text(encoding="utf-8")

    assert "call_tesla_api" not in document
    assert "callers cannot supply VINs, paths, methods" in document
    assert "indeterminate and is never retried automatically" in document
    assert "explicit_current_turn_intent=true" in document
