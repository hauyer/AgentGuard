# OpenAPI 1.1.0rc4 → 1.1.0rc5

## Snapshot

| Item | rc4 | rc5 |
| --- | --- | --- |
| API version | `1.1.0rc4` | `1.1.0rc5` |
| OpenAPI | `3.1.0` | `3.1.0` |
| Paths / operations | 87 / 99 | 87 / 99 |
| Paths added or removed | — | none |
| OpenAPI file SHA-256 (UTF-8, LF) | `f2b802fd7928bf2701bfbc65e4cf655c8f801a465fe6dd853f938e9d8f75b978` | `605a9cd95f0edeedd4d7235fe46a4a6d8c99ffeb4aab00e2e7f51b2addc2a6b7` |
| Canonical runtime SHA-256 | `09b21ab3cd1e95dd3073142e4274b387ffb43689437be3fa60ad0939403f7894` | `b7722098b9adac87386a62325b19240e5c7024f704d7024a38ad3a2fb6ca8acd` |

Only four operations changed: `POST /v1/studies`, `GET /v1/studies`,
`POST /v1/studies/{study_id}/analyses`, and
`GET /v1/studies/{study_id}/analyses`. The 33 operation objects pinned by QWB-App
remain identical to the checked-in compatibility fixture.

## Contract changes

- Study registration now captures `dataset_version`, WARDEN contract/runtime/mode and
  Probe/SAE identifiers, capture targets, approval requirements, and an optional
  `supersedes_study_id`. `live_steganography` registrations validate the required frozen
  dataset and execution metadata.
- Registration and analysis writes accept an optional `Idempotency-Key`. Replaying the
  same key and normalized request returns the original resource; reusing a key with a
  different request returns `409 IDEMPOTENCY_CONFLICT`. The key and resource mapping are
  stored in the existing research SQLite registry. No automatic client retry was added.
- Study listing supports `limit`, `offset`, `track`, and allow-listed `sort_by` /
  `sort_order`. Analysis history supports `limit`, `offset`, `evidence_mode`, and
  `sort_order`.
- Both write operations document global-authentication and route-level authorization
  failures (`401` / `403`), idempotency conflicts (`409`), declared `Content-Length` values
  over 16 MiB (`413`), and validation/rate/storage errors.
- Analysis adds `script_replay` as a distinct evidence mode. `measured` input is rejected
  with `422 ARTIFACT_CONTENT_NOT_VERIFIED` until artifact bytes and source can be verified.
  RQ3 adds cost metrics with units and explicit planned/completed/failed/blocked/not-run
  pair counts; absent cost or execution counts remain unknown instead of being treated
  as zero.

Changed schemas: `RegisteredStudy`, `ResearchCapabilityItem`, `StudyAnalysisReport`,
`StudyAnalysisRequest`, `StudyArmAnalysisInput`, `StudyRegistrationRequest`, and
`WardenAuditRequest`. No path was added for direct WARDEN research execution. Live remains
blocked under the fixed WARDEN contract; see
[`I5_WARDEN_RESEARCH_GATE_2026-10-28.md`](I5_WARDEN_RESEARCH_GATE_2026-10-28.md).
