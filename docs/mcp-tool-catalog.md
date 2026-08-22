# MCP Tool Catalog

**Status:** Placeholder for Phase 6.

No Tesla MCP tools are implemented through Phase 4. Tesla onboarding uses narrow
HTTP routes protected by the platform identity boundary; partner registration
is operator-only. Phase 6 will map every `MCP` row in [the Fleet API coverage
contract](fleet-api-coverage.md) to an intentional typed tool or grouped tool and
document its scope, wake behavior, risk class, retry/idempotency policy, and
audit behavior.

The eventual catalog must not contain a generic Tesla API passthrough. The generic historical interface is limited to the separately specified `get_analytics_schema` and `run_analytics_query` tools.
