# Changelog

## 1.1.0rc3 — I5 live gate and I8 interface freeze

- Audited WARDEN 0.3.0 routes and Schemas from the pinned public source; verified live OpenAPI and Live execution remain `NOT_RUN` because no service URL is configured. Readiness now reports each missing capability with its distinct blocker.
- Validated WARDEN OpenAPI request/response shapes before mutation; mapped AgentGuard's observation intent to WARDEN's supported `capture_only` mode.
- Added read-only plan listing for ambiguous POST reconciliation. A missing list entry is not proof of absence; uncertain submissions remain ambiguous and are never automatically repeated.
- Added a WARDEN whitebox Probe configuration contract pinned to the upstream JSON Schema; AgentGuard validates configuration only and does not load data, models or run the WARDEN CLI.
- Added pre-registered paired-design fields, exact design fingerprint checks, real-executor gates for five execution arms, and required content-addressed causal-validation references. The synthetic runner remains `synthetic_only`; Evidence Gateway continues to withhold upstream causal claims.
- Extended governed evidence with source/production type, run and experiment identity, model/data version and content hashes for locally verified artifacts; unknown provenance stays explicit.
- Bumped the local candidate to `1.1.0rc3`, exported public JSON Schemas, and added the 86-path OpenAPI snapshot. The existing `rc2` snapshot remains unchanged for history.
- GitHub Actions run [40336215846](https://github.com/hauyer/AgentGuard/actions/runs/40336215846) passed on Python 3.11, 3.12 and 3.13; each ran 508 passed, 6 skipped, 1 warning plus release build/install and synthetic demo checks. Real WARDEN, real model, Probe training, paired intervention and causal-validation runs remain `BLOCKED` / `NOT_RUN`.

## Unreleased — I8 interface freeze and A1–A6 reliability review

- Hardened governed evidence provenance: local content integrity, run/plan ownership, source authenticity, and methodology review remain separate; untrusted upstream causal claims are downgraded, and synthetic/stub or cross-run items are excluded from real WARDEN evidence.
- Added query-only Operator Loop reconciliation. Recovery checks persisted report ownership and known WARDEN IDs; uncertain or incomplete work is never resubmitted and cannot be marked complete without a verified Loop report.
- Added public runtime identity with software/API versions, build commit declaration, canonical OpenAPI SHA-256, and capability states; CI injects the build commit and local builds report `unknown`.
- Made the single-host SQLite/file-lock deployment limit explicit in readiness and deployment docs; extended research provenance with source-tree/input-manifest hashes, independent sample count, technical repetitions, actual model calls, and runtime identity.
- Bumped the package to `1.1.0rc2`, regenerated the 86-path OpenAPI snapshot and public evidence schemas. Real WARDEN execution remains BLOCKED/NOT_RUN.

## Unreleased — D0–D7: real-investigation platform

- D0: CI wheel-install step replaced by `scripts/verify_wheel_install.py` (single-wheel discovery, clean-venv install under the runtime lock, importlib.metadata version check against pyproject, CLI/SDK import verification from outside the checkout); per-environment test records aligned in `docs/release/TEST_REPORT.md`.
- D1: versioned `InvestigationRunConfig` contract and `ObservationRunService` — standardized real WARDEN observation runs with kernel-declared runtime-id resolution, content-addressed config/record artifacts, idempotent fixed-config replay and no blind retry on ambiguous submissions. Live-verified end to end against a real qwen2.5-7B observation run.
- D2: metadata-based risk findings (`RiskFinding`) and falsifiable hypotheses (`RiskHypothesis`) projected from the interaction graph; unsupported detections are recorded as `insufficient_evidence` rather than fabricated; findings mount onto recorded edges via `GET /v1/investigations/{id}/interaction-graph?include_findings=true`.
- D3: evidence-driven multi-round investigation loop (`InvestigationLoopService`) with five strategies (`fixed_order`, `risk_score`, `information_value`, `experience`, `llm`), durable per-round decision records, four hard budgets (actions/time/model-calls/no-new-evidence), deterministic LLM degradation, and plan-only intervention actions. Live-demonstrated with adaptive read-only rounds over a real observation.
- D4: evidence-constrained defense planning (`DefensePlanningService`) — transparent ranking factors with unknown propagation, upper-layer policy expressions, and per-candidate verification plans that stay `not_executed` without a kernel intervention executor.
- D5: controlled strategy comparison (`InvestigationEvaluationService` + `scripts/run_investigation_eval.py`) executed for real on the live stack (5 strategies × 2 repetitions); results, protocol, metrics, baselines, ablation plan and limitations recorded in `docs/research/`.
- D6: security hardening — D-round route auth pinned, forged role headers rejected, prompt-injection output can no longer reach persisted records (free-text model rationale is not stored), loop budgets proven unbreakable, journal consistency and separate model/audit billing tests added.
- D7: full acceptance — 397 tests collected, 0 failures, live-configured suite skips only the torch-optional module; build, clean wheel install and quality gates all pass. New docs: `docs/release/SECURITY_REVIEW.md`, `docs/release/ARCHITECTURE.md` updated, research suite complete. No new REST routes; the published 1.1.0rc1 contract is unchanged.

## Unreleased — D0: CI and release validation repair

- Replaced the CI wheel-install step's hardcoded `agentguard-1.0.0rc1-py3-none-any.whl` with `scripts/verify_wheel_install.py`: it discovers exactly one `agentguard-*.whl` in `dist/`, installs it into a fresh venv under `requirements/runtime.lock`, compares the `importlib.metadata` version with `pyproject.toml`, and verifies the CLI plus SDK import from inside the venv (running from outside the checkout so no source-path leak can pass). Verified locally on Python 3.12 and 3.14.
- Aligned test reporting across environments: the full collection is 346 tests = 342 passed + 4 skipped; GitHub CI and the local suite agree once the junitxml collected total is no longer misread as passed. `docs/release/TEST_REPORT.md` now records GitHub CI, local unit, configured live-WARDEN, real-7B and not-run causal-experiment results in separate rows.
- Updated stale docs to match the verified record: a real observational qwen2.5-7B run has executed (TEST_REPORT §4a); observational success does not lift the causal-intervention or defense-verification blockers, which remain recorded as not run.

## 1.1.0-rc1 — QWB application integration (C0–C8)

- Candidate follow-up (same day): validated the full real chain AgentGuard → WARDEN →
  local qwen2.5-7B observation runtime. The C1 graph now demonstrably projects real
  agent-to-agent message edges from a live kernel run; live tests skip honestly when
  `AGENTGUARD_LIVE_WARDEN_URL` is unset.
- Fixed a real-run crash: `WardenLifecycleService._evidence_refs` preferred WARDEN
  `file://` artifact URIs over artifact ids, so completed real runs failed while
  recording their experience record. Artifact ids now take precedence (matching the
  EvidenceGateway rule) and unsafe references are dropped unopened.
- `GET /v1/investigations/options` now surfaces the runtime identifiers the kernel's
  `agent.runtime` capability metadata declares (`confirmed_runtime_ids`), which the
  plan compiler actually validates against; unavailable runtimes keep the list empty.
- Added stage-10 concept-separation safeguards (8 tests) pinning Observation/Finding/
  Hypothesis/Attribution/Intervention/Verification/Defense-Recommendation/Defense-
  Effectiveness distinctions across the public API.

- Added the message-level interaction graph API `GET /v1/investigations/{request_id}/interaction-graph` (QWB-INT-001). Real edges come only from WARDEN run payload message metadata recorded in the workflow journal; synthetic engine reports are served only under an explicit `run_id` and are always labelled `agentguard_synthetic_report`. Missing metadata returns `available=false` with `GRAPH_SOURCE_UNAVAILABLE` instead of fabricated edges.
- Added the read-only experiment index `GET /v1/experiments`, `GET /v1/experiments/{experiment_id}` and `POST /v1/experiments/compare` (QWB-INT-006) over persisted WARDEN workflows and run-report artifacts. Missing metrics stay `null`, grouping-free runs keep `experiment_id=null`, and comparisons return `not_comparable` with reasons unless scenario, model version, data-source level and shared metrics all check out.
- Added layered readiness `GET /v1/system/readiness` and investigation option discovery `GET /v1/investigations/options` (QWB-INT-005/007) with per-layer blockers, short-TTL cache provenance, and no promotion of WARDEN health into model-runtime, audit or intervention executability; intervention execution stays `unsupported` until a kernel executor is declared.
- Added the durable investigation timeline `GET /v1/investigations/{request_id}/timeline` with `after_event_id` incremental fetch, projected strictly from `warden_workflow_events`; no step is synthesized for unrecorded transitions.
- Extended `GovernedEvidence` with additive artifact capability fields (`metadata_available`, `content_access_supported`, `integrity_verified`, `access_restriction`) and added `GET /v1/reports` as a paginated index of persisted run reports; report reads now rehydrate from the content-addressed store after a service restart.
- Extended `DefensePreview` with additive capability fields (`approval_required_per_candidate`, `execution_supported_overall`, `rollback_supported`, `verification_evidence_available`, `benign_utility_data_available`, `capability_notes`); approval still never implies execution support.
- Fixed the pre-existing public `GET /v1/defense-policies/{request_id}` failure caused by a keyword-only `persist` parameter mismatch with the route adapter.
- Extended the standard-library SDK with `get_readiness`, `get_investigation_options`, `get_investigation_graph`, `get_investigation_timeline`, `list_experiments`, `get_experiment`, `compare_experiments` and `list_reports`.
- Added read-only QWB application-layer contract tests (dashboard, investigations, evidence, defense, reports, experiments, experiences, security) and an OpenAPI additive-only regression against the C0 baseline; full suite 333 passed, 2 skipped (torch-optional whitebox, unconfigured live WARDEN HTTP), plus a uvicorn subprocess live-HTTP integration test.
- Real WARDEN execution, model validation and causal-defense validation remain blocked as recorded in `docs/DEPENDENCY_BLOCKERS.md`; no real causal result is claimed.

## 1.0.0-rc1 — Candidate acceptance

- Repaired CI under the existing Ruff rules: sorted imports, removed unused imports, replaced unsafe broad exception handling with typed handling, and corrected small idioms without disabling checks.
- Recovered meaningful coverage hidden by repeated test-function names; test collection increased from 246 to 255 cases. Exact duplicate cancellation coverage remains represented once.
- Split stable business route declarations into `agentguard.api.public_routes` and the static console into `agentguard.api.console`; existing handlers and route contracts remain unchanged.
- Rejected known hidden-evaluator fields recursively in WARDEN request metadata and sanitized request-validation responses so invalid values are not echoed to clients or persisted in the workflow journal.
- Added a regression contract covering all public and legacy route methods, deprecated Probe endpoints, request schemas, and pagination parameters.
- Full A8 acceptance results and external blockers are recorded in `docs/release/` and `docs/migration/UPPER_LAYER_A8.md`.
- Python 3.11/3.12/3.13 Actions matrix passed with the release build and clean wheel install. Real WARDEN HTTP, model execution, and causal-defense validation remain blocked when no WARDEN URL/executor is configured; no real causal result is claimed.

## Unreleased — Upper-layer platform A7

- Added stable `/v1/system`, `/v1/workflows`, `/v1/investigations`, `/v1/evidence`, `/v1/defense-policies`, and `/v1/reports` business routes while keeping the existing kernel/run paths available.
- Added a standard-library `AgentGuardClient` with origin validation, redirect rejection, bounded JSON responses, typed API failures, request IDs, pagination, and investigation/evidence/report helpers.
- Routed the restricted MCP server through the Public API SDK; removed local engine, WARDEN, compatibility-runner, artifact, and experience-store access. MCP can create only a WARDEN plan with `start_run=false`; approval and execution tools are absent.
- Added `X-Request-ID`, per-process peer rate limiting, explicit-origin CORS, distinct operator-token support, security headers, and SQLite request audit events that omit query strings, bodies, and credentials.
- Audit events are reserved before dispatch; if the existing workflow database cannot reserve an event, the API returns 503 without executing the request. Interrupted requests remain marked incomplete.
- Added API/SDK/MCP boundary and security tests; full suite: 245 passed, 2 skipped. Live WARDEN HTTP, local-model execution and real causal validation remain separate external acceptance items.

## Unreleased — Upper-layer platform A6

- Expanded the existing SQLite experience records with source/run provenance, outcome and benign-utility measurements, execution cost, model/policy versions, reproducibility hash, expiry, and explicit dataset partition.
- Migrated legacy rows into held-out quarantine; enforce one split per case family and keep test/held-out records out of public retrieval and reward selection.
- Require completed WARDEN provenance, verified evidence references, reproducibility, repeated intervention evidence, risk and benign-utility metrics, and current training records before any outcome can become a reward. Public submissions cannot self-assert verified status.
- Added conflict quarantine, expiry filtering, version/evidence/date filters, cost-adjusted Thompson ranking with uncertainty reporting, and immutable recommendation-policy activation/rollback history.
- Added API and CLI policy inspection/rollback; REST policy changes require a configured operator or legacy API token.
- Validated 236 tests passed and 1 unconfigured live WARDEN HTTP test skipped; no real model or defense outcome is inferred.

## Unreleased — Upper-layer platform A5

- Added a governed Evidence Gateway for AgentGuard reports and journaled WARDEN runs, including run/plan identity checks, safe artifact-reference filtering, local content-hash validation, and source/method limitations.
- Kept `synthetic`, `observational`, `interventional`, and `causal_validated` evidence distinct; causal validation now requires explicit repeated, matched-control, negative-control, benign-utility, artifact-integrity, and alternative-explanation checks.
- Added the defense lifecycle from proposal and approval through submission, execution, and pending or completed verification; statistical passes from caller-supplied arrays remain explicitly unverified.
- Ranked defense candidates with disclosed impact, evidence, utility-cost, reversibility, approval, and verified-experience factors; generated candidates remain proposals and synthetic execution remains labeled synthetic.
- Added WARDEN-evidence-based policy proposals persisted in the existing workflow SQLite database; after approval, absent external execution capability is recorded as `unsupported` with no submission or simulated success.
- Added public evidence/lifecycle schemas and targeted API, provenance, filtering, and transition tests.

## Unreleased — Upper-layer platform A2

- Serialized durable workflow execution across local threads/processes and closed SQLite connections deterministically.
- Added journal transition rules, immutable terminal data, business-state projection, durable attempt counters, and observed remote-status events.
- Unified API audit aliases and CLI submit/run behind the existing lifecycle journal; preserved uncertainty for malformed submission responses and checked current defense approval on resume.
- Added local pending cancellation while reporting external cancellation as unsupported; validated 195 tests and a live blocked-workflow replay.

## Unreleased — Upper-layer platform A3

- Moved the five legacy Probe, paired-runner, white-box, FRACTAL-Bench, and StegoPot implementations into an explicit `agentguard.compat` namespace while preserving their 0.9 Python import paths as shims.
- Kept Probe HTTP routes deprecated and clarified that they train or score caller-supplied vectors locally, without invoking WARDEN; current WARDEN REST has no equivalent Probe training/prediction routes.
- Kept attribution as upper-layer statistics, made optional integration exports lazy, and documented compatibility conditions and the actual no-deletion list.

## Unreleased — Upper-layer platform A4

- Added fixed-order, risk-score, information-value, verified-experience, and bounded LLM investigation strategies with evidence-linked decision traces.
- Added role tool/message allowlists; the LLM receives no tools, can only select supplied candidates, and its output is strictly schema-validated before the existing policy gate remains in control.
- Added a candidate-step cap, model-call/output-token budgeting, explicit deterministic fallback reasons, CLI/API strategy selection, and checked-in request/budget schemas.
- Ensured the paired investigation design follows the candidate selected by the active strategy; recorded cases with unknown model usage fall back without making an unbounded advisor call.

## Unreleased — Upper-layer platform A1

- Hardened the existing WARDEN client with credential-safe URL validation, redirect rejection, bounded JSON responses, strict response checks, and optional GET-only retries.
- Checked API compatibility before mutations and removed raw upstream error details from boundary exceptions.
- Added unified diagnostic availability fields with explicit unknown values and a checked-in schema parity test; refreshed the observation request schema.
- Validated against a read-only WARDEN service with eight contracts and an explicitly blocked model runtime.

## Unreleased — Upper-layer platform A0

- Established `feature/agentguard-upper-layer-v1` from the tested current code and adopted read-only boundaries for WARDEN, FRACTAL-Bench, and StegoPot.
- Added a source-only architecture audit with direct-import, legacy-caller, and dependency-cycle reports, plus regression coverage for boundary detection.
- Recorded the current baseline, dependency graph, external blockers, and A0–A8/application acceptance sequence without deleting business modules.

## Unreleased — Real-model observation integration

- Prevent real runtime or paired-executor injection into the AgentGuard API; real audits must use WARDEN. Keep legacy offline interfaces until kernel parity is verified.
- Reconcile a lost run-submission response against WARDEN only when the remote plan identity matches and exactly one run belongs to that plan; never resubmit an ambiguous run.
- Compare the full normalized kernel request during reconciliation and preserve known runs across polling failures, including recovery of older interrupted journal entries.
- Added `observation` mode to WARDEN requests and diagnostics. Local model runs now return `queued` promptly and can be polled through the existing durable AgentGuard workflow.
- Kept completed observation evidence at `causal_claim_status=not_assessed`; no causal claim is inferred from a message trace.
- Recorded a real local 7B model run and its failed aggregate experiment in `docs/migration/OBSERVATION_RUN.md`.

## Unreleased — 1.0.0 migration, stage 0 audit

- Stage 1: added live WARDEN diagnostics, API-version preflight, real `evidence.artifacts` reference extraction, an opt-in HTTP integration test, and defense-in-depth redaction of benchmark truth in plan responses.
- Fixed optional-PyTorch test collection, Windows SQLite connection cleanup, and `/health` version drift; recorded a real blocked-plan HTTP run in `docs/migration/STAGE1_REPORT.md`.
- Imported the supplied 0.9.0 source archive into a new local Git baseline and created `feature/warden-kernel-migration`; the archive contained no original Git history.
- Documented test/CLI/API baselines, direct callers of overlapping modules, WARDEN 0.2.0 source/API compatibility, and an ordered migration with rollback gates in `docs/migration/`.
- No production module was removed or changed. The full suite still has a Windows SQLite cleanup failure; real WARDEN HTTP and model execution remain unverified.

## 0.9.0 — Durable WARDEN workflow journal and recovery

- Added a SQLite-backed workflow journal with canonical request hashes, append-only status events, request snapshots, run/plan snapshots, and durable results.
- Added idempotent workflow replay and HTTP 409 for request-ID reuse with a different payload.
- Added safe recovery from known plan/run IDs; ambiguous plan/run submission failures are marked `submission_uncertain` and are never automatically resubmitted.
- Added workflow inspection endpoints and `agentguard kernel workflow-status` CLI command.
- Wired the workflow journal into the API and CLI lifecycle service; bumped package/API version to 0.9.0.
- Added concurrency, restart persistence, idempotent replay, recovery, and API audit-event tests.
- Validation uses a simulated WARDEN transport; real WARDEN/FRACTAL-Bench/StegoPot execution remains unverified.

## 0.8.0 — WARDEN lifecycle orchestration

- Added `WardenLifecycleService` for health/capability/contract/resource preflight, plan submission, run submission, bounded polling, terminal-state normalization, and evidence-gated experience recording.
- Added `POST /v1/kernel/workflows` and `agentguard kernel run` with bounded wait options.
- Experiences are persisted only for completed runs with explicit evidence references and scenario/risk labels; records remain observational, unverified, inconclusive, and ineligible for automatic recommendation.
- Added idempotent experience IDs per WARDEN run and stricter validation of list-shaped kernel responses.
- Extended the AgentGuard-only audit contract fields without forwarding them to WARDEN's strict `/v1/audit-plans` payload.
- Added lifecycle tests for missing contracts/resources, blocked plans, completed/failed runs, missing evidence, unknown status, approval gates, bounded polling, timeout, and API preflight.
- Bumped AgentGuard package/API version to 0.8.0.

## 0.7.0 — WARDEN kernel boundary and API integration

- Added a single `WardenKernelClient` boundary for WARDEN health, capabilities, Probe/SAE resources, threat contracts, audit plans, and run status.
- Added `/v1/kernel/*` REST endpoints and `agentguard kernel` CLI commands; supports plan-only submissions and preserves WARDEN `blocked`/`failed` states.
- Added strict AgentGuard-side audit request contracts, JSON Schema, example configuration, transport error handling, timeout handling, and injectable transport tests.
- Made the WARDEN URL explicit through `AGENTGUARD_WARDEN_URL`; no local/synthetic fallback is presented as a real causal audit.
- Documented subsystem ownership: WARDEN owns low-level Probe/SAE and causal-audit execution; AgentGuard retains orchestration, policy, approval, experience, and reporting.
- Retained legacy local Probe/attribution/white-box helpers for compatibility and offline baselines; they are explicitly not the authoritative production audit path.
- Bumped AgentGuard package/API version to 0.7.0.

## 0.6.0 — Evidence-gated experience bandit

- Added Beta-Bernoulli Thompson Sampling to rank action types using only verified, explicitly recommendation-enabled interventional experience records.
- Added deterministic seeded rankings, scenario/risk filtering, excluded-record diagnostics, evidence references, and explicit non-execution flags.
- Added typed response contracts and JSON Schemas for bandit action scores and recommendation reports.
- Added CLI `agentguard experience recommend` and REST `GET /v1/experiences/bandit-recommendations`.
- Added tests for evidence gating, reproducibility, context filtering, inconclusive records, and API behavior.
- Documented why within-record repetitions are not counted as independent reward observations.


## 0.5.0 — Research protocol and optional LLM investigation

- Added immutable SQLite-backed study preregistration with canonical SHA-256 registration hashes and integrity checks.
- Added family-level clustered paired bootstrap and family-level sign-flip permutation analysis.
- Added Holm and Benjamini–Hochberg multiple-comparison adjustment.
- Added preregistered endpoint enforcement, deviation reporting, artifact-linked analysis reports, and REST/CLI study workflows.
- Added secret-recovery mediation summaries, separate collusion/benign-utility outcomes, and descriptive path-shift metrics (TV distance, cosine distance, top-k Jaccard, Spearman).
- Added attribution REST/CLI endpoints and four additional JSON Schemas with synthetic examples.
- Added a local PyTorch white-box activation adapter for capture, zeroing, reference activation replacement, and noise patching with token-position validation and audit hashes; tested on a toy model only.
- Added an opt-in OpenAI-compatible LLM investigation advisor (DeepSeek defaults) that can only prioritize an existing finding ID; no tools or execution authority are exposed.
- Added deterministic fallback, sanitized provider errors, HTTPS enforcement, usage-aware audit records, examples, schemas, and tests.
- Explicitly keeps real FRACTAL-Bench/StegoPot paired replay and true multi-agent ablation as unverified follow-up work.


## 0.4.0 — Large development expansion

- Added an interaction graph report and evidence-linked candidate paths; integrated rule, graph and behavioral detectors plus an opt-in structured semantic scoring callback.
- Added a matched control/treatment/negative-control runner for synthetic backends, paired statistics, API budget reservation and artifact-backed execution reports; real backends require a validated paired executor.
- Added a dependency-free binary logistic linear probe, case-family-isolated split utility, AUROC/AUPRC/FPR/Brier metrics, and probe training/prediction API endpoints.
- Added stricter hidden-truth feature rejection, explicit tool-call intervention targets, and read-only policy profile enforcement.
- Added full intervention history in run reports and serialized per-run API writes to protect idempotency and intervention budgets under concurrent requests.
- Added deterministic defense candidate IDs so preview and execution use the same candidate references.
- Added optional API-token support in the web console, CI matrix, reproducible release packager, expanded tests, and updated verification documents.
- Bumped the package/API version to 0.4.0.

## 0.3.0 — Large development increment

- Added nested experiment budget contract and read-only/synthetic-demo policy profiles.
- Added explicit hidden-truth feature rejection for observations.
- Added investigation hypotheses, heuristic action ranking, paired experiment design and seeded repeats.
- Added paired bootstrap confidence intervals and sign-flip permutation tests.
- Added defense preview and paired-array verification contracts; preview does not execute actions.
- Expanded REST API with background jobs, cooperative cancellation, idempotency, observation/finding/hypothesis/timeline/report endpoints, guarded intervention and defense endpoints, artifact content access, and experience memory.
- Added optional API-token middleware and separate operator-approval token for real-backend writes.
- Added SQLite experience memory; unverified records cannot be auto-recommended.
- Added optional bounded MCP tools and a minimal Web console.
- Expanded CLI for JSON/YAML runs, report lookup, adapter checks, artifacts, benchmark comparison and experience search.
- Expanded JSON Schema exports to 24 contracts and added security/statistical/implementation status documentation.
- Added API, cancellation, statistics, experience memory, active investigation, defense, CLI and contract tests.

## 0.2.0

- Added explicit runtime capability manifests and pre-intervention policy gates.
- Added run timeline and budget usage to reports.
- Added SHA-256 content-addressed local artifact storage with integrity checks.
- Added real FRACTAL-Bench scenario catalogue and StegoPot `run_file` integration seams.
- Added deterministic role-coordination trace and initial JSON Schema exports.

## 0.1.0

- Initial contract-first MVP with Mock backend, baseline detector, orchestration, API, CLI, and tests.
