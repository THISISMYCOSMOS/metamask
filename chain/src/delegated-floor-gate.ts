import { decodeAbiParameters, decodeFunctionData, encodeAbiParameters, encodeFunctionData } from "viem";

import { MODE_CODE_SIMPLE_SINGLE } from "./delegation.js";
import { canonicalJson, canonicalSha256, PreExecutionGateError, type ExecutionRequest } from "./pre-execution-gate.js";

const HASH_PATTERN = /^0x[0-9a-f]{64}$/;

export { MODE_CODE_SIMPLE_SINGLE };
export const ERC20_TRANSFER_ABI = [
  {
    type: "function",
    name: "transfer",
    inputs: [{ name: "to", type: "address" }, { name: "amount", type: "uint256" }],
    outputs: [{ type: "bool" }],
    stateMutability: "nonpayable",
  },
] as const;
export const REDEEM_DELEGATIONS_ABI = [
  {
    type: "function",
    name: "redeemDelegations",
    inputs: [
      { name: "_permissionContexts", type: "bytes[]" },
      { name: "_modes", type: "bytes32[]" },
      { name: "_executionCallDatas", type: "bytes[]" },
    ],
    outputs: [],
    stateMutability: "nonpayable",
  },
] as const;
export const DELEGATION_ARRAY_ABI_TYPE = {
  type: "tuple[]",
  components: [
    { name: "delegate", type: "address" },
    { name: "delegator", type: "address" },
    { name: "authority", type: "bytes32" },
    {
      name: "caveats",
      type: "tuple[]",
      components: [
        { name: "enforcer", type: "address" },
        { name: "terms", type: "bytes" },
        { name: "args", type: "bytes" },
      ],
    },
    { name: "salt", type: "uint256" },
    { name: "signature", type: "bytes" },
  ],
} as const;

export interface BalanceFloorApproval {
  schemaVersion: 1;
  kind: "approved-policy-envelope";
  approvalId: string;
  approvalScope: "user";
  approvedBy: string;
  proposalSha256: `0x${string}`;
  policySha256: `0x${string}`;
  confirmation: string;
  proposal: {
    schemaVersion: 1;
    kind: "policy-proposal";
    policySha256: `0x${string}`;
    policy: {
      schemaVersion: 1;
      kind: "assetBalanceFloor";
      policyId: string;
      chainId: number;
      walletAddress: `0x${string}`;
      tokenAddress: `0x${string}`;
      assetBalanceFloor: string;
    };
    [key: string]: unknown;
  };
}

export interface DelegatedTransferContext {
  chainId: number;
  currentBlockNumber: string;
  currentBlockHash: `0x${string}`;
  delegateNonce: string;
  delegationManagerAddress: `0x${string}`;
  walletAddress: `0x${string}`;
  tokenAddress: `0x${string}`;
  assetBalance: string;
}

export interface DelegatedTransferEffect {
  walletAddress: `0x${string}`;
  tokenAddress: `0x${string}`;
  recipientAddress: `0x${string}`;
  transferAmount: string;
  afterAssetBalance: string;
}

export interface DelegatedFloorCandidate {
  schemaVersion: 1;
  kind: "delegated-floor-candidate";
  candidateId: string;
  approvalSha256: `0x${string}`;
  policySha256: `0x${string}`;
  context: DelegatedTransferContext;
  execution: ExecutionRequest;
  effect: DelegatedTransferEffect;
}

export interface DelegatedFloorDecision {
  schemaVersion: 1;
  kind: "delegated-floor-decision";
  candidateId: string;
  candidateSha256: `0x${string}`;
  executionSha256: `0x${string}`;
  approvalSha256: `0x${string}`;
  policySha256: `0x${string}`;
  accepted: boolean;
  reasonCodes: string[];
}

function uint(value: string): bigint | undefined {
  if (!/^(0|[1-9][0-9]*)$/.test(value)) return undefined;
  try {
    const parsed = BigInt(value);
    return parsed < 1n << 256n ? parsed : undefined;
  } catch {
    return undefined;
  }
}

