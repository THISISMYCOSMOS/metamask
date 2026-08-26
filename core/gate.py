"""The only Core path that may call a sender after a final deterministic decision.

Scope limit, stated plainly: the one-shot property below is **process-local**.
``_consumed_decisions`` lives in one ``ExecutionGate`` instance in one Python
process.  A second process, a restarted process, or any caller that reaches the
wallet or RPC endpoint without going through this gate is not covered.  This is
a companion guard around a caller-injected executor seam -- it is bypassable by
construction and is not Agent Wallet native enforcement, nor a global
exactly-once guarantee.
"""
from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import TypeVar

from .canonical import canonical_sha256
from .evaluator import evaluate
from .models import ApprovedPolicyEnvelope, ChainContext, Erc20TransferTransaction, ExecutionCandidate, FinalDecision


T = TypeVar("T")


class GateRejected(RuntimeError):
    pass


class ExecutionGate:
    """Fail closed on drift and consume an accepted decision before calling send.

    A send exception still consumes the decision: a caller cannot safely know
    whether the wallet/provider accepted the transaction after an ambiguous error.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._consumed_decisions: set[str] = set()

    def execute(
        self,
        approval: ApprovedPolicyEnvelope,
        candidate: ExecutionCandidate,
        *,
        read_context: Callable[[], ChainContext],
        send: Callable[[Erc20TransferTransaction], T],
    ) -> tuple[FinalDecision, T]:
        decision = evaluate(approval, candidate)
        if decision.candidateSha256 != canonical_sha256(candidate):
            raise GateRejected("candidate hash mismatch")
        if decision.executionSha256 != canonical_sha256(candidate.transaction):
            raise GateRejected("execution hash mismatch")
        if decision.historySha256 != candidate.historySha256:
            raise GateRejected("history hash mismatch")
        if not decision.accepted:
            raise GateRejected("transaction rejected: " + ",".join(decision.reasonCodes))

        current_context = read_context()
        if current_context != candidate.context:
            raise GateRejected("chain context drifted before send")

        decision_hash = canonical_sha256(decision)
        with self._lock:
            if decision_hash in self._consumed_decisions:
                raise GateRejected("accepted decision was already consumed")
            self._consumed_decisions.add(decision_hash)

        return decision, send(candidate.transaction)
