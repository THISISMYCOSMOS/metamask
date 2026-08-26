# Backend policy proposal compiler

This is the production backend path for Korean natural-language policy proposals.
The default provider is the Gemini Developer API free tier, using structured
JSON output with `gemini-3.5-flash-lite`. It intentionally has no fixture or
local compiler fallback.

`GEMINI_API_KEY` must be configured; `GEMINI_MODEL` defaults to
`gemini-3.5-flash-lite`. Missing configuration, provider/network failure,
blocked or incomplete output, and malformed responses all fail closed without
creating a proposal. Google states that free-tier content may be used to
improve its products, so do not submit secrets or private wallet metadata.

The optional Anthropic adapter remains available through
`PolicyProposalService.from_anthropic_env`, but it is not the default path.

## Shared Backend/Core contract

Backend and Core use the same strict models from `core/models.py`. The compiler
returns a `CompilationResult`; a fully supported request contains exactly one
`PolicyProposal` and its canonical SHA-256:

```json
{
  "schemaVersion": 1,
  "kind": "policy-compilation-result",
  "requestId": "request-1",
  "requestSha256": "0x...",
  "supported": true,
  "proposal": {
    "schemaVersion": 1,
    "kind": "policy-proposal",
    "proposalId": "proposal-1",
    "requestSha256": "0x...",
    "intentText": "USDC를 20개 이상 남겨줘",
    "compiler": {"provider": "google-gemini", "model": "gemini-3.5-flash-lite"},
    "policy": {
      "schemaVersion": 1,
      "kind": "assetBalanceFloor",
      "policyId": "usdc-floor",
      "chainId": 31337,
      "walletAddress": "0x...",
      "tokenAddress": "0x...",
      "assetBalanceFloor": "20000000"
    },
    "policySha256": "0x...",
    "rationales": ["..."],
    "assumptions": ["..."],
    "unsupportedItems": []
  },
  "proposalSha256": "0x...",
  "rationales": ["..."],
  "assumptions": ["..."],
  "unsupportedItems": [],
  "reasonCodes": []
}
```

`proposalSha256` is SHA-256 of canonical UTF-8 JSON (recursive keys sorted,
compact separators, no hash field included). Approval accepts only the exact
string `APPROVE <proposalSha256>` and returns the shared Core
`ApprovedPolicyEnvelope`.

Only the ERC-20 `assetBalanceFloor` policy is supported. The caller supplies
authoritative chain, wallet, token, decimal and identifier fields in
`CompileRequest`; the model can only derive the base-unit floor and report
rationales, assumptions and unsupported intent.

## Approval and execution handoff

`PolicyProposalService.approve` delegates to `core/policy_binding.py`. Supplying
the original `CompileRequest` re-verifies every caller-owned field and the
request hash before recording the exact-hash approval. Unsupported or mixed
intent never receives a proposal, so it cannot reach approval.

There is no conversion, second proposal or second approval between Backend and
Core. `core/execution_service.py` consumes that same envelope, builds a candidate
from restored Anvil simulation evidence, evaluates it deterministically and
calls the injected sender only after the final gate accepts it.

## Tests

Run from the repository root with an available Python 3.11+ interpreter:

```powershell
uv run --cache-dir tmp\uv-cache --project verifier python -m unittest `
  backend.test_policy_compiler core.test_core core.test_integration `
  core.test_rpc_simulator -v
```

The tests use injected transports and never call Gemini or Anthropic.
