"""RQ2/RQ3 program: compile, bind, approve, and decide a simulated candidate."""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "verifier"
if str(VERIFIER) not in sys.path:
    sys.path.insert(0, str(VERIFIER))

from candidate_models import CandidateTrace  # noqa: E402
from evaluate_approved_candidate import evaluate_approved_candidate  # noqa: E402
from invariant_models import ForkBinding, InvariantPolicy  # noqa: E402
from synthesis_models import (  # noqa: E402
    CompilerIdentity,
    InvariantRationale,
    LlmPolicyResponse,
    PolicyProposal,
    canonical_model_sha256,
)
from synthesis_workflow import (  # noqa: E402
    approve_policy_proposal,
    compile_policy_proposal,
    create_intent_request,
)

from .models import BenchmarkCase, CompilationOutput


class ProgramRejected(ValueError):
    """The intent cannot become an approvable deterministic policy."""


def _bound_invariant(case: BenchmarkCase, output: CompilationOutput) -> dict[str, str]:
    compiled = output.invariant
    if not output.supported or compiled is None:
        raise ProgramRejected("compiler did not produce an approvable invariant")
    data: dict[str, str] = {"id": f"{case.caseId}-invariant", "kind": compiled.kind}
    if compiled.kind == "portfolioValueFloor":
        data["floorValue1e18"] = compiled.floorValue1e18  # type: ignore[assignment]
    elif compiled.kind == "portfolioDrawdownCapBps":
        reference = case.context.currentPortfolioValue1e18
        if reference is None:
            raise ProgramRejected("caller-owned current portfolio value is required for drawdown")
        data["referenceValue1e18"] = reference
        data["maxDrawdownBps"] = compiled.maxDrawdownBps  # type: ignore[assignment]
    elif compiled.kind == "cumulativeLossCap":
        data["windowSeconds"] = compiled.windowSeconds  # type: ignore[assignment]
        data["maxLossValue1e18"] = compiled.maxLossValue1e18  # type: ignore[assignment]
    elif compiled.kind == "cumulativeLossCapBps":
        data["windowSeconds"] = compiled.windowSeconds  # type: ignore[assignment]
        data["maxLossBps"] = compiled.maxLossBps  # type: ignore[assignment]
    return data


def build_proposal(
    case: BenchmarkCase,
    output: CompilationOutput,
    *,
    fork: ForkBinding,
    compiler_provider: str,
    compiler_model: str,
) -> PolicyProposal:
    """Attach only caller-owned IDs/fork/reference data to provider output."""
    invariant = _bound_invariant(case, output)
    policy = InvariantPolicy.model_validate(
        {
            "schemaVersion": 1,
            "policyId": f"{case.caseId}-policy",
            "traceKind": "portfolio-candidate",
            "fork": fork.model_dump(mode="json"),
            "invariants": [invariant],
        }
    )
    request = create_intent_request(
        request_id=f"{case.caseId}-request",
        intent_text=case.intentText,
        policy_template=policy,
    )
    response = LlmPolicyResponse(
        schemaVersion=1,
        kind="llm-policy-response",
        requestSha256=canonical_model_sha256(request),
        compiler=CompilerIdentity(provider=compiler_provider, model=compiler_model),
        policy=policy,
        rationales=[
            InvariantRationale(
                invariantId=policy.invariants[0].id,
                summary=" ".join(output.rationales),
            )
        ],
        assumptions=output.assumptions,
    )
    return compile_policy_proposal(request, response)


def approve_and_decide(
    proposal: PolicyProposal,
    *,
    confirmation: str,
    candidate: CandidateTrace,
    approved_by: str = "user",
) -> dict:
    """Require the exact proposal hash, then evaluate the simulated post-state."""
    approval = approve_policy_proposal(
        proposal,
        confirmation=confirmation,
        approved_by=approved_by,
        approval_scope="user",
    )
    return evaluate_approved_candidate(approval, candidate)
