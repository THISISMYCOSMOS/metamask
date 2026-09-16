"""Pure, deterministic final-decision evaluator for the approved floor policy.

Every check appends an explicit reason code instead of raising, so a rejection
is a readable record rather than an exception message.  The evaluator never
infers intent and never repairs a mismatch.

It compares the calldata that would actually be broadcast against the transfer
the simulation performed, field for field.  A predicted balance alone would let
a caller substitute a different recipient, amount or gas limit after the
simulation and still look consistent.
"""
from __future__ import annotations

from .canonical import canonical_sha256
from .models import ApprovedPolicyEnvelope, ExecutionCandidate, FinalDecision


ERC20_TRANSFER_SELECTOR = "a9059cbb"


def decode_erc20_transfer(data: str) -> tuple[str, int] | None:
    """Decode ``transfer(address,uint256)`` calldata, or return None if it is not one."""
    payload = data[2:]
    if len(payload) != 8 + 64 + 64 or payload[:8].lower() != ERC20_TRANSFER_SELECTOR:
        return None
    recipient_word = payload[8:72]
    amount_word = payload[72:136]
    if recipient_word[:24] != "0" * 24:
        return None
    try:
        return "0x" + recipient_word[24:].lower(), int(amount_word, 16)
    except ValueError:
        return None


def evaluate(approval: ApprovedPolicyEnvelope, candidate: ExecutionCandidate) -> FinalDecision:
    """Return an explicit reject for every semantic mismatch; never infer intent."""
    reasons: list[str] = []
    policy = approval.proposal.policy
    simulation = candidate.simulation
    transaction = candidate.transaction
    approval_hash = canonical_sha256(approval)

    if candidate.approvalSha256 != approval_hash:
        reasons.append("APPROVAL_HASH_MISMATCH")
    if candidate.policySha256 != approval.policySha256:
        reasons.append("POLICY_HASH_MISMATCH")
    if candidate.context.chainId != policy.chainId:
        reasons.append("POLICY_CHAIN_MISMATCH")
    if candidate.context.walletAddress.lower() != policy.walletAddress.lower():
        reasons.append("POLICY_WALLET_MISMATCH")
    if candidate.context.tokenAddress.lower() != policy.tokenAddress.lower():
        reasons.append("POLICY_TOKEN_MISMATCH")
    if transaction.toAddress.lower() != policy.tokenAddress.lower():
        reasons.append("TRANSACTION_TOKEN_MISMATCH")

    transfer = decode_erc20_transfer(transaction.data)
    if transfer is None:
        reasons.append("INVALID_ERC20_TRANSFER_CALLDATA")
        recipient, amount = None, None
    else:
        recipient, amount = transfer
        if amount == 0:
            reasons.append("ZERO_TRANSFER_AMOUNT")

    # The broadcast transaction must be the transaction that was simulated.
    if simulation.status != "success":
        reasons.append("SIMULATION_NOT_SUCCESSFUL")
    if simulation.chainId != candidate.context.chainId:
        reasons.append("SIMULATION_CHAIN_MISMATCH")
    if simulation.tokenAddress.lower() != transaction.toAddress.lower():
        reasons.append("SIMULATION_TOKEN_MISMATCH")
    if simulation.senderAddress.lower() != transaction.fromAddress.lower():
        reasons.append("SIMULATION_SENDER_MISMATCH")
    if simulation.senderNonce != transaction.nonce:
        reasons.append("SIMULATION_NONCE_MISMATCH")
    if simulation.gasLimit != transaction.gasLimit:
        reasons.append("SIMULATION_GAS_LIMIT_MISMATCH")
    if simulation.senderAddress.lower() == simulation.recipientAddress.lower():
        reasons.append("SIMULATION_SELF_TRANSFER")
    if recipient is not None and recipient.lower() != simulation.recipientAddress.lower():
        reasons.append("SIMULATION_RECIPIENT_MISMATCH")
    if amount is not None and str(amount) != simulation.transferAmount:
        reasons.append("SIMULATION_AMOUNT_MISMATCH")

    # The simulated balances must be self-consistent with that same transfer.
    simulated_amount = int(simulation.transferAmount)
    if simulation.beforeAssetBalance != candidate.context.assetBalance:
        reasons.append("SIMULATION_BEFORE_BALANCE_MISMATCH")
    if int(simulation.beforeAssetBalance) - int(simulation.afterAssetBalance) != simulated_amount:
        reasons.append("SIMULATION_PREDICTED_BALANCE_MISMATCH")
    if int(simulation.afterRecipientBalance) - int(simulation.beforeRecipientBalance) != simulated_amount:
        reasons.append("SIMULATION_RECIPIENT_BALANCE_MISMATCH")

    if int(simulation.afterAssetBalance) < int(policy.assetBalanceFloor):
        reasons.append("ASSET_BALANCE_FLOOR_VIOLATION")

    return FinalDecision(
        schemaVersion=1,
        kind="final-decision",
        candidateId=candidate.candidateId,
        approvalSha256=approval_hash,
        policySha256=approval.policySha256,
        candidateSha256=canonical_sha256(candidate),
        executionSha256=canonical_sha256(transaction),
        historySha256=candidate.historySha256,
        accepted=not reasons,
        reasonCodes=tuple(reasons),
    )
