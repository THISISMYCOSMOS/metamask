import assert from "node:assert/strict";
import test from "node:test";
import { encodeAbiParameters, encodeFunctionData } from "viem";

import {
  type AgentWalletExecutionBundle,
  type AgentWalletReceiptEvidence,
  type AgentWalletRuntimeRpc,
  executeAgentWalletBundle,
  parseAgentWalletExecutionBundle,
} from "./agent-wallet-runtime.js";
import type { AgentWalletCliSendResult } from "./agent-wallet-cli.js";
import {
  DELEGATION_ARRAY_ABI_TYPE,
  ERC20_TRANSFER_ABI,
  MODE_CODE_SIMPLE_SINGLE,
  REDEEM_DELEGATIONS_ABI,
  type BalanceFloorApproval,
  type DelegatedFloorCandidate,
  type DelegatedTransferContext,
} from "./delegated-floor-gate.js";
import { encodeSingleExecution } from "./delegation.js";
import { canonicalSha256, type ExecutionRequest } from "./pre-execution-gate.js";

const wallet = `0x${"11".repeat(20)}` as const;
const token = `0x${"22".repeat(20)}` as const;
const delegate = `0x${"33".repeat(20)}` as const;
const manager = `0x${"44".repeat(20)}` as const;
const recipient = `0x${"77".repeat(20)}` as const;
const transactionHash = `0x${"99".repeat(32)}` as const;

function delegatedTransferData(): `0x${string}` {
  const permissionContext = encodeAbiParameters(
    [DELEGATION_ARRAY_ABI_TYPE],
    [[{
      delegate,
      delegator: wallet,
      authority: `0x${"00".repeat(32)}`,
      caveats: [],
      salt: 1n,
      signature: "0x12",
    }]],
  );
  const transferData = encodeFunctionData({
    abi: ERC20_TRANSFER_ABI,
    functionName: "transfer",
    args: [recipient, 100n],
  });
  return encodeFunctionData({
    abi: REDEEM_DELEGATIONS_ABI,
    functionName: "redeemDelegations",
    args: [[permissionContext], [MODE_CODE_SIMPLE_SINGLE], [encodeSingleExecution(token, 0n, transferData)]],
  });
}

function approval(floor = "900", revised = false): BalanceFloorApproval {
  const policy = {
    schemaVersion: 1 as const,
    kind: "assetBalanceFloor" as const,
    policyId: "usdc-floor",
    chainId: 1,
    walletAddress: wallet,
    tokenAddress: token,
    assetBalanceFloor: floor,
  };
  const base = {
    schemaVersion: 1 as const,
    proposalId: "proposal-1",
    requestSha256: `0x${"55".repeat(32)}` as const,
    intentText: "USDC를 900 base-units 이상 남겨줘",
    compiler: { provider: "google-gemini", model: "gemini-3.5-flash-lite" },
    policy,
    policySha256: canonicalSha256(policy),
    rationales: [revised ? "사용자가 잔고 하한을 수정했습니다." : "Gemini가 잔고 하한을 제안했습니다."],
    assumptions: [],
    unsupportedItems: [],
  };
  const proposal = revised
    ? {
        ...base,
        kind: "revised-policy-proposal" as const,
        revision: {
          schemaVersion: 1 as const,
          kind: "user-policy-revision" as const,
          sourceProposalSha256: `0x${"88".repeat(32)}` as const,
          revisedBy: "owner",
          assetBalanceFloorBefore: "800",
          assetBalanceFloorAfter: floor,
        },
      }
    : { ...base, kind: "policy-proposal" as const };
  const proposalSha256 = canonicalSha256(proposal);
  return {
    schemaVersion: 1,
    kind: "approved-policy-envelope",
    approvalId: "approval-1",
    approvalScope: "user",
    approvedBy: "owner",
    proposal,
    proposalSha256,
    policySha256: proposal.policySha256,
    confirmation: `APPROVE ${proposalSha256}`,
  };
}

function candidate(inputApproval: BalanceFloorApproval): DelegatedFloorCandidate {
  return {
    schemaVersion: 1,
    kind: "delegated-floor-candidate",
    candidateId: "delegated-transfer-1",
    approvalSha256: canonicalSha256(inputApproval),
    policySha256: inputApproval.policySha256,
    context: {
      chainId: 1,
      currentBlockNumber: "25700001",
      currentBlockHash: `0x${"66".repeat(32)}`,
      delegateNonce: "7",
      delegationManagerAddress: manager,
      walletAddress: wallet,
      tokenAddress: token,
      assetBalance: "1000",
    },
    execution: {
      chainId: 1,
      fromAddress: delegate,
      toAddress: manager,
      value: "0",
      data: delegatedTransferData(),
      gas: "3000000",
      nonce: "7",
    },
    effect: {
      walletAddress: wallet,
      tokenAddress: token,
      recipientAddress: recipient,
      transferAmount: "100",
      afterAssetBalance: "900",
    },
  };
}

