# MVP scope

Updated 2026-08-09. The MVP proves one narrow end-to-end claim:

> An explicitly approved portfolio policy can deterministically reject a harmful candidate state
> that the existing Delegation Framework baseline allows.

## In scope

- CLI and JSON artifacts only; no product UI.
- The pinned Ethereum fork and the existing Phase 1 G3 evidence.
- A versioned candidate portfolio-value series in 1e-18 USD units.
- Complete history coverage for the policy's rolling window.
- `portfolioValueFloor` and `cumulativeLossCap` only.
- Strict schema validation, exact fork binding, deterministic hashes, and fail-closed exit codes.
- One G3-derived reject fixture and one synthetic benign accept control.
- As the final MVP slice, constrained natural-language intent compilation into the same policy
  schema, with explicit approval remaining separate from synthesis.

## Out of scope

- `executionPriceBand`, `netDeltaBound`, and `venueIntegrity`.
- Arbitrary assets, conservative rounding across token decimal combinations, or time-varying
  oracle valuation.
- A persistent production history database, live wallet integration, UI, on-chain enforcement,
  hosted services, throughput tuning, or additional exploit classes.
- Treating LLM output as approved policy or allowing an LLM to participate in runtime decisions.

## Candidate-slice acceptance

- A deterministic adapter derives the final candidate plus a complete rolling-window history
  from the already-validated G3 trace.
- The candidate evaluator rejects that artifact with both MVP invariants.
- A benign candidate at the inclusive limits is accepted.
- The final candidate state is judged; prior history supplies rolling-loss anchors but a
  restorative candidate is not rejected solely because an earlier state was below the floor.
- Missing window history, fork mismatch, discontinuity, reordered transitions, unknown fields,
  and content mutation fail closed.
- Default CLI behavior exits successfully only for `accepted=true`.

This candidate slice is necessary for the MVP because the Phase 1 trace model is an attack-proof
artifact, not a reusable transaction decision contract. Generalizing beyond the value-series
contract above is explicitly deferred.

`specs/mvp-candidate-invariants.json` remains a demo fixture derived from fixed Phase 1 parameters.
It is not evidence of a real user's approval. The MVP is complete only after the final synthesis
slice keeps proposed policy generation and explicit approval as separate steps.
