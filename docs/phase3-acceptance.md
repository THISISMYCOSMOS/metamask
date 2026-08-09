# Phase 3 deterministic evaluator — stress-test acceptance

Updated 2026-08-09. This phase starts with the two invariants directly demonstrated by the
committed G3 trace. It does not claim that invariant synthesis or transaction pre-execution
integration is complete.

## Why evaluator-first

The committed G3 trace is already a reproducible golden negative input. Adding more exploit
traces before a deterministic decision layer would increase evidence without proving the core
project claim: that an approved intent can block a harmful state transition. The next vertical
slice is therefore:

`approved policy JSON -> validated trace -> deterministic integer evaluator -> accept/reject`

The first stress test found that `verifier/models.py` is intentionally Phase 1-specific: it
requires exactly 20 successful steps, a zero final USDC balance, and positive loss. Reusing that
schema as the evaluator itself would make a benign control impossible. The resolution is to keep
the strict Phase 1 evidence validator intact, normalize its validated balances into independent
`PortfolioPoint` values, and keep the invariant functions outcome-neutral. The pure evaluator is
tested with both the G3 negative and a benign continuous control.

## Current acceptance gates

- Policy input is versioned, strict, rejects unknown fields, and bounds numeric strings to
  `uint256`.
- Policy is bound to the exact chain id, fork block number, fork block hash, and trace kind.
- Duplicate invariant ids, zero-length windows, discontinuous state, reordered timestamps or
  blocks, state/step mismatch, and result/step mismatch fail closed.
- Valuation and comparisons use integers only. Bounds are inclusive: value equal to a floor or
  loss equal to a cap passes; one unit beyond fails.
- The policy hash and evaluation report are deterministic and contain no wall-clock field.
- `traceHashedContentSha256` fingerprints the canonical semantic content of the trace's `hashed`
  object. It intentionally excludes `meta` and is distinct from the chain G3 determinism digest.
- Default CLI behavior returns success only for an accepted decision. `--expect reject` exists
  solely for negative-fixture verification.
- The committed `traces/cumulative-loss.json` must fail both `portfolioValueFloor` and
  `cumulativeLossCap`.

## Demonstration policy, not production policy

`specs/phase1-demo-invariants.json` is a reproducible Phase 3 fixture, not a claim about a user's
real risk preference.

- The rolling 24-hour loss cap is the pinned oracle value of the already-fixed 2,000 USDC daily
  limit: `1999535040000000000000` in 1e-18 USD units. Unlike the baseline's fixed period buckets,
  the rolling window also spans a period boundary.
- The portfolio floor is the G3 starting portfolio value minus that same cap:
  `26979251676100000000000` in 1e-18 USD units.

These thresholds were selected from Phase 1 fixed parameters to exercise the evaluator. A later
synthesis phase must produce candidate values from natural-language intent and obtain explicit
user approval; it must not silently promote this demo fixture to a production policy.

## Verification commands

From the repository root:

```powershell
uv run --cache-dir tmp\uv-cache --project verifier python verifier\validate_trace.py traces\cumulative-loss.json --quiet
uv run --cache-dir tmp\uv-cache --project verifier python -m unittest discover -s verifier -p 'test_*.py' -v
uv run --cache-dir tmp\uv-cache --project verifier python verifier\evaluate_invariants.py `
  specs\phase1-demo-invariants.json traces\cumulative-loss.json --expect reject
```

## Not yet claimed

- A generic candidate-transaction trace schema emitted by `chain/` before broadcast.
- Arbitrary-asset rounding semantics. This Phase 1 adapter accepts only valuations that divide
  exactly at the 1e-18 USD scale; conservative start/end rounding must be designed with the
  generic trace contract rather than silently introduced here.
- Time-varying market valuation. The current G3 adapter intentionally applies the pinned fork
  block oracle snapshot to every synthetic step, so it does not detect later price-driven loss.
- Pre-trace history anchoring or persistent rolling state. This slice evaluates only the history
  present in the supplied trace; transaction-time integration must supply the preceding window.
- The remaining `executionPriceBand`, `netDeltaBound`, and `venueIntegrity` evaluators.
- Natural-language synthesis, policy review/approval UI, on-chain enforcement, or production
  latency/throughput measurements.