function bundle(floor = "900", revised = false): AgentWalletExecutionBundle {
  const inputApproval = approval(floor, revised);
  return {
    schemaVersion: 1,
    kind: "agent-wallet-execution-bundle",
    approval: inputApproval,
    candidate: candidate(inputApproval),
  };
}

class FakeRpc implements AgentWalletRuntimeRpc {
  simulations = 0;
  contextReads = 0;
  receiptChecks = 0;
  context: DelegatedTransferContext;

  constructor(context: DelegatedTransferContext) {
    this.context = structuredClone(context);
  }

  async simulate(_execution: Readonly<ExecutionRequest>): Promise<void> {
    this.simulations += 1;
  }

  async readContext(_candidate: Readonly<DelegatedFloorCandidate>): Promise<DelegatedTransferContext> {
    this.contextReads += 1;
    return structuredClone(this.context);
  }

  async verifyReceipt(
    _sent: Readonly<AgentWalletCliSendResult>,
    input: Readonly<DelegatedFloorCandidate>,
  ): Promise<AgentWalletReceiptEvidence> {
    this.receiptChecks += 1;
    return {
      transactionHash,
      blockNumber: "25700002",
      blockHash: `0x${"aa".repeat(32)}`,
      receiptStatus: "success",
      assetBalanceAfter: input.effect.afterAssetBalance,
    };
  }
}

test("strict bundle parser accepts original and user-revised approved proposals", () => {
  assert.equal(parseAgentWalletExecutionBundle(bundle()).approval.proposal.kind, "policy-proposal");
  assert.equal(parseAgentWalletExecutionBundle(bundle("900", true)).approval.proposal.kind, "revised-policy-proposal");
});

test("strict bundle parser rejects unknown fields before any RPC operation", () => {
  const input = bundle() as unknown as Record<string, unknown>;
  input.untrusted = true;
  assert.throws(() => parseAgentWalletExecutionBundle(input), /unknown fields/);
});

test("dry preflight simulates and rechecks context without calling the sender", async () => {
  const input = bundle();
  const rpc = new FakeRpc(input.candidate.context);
  let sends = 0;
  const result = await executeAgentWalletBundle({
    bundle: input,
    rpc,
    broadcast: false,
    send: async () => {
      sends += 1;
      return { transactionHash, walletAddress: delegate };
    },
  });
  assert.equal(result.kind, "agent-wallet-preflight-result");
  assert.equal(result.eligibleForBroadcast, true);
  assert.equal(rpc.simulations, 1);
  assert.equal(rpc.contextReads, 1);
  assert.equal(rpc.receiptChecks, 0);
  assert.equal(sends, 0);
});

test("broadcast sends once only after simulation and exact context then verifies receipt", async () => {
  const input = bundle("900", true);
  const rpc = new FakeRpc(input.candidate.context);
  const sent: ExecutionRequest[] = [];
  const result = await executeAgentWalletBundle({
    bundle: input,
    rpc,
    broadcast: true,
    send: async (execution) => {
      sent.push({ ...execution });
      return { transactionHash, walletAddress: delegate };
    },
  });
  assert.equal(result.kind, "agent-wallet-broadcast-result");
  assert.equal(result.transactionHash, transactionHash);
  assert.deepEqual(sent, [input.candidate.execution]);
  assert.equal(rpc.simulations, 1);
  assert.equal(rpc.contextReads, 1);
  assert.equal(rpc.receiptChecks, 1);
});

test("rejected policy and context drift both call the sender zero times", async () => {
  const rejected = bundle("901");
  const rejectedRpc = new FakeRpc(rejected.candidate.context);
  let sends = 0;
  await assert.rejects(
    executeAgentWalletBundle({
      bundle: rejected,
      rpc: rejectedRpc,
      broadcast: true,
      send: async () => {
        sends += 1;
        return { transactionHash, walletAddress: delegate };
      },
    }),
    /not eligible for broadcast/,
  );
  assert.equal(rejectedRpc.simulations, 0);

  const accepted = bundle();
  const driftedRpc = new FakeRpc({ ...accepted.candidate.context, delegateNonce: "8" });
  await assert.rejects(
    executeAgentWalletBundle({
      bundle: accepted,
      rpc: driftedRpc,
      broadcast: true,
      send: async () => {
        sends += 1;
        return { transactionHash, walletAddress: delegate };
      },
    }),
    /context drifted/,
  );
  assert.equal(driftedRpc.simulations, 1);
  assert.equal(driftedRpc.receiptChecks, 0);
  assert.equal(sends, 0);
});
