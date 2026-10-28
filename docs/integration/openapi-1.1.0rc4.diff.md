# OpenAPI 1.1.0rc3 → 1.1.0rc4

Generated from the checked-in OpenAPI snapshots after recursively comparing JSON values.

| Check | rc3 | rc4 |
|---|---:|---:|
| OpenAPI version | `3.1.0` | `3.1.0` |
| Package/API version | `1.1.0rc3` | `1.1.0rc4` |
| Paths | 86 | 87 |
| Added paths | — | `/v1/research/capabilities` |
| Removed paths | — | none |
| Added operations | — | `GET /v1/research/capabilities` |
| Removed operations | — | none |
| Changed existing operations | — | `POST /v1/studies`; `POST /v1/studies/{study_id}/analyses` |
| Changed existing schemas | — | `StudyRegistrationRequest`, `RegisteredStudy`, `StudyAnalysisRequest`, `StudyAnalysisReport` |

The two Study write operations now declare operator/API/approval headers and clear `401`, `403`, `409` or analysis `400`/`404` responses. Optional preregistration metadata adds track, protocol/data/model revision, metric definitions, probe coordinates and planned arms. Analysis adds held-out Probe/effect association and multi-arm results. Reads and existing App-supported operations retain their former operation objects.

The pinned QWB-App `63fe1b5662df93fbf06750ecf09c14ee9e6bd5cc` still supports its original 33 operations. `tests/test_qwb_pinned_openapi_compat.py` compares all 33 runtime operation objects—including parameters, request bodies, responses, auth headers and error shapes—to the vendored exact source snapshot; the comparison is 33/33 with no differences.

The new capability endpoint performs read-only WARDEN diagnostics. It does not submit plans or runs. In WARDEN 0.3.0 the proposed research operations remain unsupported; a future capability declaration without a reviewed versioned REST operation remains `SUPPORTED_BUT_BLOCKED`.

OpenAPI file SHA-256 (UTF-8/LF): `f2b802fd7928bf2701bfbc65e4cf655c8f801a465fe6dd853f938e9d8f75b978`. Canonical runtime OpenAPI SHA-256: `09b21ab3cd1e95dd3073142e4274b387ffb43689437be3fa60ad0939403f7894`.
