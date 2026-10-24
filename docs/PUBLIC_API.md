# AgentGuard Public API and Python SDK

The REST API is the stable integration boundary for application clients. Legacy
`/v1/kernel/*` and `/v1/runs/*` paths remain available; business aliases below
route through the same AgentGuard services and lifecycle journal. A successful
HTTP response reports the state actually returned by the service. It does not
upgrade synthetic, observational, blocked, failed, or unsupported evidence.

## Business routes

| Route | Purpose |
| --- | --- |
| `GET /v1/system/health` | AgentGuard service health; does not probe WARDEN or a model runtime |
| `GET /v1/system/capabilities` | Existing AgentGuard capability manifest |
| `GET /v1/kernel/status` | WARDEN integration availability and blockers |
| `GET /v1/scenarios` | Public scenario catalog |
| `POST /v1/investigations` | Submit a typed WARDEN audit request through the durable lifecycle service |
| `GET /v1/investigations?status=&limit=&offset=` | Paginated workflow summaries |
| `POST/GET /v1/workflows` | Workflow-oriented aliases for the same submit/list operations |
| `GET /v1/investigations/{request_id}` | Durable state, remote IDs, and safe result summary |
| `GET /v1/investigations/{request_id}/evidence` | Governed evidence summary; does not open WARDEN artifact URIs |
| `GET /v1/evidence/investigations/{request_id}` | Evidence-oriented alias for the same governed summary |
| `GET /v1/defense-policies/{request_id}` | Read proposal-only defense candidates |
| `POST /v1/defense-policies/{request_id}/preview` | Persist a proposal preview; does not approve or execute |
| `POST /v1/defense-policies/{request_id}/execute` | Compatibility alias; separate approval token required, and the missing external executor remains `unsupported` |
| `GET /v1/reports/{run_id}` | Read an AgentGuard report (including explicitly synthetic reports) |
| `GET /v1/system/audit-events?limit=&offset=` | Operator-only API request audit page |
| `GET /v1/system/readiness?force_refresh=` | Layered readiness checks (connectivity, API compatibility, contracts, model runtime, Probe/SAE, audit and intervention execution, evidence access) with per-layer blockers, cache provenance; WARDEN health is never promoted to executability |
| `GET /v1/investigations/options?force_refresh=` | Discovered audit-mode options, threat contracts, Probe/SAE resources and capture-target support for investigation forms; nothing unreported is invented |
| `GET /v1/investigations/{request_id}/interaction-graph?run_id=` | Message-level interaction graph from authorized WARDEN run metadata; `available=false` with `GRAPH_SOURCE_UNAVAILABLE` when no compliant source exists; an explicit `run_id` renders the synthetic engine report, always labelled `agentguard_synthetic_report` |
| `GET /v1/investigations/{request_id}/timeline?after_event_id=` | Durable journal timeline for one investigation; steps without recorded events are never synthesized |
| `GET /v1/experiments?status=&scenario_id=&data_source=&limit=&offset=` | Read-only experiment index over persisted WARDEN workflows and run-report artifacts; missing metrics stay `null` |
| `GET /v1/experiments/{experiment_id}` | One indexed experiment by experiment/request/run id |
| `POST /v1/experiments/compare` | Descriptive comparison of two existing run reports after comparability checks; incomparable pairs return `not_comparable` with reasons |
| `GET /v1/reports?status=&scenario_id=&limit=&offset=` | Paginated index of persisted run reports (artifact digests only, never paths) |

List responses return `items`, page `count`, `offset`, `limit` and, where
applicable, `next_offset`. The original endpoint paths and response fields remain
available during migration.

Evidence summaries expose the C5 artifact capability fields
(`metadata_available`, `content_access_supported`, `integrity_verified`,
`access_restriction`); `integrity_verified` is true only for AgentGuard-local
content-addressed artifacts whose hash was re-verified. Defense previews expose
the C6 capability fields (`approval_required_per_candidate`,
`execution_supported_overall`, `rollback_supported`,
`verification_evidence_available`, `benign_utility_data_available`,
`capability_notes`); approval never implies execution support.

