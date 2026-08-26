# MVP scope

> Historical boundary: this file records the offline MVP fixed on 2026-08-10. It is not the
> current product-status document. The live Gemini, user-revision, browser MetaMask, and Agent
> Wallet execution boundaries are documented in the repository `README.md`.

Updated 2026-08-10. The MVP proves one narrow end-to-end claim:

> An explicitly approved portfolio policy can deterministically reject a harmful candidate state
> that the existing Delegation Framework baseline allows.

## In scope

- CLI and JSON artifacts plus a local review UI; no live provider or wallet integration.
- The pinned Ethereum fork and the existing Phase 1 G3 evidence.
- A versioned candidate portfolio-value series in 1e-18 USD units.
- Complete history coverage for the policy's rolling window.
- `portfolioValueFloor`, `portfolioDrawdownCapBps`, `cumulativeLossCap`, and
  `cumulativeLossCapBps` only.
- Strict schema validation, exact fork binding, deterministic hashes, and fail-closed exit codes.
- One G3-derived reject fixture and one synthetic benign accept control.
- As the final MVP slice, constrained natural-language intent compilation into the same policy
  schema, with explicit approval remaining separate from synthesis.
- A local black/orange review UI for intent request creation, strict offline-response validation,
  exact proposal inspection, exact proposal-hash approval, and deterministic evaluation of the
  committed G3-derived candidate. Arbitrary free-text intents still stop at `request-created` until
  a request-bound provider response exists.
- A structured, JSON-backed condition editor limited to the four canonical invariant kinds
  (`portfolioValueFloor`, `portfolioDrawdownCapBps`, `cumulativeLossCap`, `cumulativeLossCapBps`).
  Users edit values and pick kinds through form fields, never raw JSON. Submitting structured
  conditions compiles a fresh unapproved proposal locally and deterministically
  (`compilerSource=local-structured-editor`), with no live LLM call. The original free-text intent
  is preserved on the proposal for review only; an explicit assumption states that the structured
  fields, not inferred language, are authoritative.

## Out of scope

- `executionPriceBand`, `netDeltaBound`, and `venueIntegrity`.
- Arbitrary assets, conservative rounding across token decimal combinations, or time-varying
  oracle valuation.
- A persistent production history database, live wallet integration, on-chain enforcement,
  hosted services, throughput tuning, or additional exploit classes.
- Exact transaction construction, live-fork transaction simulation, transaction-to-candidate
  binding, guarded wallet broadcast, and transaction receipt claims.
- Cryptographic approver identity, signatures, multi-user authorization, or remote approval
  storage. `approvedBy` is a local audit label, not an authenticated identity.
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
It is not evidence of a real user's approval. The synthesis slice below completes the code MVP by
keeping proposal generation and explicit approval as separate steps. A user-scoped approval is
generated only after the user supplies the exact proposal-hash confirmation and is stored
separately from the test fixture.

## Synthesis and approval slice

The provider-neutral compiler boundary is now implemented as four immutable artifact types:

`intent-compiler-request -> llm-policy-response -> policy-proposal -> policy-approval`

- The LLM response is bound to the request hash, exact fork, allowed invariant kinds, canonical
  invariant order, and matching rationale ids.
- `allowedInvariants` is a duplicate-free canonical subset. Requests containing a percentage
  invariant use a separate compiler rule set that limits output to that subset, encodes percentages
  as integer BPS strings (`20%` is `"2000"`, range `0` through `10000`), and requires an explicit
  `referenceValue1e18` for `portfolioDrawdownCapBps` without inferring missing conditions.
- The response becomes an unapproved proposal. It cannot be used by the approved runtime path.
- Approval requires the exact phrase `APPROVE <proposalSha256>` and embeds the proposal so policy
  or intent mutation invalidates the approval.
- Runtime evaluation accepts `approvalScope=user` by default. The committed offline fixture uses
  `approvalScope=test-fixture` and requires an explicit test-only flag.
- No provider credentials or network calls are built into the compiler boundary. A provider sends
  back the strict response JSON; malformed or expanded output is rejected.

The offline response demonstrates and tests the contract but is not evidence of a live provider
call. Likewise, the committed test approval is not a user's approval. On 2026-08-10 the user
supplied the exact confirmation for proposal:

`0xed84bfa046632e62ab288ec91328897e10b1b856346749862eda835de7149b21`

The resulting `specs/mvp-user-policy-approval.json` has approval hash:

`0x6bc7ec4fccbd7478aa597b83b41cb91161f3483bc2a6fe66f3f8311c44e77828`

Semantic faithfulness of free-form language is not asserted automatically: the proposal exposes
the original intent, thresholds, rationales, and assumptions for explicit review. Runtime remains
fully deterministic and never invokes an LLM. `approvalScope=user` records exact-hash CLI
confirmation but does not cryptographically authenticate the person supplying it.

## Local UI boundary

Editing the intent text or the structured conditions always replaces the in-memory policy flow
state outright: any existing approval and candidate evaluation is discarded, and fresh
`requestSha256`/`proposalSha256` values are computed from the new content. Approval again requires
the exact phrase `APPROVE <new proposalSha256>`; a stale hash from a prior proposal is rejected.

After exact-hash approval, the local UI evaluates the committed G3-derived candidate through the
same deterministic approved-policy evaluator used by the CLI. It displays `accepted`, each
invariant's `passed` result, observed values, limits, and evidence. This is the final MVP result.

`accepted` describes only the supplied portfolio candidate. The UI keeps broadcast eligibility
false because no exact transaction request, execution-context snapshot, live simulation, or
transaction-to-candidate binding is present. It must not be described as MetaMask rejection,
wallet enforcement, transaction success, or transaction failure.