function sameAddress(a: string, b: string): boolean {
  return a.toLowerCase() === b.toLowerCase();
}

export function evaluateDelegatedFloor(
  approval: BalanceFloorApproval,
  candidate: DelegatedFloorCandidate,
): DelegatedFloorDecision {
  const reasons: string[] = [];
  const proposalHash = canonicalSha256(approval.proposal);
  const approvalHash = canonicalSha256(approval);
  const policy = approval.proposal.policy;
  const policyHash = canonicalSha256(policy);

  if (!HASH_PATTERN.test(approval.proposalSha256) || approval.proposalSha256 !== proposalHash) {
    reasons.push("APPROVAL_PROPOSAL_HASH_MISMATCH");
  }
  if (approval.confirmation !== `APPROVE ${proposalHash}`) reasons.push("APPROVAL_CONFIRMATION_MISMATCH");
  if (approval.policySha256 !== policyHash || approval.proposal.policySha256 !== policyHash) {
    reasons.push("APPROVAL_POLICY_HASH_MISMATCH");
  }
  if (candidate.approvalSha256 !== approvalHash) reasons.push("CANDIDATE_APPROVAL_HASH_MISMATCH");
  if (candidate.policySha256 !== policyHash) reasons.push("CANDIDATE_POLICY_HASH_MISMATCH");

  const before = uint(candidate.context.assetBalance);
  const amount = uint(candidate.effect.transferAmount);
  const after = uint(candidate.effect.afterAssetBalance);
  const floor = uint(policy.assetBalanceFloor);
  if (before === undefined || amount === undefined || after === undefined || floor === undefined) {
    reasons.push("INVALID_UINT256_VALUE");
  } else {
    if (amount === 0n) reasons.push("ZERO_TRANSFER_AMOUNT");
    if (before < amount || before - amount !== after) reasons.push("ASSET_BALANCE_EFFECT_MISMATCH");
    if (after < floor) reasons.push("ASSET_BALANCE_FLOOR_VIOLATION");
  }

  if (candidate.context.chainId !== policy.chainId || candidate.execution.chainId !== policy.chainId) {
    reasons.push("POLICY_CHAIN_MISMATCH");
  }
  if (!sameAddress(candidate.context.walletAddress, policy.walletAddress) ||
      !sameAddress(candidate.effect.walletAddress, policy.walletAddress)) {
    reasons.push("POLICY_WALLET_MISMATCH");
  }
  if (!sameAddress(candidate.context.tokenAddress, policy.tokenAddress) ||
      !sameAddress(candidate.effect.tokenAddress, policy.tokenAddress)) {
    reasons.push("POLICY_TOKEN_MISMATCH");
  }
  if (candidate.execution.nonce !== candidate.context.delegateNonce) reasons.push("DELEGATE_NONCE_MISMATCH");
  if (!sameAddress(candidate.execution.toAddress, candidate.context.delegationManagerAddress)) {
    reasons.push("DELEGATION_MANAGER_MISMATCH");
  }
  if (candidate.execution.value !== "0") reasons.push("NONZERO_NATIVE_VALUE");

  return {
    schemaVersion: 1,
    kind: "delegated-floor-decision",
    candidateId: candidate.candidateId,
    candidateSha256: canonicalSha256(candidate),
    executionSha256: canonicalSha256(candidate.execution),
    approvalSha256: approvalHash,
    policySha256: policyHash,
    accepted: reasons.length === 0,
    reasonCodes: reasons,
  };
}

/**
 * Prove that the exact outer transaction redeems one direct delegation and
 * contains one ERC-7579 single execution matching the simulated ERC-20 effect.
 * Any unsupported delegation chain or calldata shape is rejected.
 */
