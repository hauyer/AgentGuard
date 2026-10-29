# Stego research remediation API v2

This is an additive local contract. `openapi-stego-sandbox-v2.json` publishes 97 paths and 111 operations. The original 102 operations and their component schemas remain unchanged; the compatibility tests compare complete JSON shapes. No database migration or historical artifact rewrite is required.

- `POST /v1/studies/{study_id}/replicate-batches`: an operator-authenticated, idempotent submission with a client-generated replicate_id. The immutable manifest freezes workflow membership before arm submissions. Retry with the same intent; create a new replicate explicitly. Failed submissions remain in that batch.
- `GET /v1/studies/{study_id}/comparisons/batches/{batch_id}`: compare only frozen workflow members. No earliest-run selection, retry substitution, or pooling across batches. Incomplete arms retain their planned denominators.
- `GET /v1/studies/{study_id}/submission-intents/{intent_key}`: read the durable submission journal without starting a remote Run.
- `GET /v1/studies/{study_id}/arms/{arm_id}/execution-contract`: fetch the execution contract from the WARDEN stage and verify registered method/version/target/parameters/data/splits/model/seed. For contextual protocol v3, submission is blocked if these cannot be verified. A browser preview cannot override the frozen target.

`warden.research-run/2` remains historical evidence. `warden.research-run/3` adds explicit Receiver attempts, strict decoding, carrier validity, failure reasons, independent versus same-path collaborative utility, cost, training identity and optional batch/replicate identity. Controlled-collaborative/3 additionally requires distinct actual model input hashes across train, validation and heldout cases, and a recomputable inverted binary-readout diagnostic. Public artifacts contain outcomes and hashes; restricted raw generations and activations remain local to WARDEN.

Graph projections use actual message events only. Probe observations form an overlay, not an additional communication edge. Passing the validation-frozen label threshold means target-label decodability, not a calibrated probability of collusion. Literal replacement with no matching token is not applied replacement. Statistical completion, baseline validity, observed behavior differences and causal mechanism validation are separate fields.

Comparisons reuse family-clustered paired bootstrap/sign-flip inference and Holm adjustment. Seeds, splits, model and tokenizer revisions, weights, executor and training identities must agree within a batch. Twenty synthetic public records under one fixed codebook are a narrow engineering pilot, not independent covert mechanisms or confirmatory evidence. A raw SRR of zero may be a reversible label flip and is not an adaptive-secrecy claim.

The App BFF adds project ownership and CSRF checks to these routes. Existing M/S studies remain readable and are never silently relabeled as new v3 studies. Remote CI is not claimed for local commits.