## Python SDK

The SDK uses only the Python standard library, validates the API origin, rejects
redirects, bounds response size, quotes resource identifiers, and exposes
classified `AgentGuardAPIError` failures. It never retries a mutation.

```python
from agentguard.sdk import AgentGuardClient

client = AgentGuardClient.from_env()
health = client.health()
page = client.list_investigations(limit=25, offset=0)
readiness = client.get_readiness()
options = client.get_investigation_options()
graph = client.get_investigation_graph("inv-1")
timeline = client.get_investigation_timeline("inv-1")
experiments = client.list_experiments(limit=25)
detail = client.get_experiment("exp-1")
comparison = client.compare_experiments(["run-a", "run-b"])
reports = client.list_reports(status="completed")
```

Environment variables:

- `AGENTGUARD_BASE_URL` (default `http://127.0.0.1:8000`)
- `AGENTGUARD_API_TOKEN` (optional general API credential)
- `AGENTGUARD_OPERATOR_API_TOKEN` (optional separate operator credential)
- `AGENTGUARD_API_TIMEOUT` (default 15 seconds; maximum 120)

## Authentication and operational controls

- With `AGENTGUARD_API_TOKEN` configured, non-public routes require
  `X-AgentGuard-Token`. If a separate operator token is configured, it can also
  authenticate general API requests.
- Policy activation/rollback and API audit reads require
  `X-AgentGuard-Operator-Token` when `AGENTGUARD_OPERATOR_API_TOKEN` is set. For
  migration compatibility, if no separate operator token is configured, the
  existing API token remains accepted for those operator routes. Deployments
  should set a distinct operator token.
- `AGENTGUARD_APPROVAL_TOKEN` remains independent and gates approval-sensitive
  actions. MCP has no approval or defense-execution tool.
- Each response carries `X-Request-ID`. A syntactically valid caller value is
  preserved; otherwise AgentGuard creates a UUID. Error bodies include it for
  middleware-level authorization and rate-limit failures.
- Request audit events are reserved before routing, then updated with request ID,
  method, matched route template, status, actor role, and timestamp in the existing
  workflow SQLite database. If the reservation fails, the API returns 503 without
  dispatching the request; a process interruption can leave an explicit incomplete
  event. Events never store query strings, bodies, or credentials. Unmatched paths
  are stored as `/<unmatched>`. The journal retains approximately the latest
  100,000 events.
- `AGENTGUARD_RATE_LIMIT_PER_MINUTE` sets an in-memory per-process limit per
  direct peer address (default 300). Forwarded IP headers are not trusted. The
  limit resets on process restart and is not shared between worker processes;
  multi-worker deployments should enforce a shared edge limit at a trusted
  reverse proxy.
- `AGENTGUARD_CORS_ORIGINS` is a comma-separated explicit origin allowlist.
  Wildcard origins are rejected. CORS is disabled when the variable is empty.

## Restricted MCP

`agentguard-mcp` is a client of this API, using the same SDK and configured
credentials. It provides read-only status/catalog/investigation/evidence and
verified-experience queries, proposal-only defense previews, and creation of
`observation`, `capture_only`, or `detection` plans with `start_run=false`. It
does not instantiate an engine, read local stores, run synthetic experiments,
submit approvals, execute defenses, or access WARDEN directly. A failed or
unavailable API call is returned as an explicit error object.

## Trust and deployment limits

The default API process has no authentication unless credentials are configured;
bind it to a trusted local/private interface or configure credentials before
exposing it. Request rate limiting is process-local. Place multi-process or
public deployments behind a trusted proxy with shared rate limiting and network
controls. Do not enable forwarded-header trust for untrusted peers. Health and
OpenAPI/docs routes remain public when API authentication is enabled.