export function verifyDelegatedExecutionBinding(candidate: DelegatedFloorCandidate): boolean {
  try {
    const decodedOuter = decodeFunctionData({ abi: REDEEM_DELEGATIONS_ABI, data: candidate.execution.data });
    if (decodedOuter.functionName !== "redeemDelegations") return false;
    const [permissionContexts, modes, executionCallDatas] = decodedOuter.args;
    if (permissionContexts.length !== 1 || modes.length !== 1 || executionCallDatas.length !== 1) return false;
    if (modes[0].toLowerCase() !== MODE_CODE_SIMPLE_SINGLE) return false;
    if (
      encodeFunctionData({ abi: REDEEM_DELEGATIONS_ABI, functionName: "redeemDelegations", args: decodedOuter.args }).toLowerCase() !==
      candidate.execution.data.toLowerCase()
    ) return false;

    const [delegations] = decodeAbiParameters([DELEGATION_ARRAY_ABI_TYPE], permissionContexts[0]);
    if (delegations.length !== 1) return false;
    const delegation = delegations[0];
    if (!sameAddress(delegation.delegator, candidate.effect.walletAddress)) return false;
    if (!sameAddress(delegation.delegate, candidate.execution.fromAddress)) return false;
    if (delegation.signature === "0x") return false;
    if (
      encodeAbiParameters([DELEGATION_ARRAY_ABI_TYPE], [delegations]).toLowerCase() !==
      permissionContexts[0].toLowerCase()
    ) return false;

    const packed = executionCallDatas[0].slice(2);
    if (packed.length < 40 + 64 + 8) return false;
    const target = `0x${packed.slice(0, 40)}`;
    const nativeValue = BigInt(`0x${packed.slice(40, 104)}`);
    const innerData = `0x${packed.slice(104)}` as `0x${string}`;
    if (!sameAddress(target, candidate.effect.tokenAddress) || nativeValue !== 0n) return false;

    const decodedTransfer = decodeFunctionData({ abi: ERC20_TRANSFER_ABI, data: innerData });
    if (decodedTransfer.functionName !== "transfer") return false;
    const [recipient, amount] = decodedTransfer.args;
    if (!sameAddress(recipient, candidate.effect.recipientAddress)) return false;
    if (amount.toString() !== candidate.effect.transferAmount) return false;
    return encodeFunctionData({
      abi: ERC20_TRANSFER_ABI,
      functionName: "transfer",
      args: decodedTransfer.args,
    }).toLowerCase() === innerData.toLowerCase();
  } catch {
    return false;
  }
}

export class DelegatedFloorGate {
  private readonly consumed = new Set<string>();

  async execute<T>(options: {
    approval: BalanceFloorApproval;
    candidate: DelegatedFloorCandidate;
    readContext: () => DelegatedTransferContext | Promise<DelegatedTransferContext>;
    send: (execution: Readonly<ExecutionRequest>) => T | Promise<T>;
  }): Promise<{ decision: DelegatedFloorDecision; sendResult: T }> {
    const snapshot = JSON.parse(JSON.stringify(options.candidate)) as DelegatedFloorCandidate;
    const decision = evaluateDelegatedFloor(options.approval, snapshot);
    if (!decision.accepted) {
      throw new PreExecutionGateError(`delegated transaction rejected: ${decision.reasonCodes.join(",")}`);
    }
    if (canonicalJson(await options.readContext()) !== canonicalJson(snapshot.context)) {
      throw new PreExecutionGateError("delegated execution context drifted before broadcast");
    }
    if (!verifyDelegatedExecutionBinding(snapshot)) {
      throw new PreExecutionGateError("delegated execution is not bound to the simulated asset effect");
    }

    const decisionHash = canonicalSha256(decision);
    if (this.consumed.has(decisionHash)) throw new PreExecutionGateError("delegated decision was already consumed");
    this.consumed.add(decisionHash);
    const sendResult = await options.send(Object.freeze({ ...snapshot.execution }));
    return { decision, sendResult };
  }
}
