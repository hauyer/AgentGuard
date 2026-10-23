# Changelog

## Unreleased — 1.0.0 migration, stage 0 audit

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
