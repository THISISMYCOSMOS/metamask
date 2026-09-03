# Research program

This directory is the RQ2/RQ3 research surface. It is separate from the local
wallet execution UI so compiler accuracy is never confused with transaction
success.

## Program boundary

The four-invariant path is:

`Korean intent -> actual Gemini structured output -> strict local contract ->`
`caller-owned fork/reference binding -> unapproved proposal -> exact hash approval ->`
`deterministic evaluation of a simulator-produced post-state candidate`

The provider can choose only one of `portfolioValueFloor`,
`portfolioDrawdownCapBps`, `cumulativeLossCap`, or `cumulativeLossCapBps` and
their threshold/window values. It cannot choose IDs, fork facts, approval, or
the drawdown reference portfolio value. Unsupported, ambiguous, and mixed
requests produce no proposal.

The fixed benchmark is `data/compiler_cases.json`: 60 cases, with 12 cases for
each invariant kind and 12 unsupported/ambiguous controls. It is a compiler
benchmark, not an on-chain transaction dataset.

## Two-step live use

Use module execution from the repository root. The compile step calls Gemini
and prints the exact approval sentence; the decide step requires that sentence.

```powershell
python -m research.run_program compile `
  research\data\compiler_cases.json pvf-001 `
  traces\mvp-candidate-reject.json tmp\research-proposal.json

python -m research.run_program decide `
  tmp\research-proposal.json traces\mvp-candidate-reject.json `
  "APPROVE 0x..."
```

`compile` requires `GEMINI_API_KEY` and has no fixture fallback. A rejected
decision exits with code 2; invalid input/provider/approval exits with code 1.

For a 60-case live run, write predictions to a new file and score them:

```powershell
python -m research.run_gemini_benchmark `
  research\data\compiler_cases.json tmp\gemini-predictions.jsonl `
  --manifest tmp\gemini-run-manifest.json

python -m research.evaluate_compiler `
  research\data\compiler_cases.json tmp\gemini-predictions.jsonl
```

The current fixed run has 58 strict provider outputs and two fail-closed local
contract failures. Its operational metrics across all 60 cases are support
accuracy 0.9667, precision 1.0, recall 0.9583, false-positive rate 0.0 and
exact-invariant accuracy 0.9667. This is one fixed-dataset run, not a general
low-false-positive result. Because strict provider-output coverage is 58/60,
the manifest status and strict `metrics` remain incomplete/null.

The runner records a dataset hash, system-prompt hash, request/configuration
hash per case, returned model version when available, token-count metadata and
per-case failure state. It appends only strict valid predictions and resumes
missing or failed cases without repeating completed calls. A different dataset,
requested model or system prompt requires `--overwrite` so results from unlike
runs cannot be silently mixed.

When a provider response fails the strict local contract, no proposal is
created. `operationalMetrics` scores that fail-closed system result as
non-approvable while `metrics` remains `null` until all 60 provider outputs are
strictly valid. Keep those two result types distinct in the paper.

## Research evidence bundles

The committed evidence set is under `research/evidence/`. All four cases use
the same strict bundle schema:

- `offline-g3-reject`
- `offline-benign-accept`
- `live-floor-accept`
- `live-floor-preflight-reject`

The bundle's deterministic `payload` is bound by `payloadSha256`. Wall-clock
generation metadata is outside that hash. Every embedded artifact has its own
canonical SHA-256 binding, and every bundle explicitly records whether a
broadcast was attempted and whether wallet-native enforcement was used.

Rebuild the counterfactual benign control, source records and bundles from the
repository root:

```powershell
uv run --cache-dir tmp\uv-cache --project verifier python verifier\build_benign_candidate_fixture.py `
  specs\mvp-candidate-invariants.json traces\cumulative-loss.json `
  traces\mvp-candidate-accept.json traces\mvp-candidate-accept-source.json --overwrite

uv run --cache-dir tmp\uv-cache --project verifier python -m research.build_evidence_sources `
  offline offline-g3-reject specs\mvp-user-policy-approval.json `
  traces\mvp-candidate-reject.json research\evidence\sources\offline-g3-reject.source.json --overwrite

uv run --cache-dir tmp\uv-cache --project verifier python -m research.build_evidence_sources `
  offline offline-benign-accept specs\mvp-user-policy-approval.json `
  traces\mvp-candidate-accept.json research\evidence\sources\offline-benign-accept.source.json `
  --provenance traces\mvp-candidate-accept-source.json --overwrite

uv run --cache-dir tmp\uv-cache --project verifier python -B research\capture_agent_wallet_direct_accept.py `
  research\evidence\agent-wallet\direct-floor-bundle.json `
  research\evidence\agent-wallet\direct-floor-runtime-result.json `
  research\evidence\sources\live-floor-accept.source.json `
  --polling-id 3fef4086-d8a4-4ccb-845e-83ceaf4b9035 --overwrite

$names = @('offline-g3-reject','offline-benign-accept','live-floor-accept','live-floor-preflight-reject')
foreach ($name in $names) {
  uv run --cache-dir tmp\uv-cache --project verifier python -m verifier.export_evidence_bundle `
    "research\evidence\sources\$name.source.json" `
    "research\evidence\bundles\$name.bundle.json" --overwrite
}
```

`mvp-candidate-accept.json` is a constructed counterfactual, not a transaction
observed in G3. Its provenance file records exactly which pinned G3 facts are
reused and which values are recomputed. The historical Sepolia record remains
under `inputs/` as supplementary provenance, but the current live accept bundle
is the fresh Agent Wallet transaction with its original off-chain bindings.

To refresh the live no-broadcast rejection, load a configured Gemini key and
run `python -m research.capture_live_floor_reject ...`. The capture performs
read-only Sepolia RPC calls and a live Gemini compile, but contains no send
operation. Its output must show `walletRequest: null`, `txHash: null` and
`broadcastAttempted: false`.
