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
  research\data\compiler_cases.json tmp\gemini-predictions.jsonl

python -m research.evaluate_compiler `
  research\data\compiler_cases.json tmp\gemini-predictions.jsonl
```

Actual live metrics must not be claimed until the Gemini run completes. The
unit tests use injected provider responses and prove contracts only.
