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

List responses return `items`, page `count`, `offset`, `limit` and, where
applicable, `next_offset`. The original endpoint paths and response fields remain
available during migration.

## Python SDK

The SDK uses only the Python standard library, validates the API origin, rejects
redirects, bounds response size, quotes resource identifiers, and exposes
classified `AgentGuardAPIError` failures. It never retries a mutation.

```python
from agentguard.sdk import AgentGuardClient

client = AgentGuardClient.from_env()
health = client.health()
page = client.list_investigations(limit=25, offset=0)
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
