import assert from "node:assert/strict";
import test from "node:test";
import { encodeFunctionData } from "viem";

import type { AgentWalletCliSendResult } from "./agent-wallet-cli.js";
import {
  type AgentWalletDirectBundle,
  type DirectFloorCandidate,
  type DirectFloorContext,
  type DirectFloorReceiptEvidence,
  type DirectFloorRpc,
  evaluateDirectFloor,
  executeAgentWalletDirectBundle,
  parseAgentWalletDirectBundle,
} from "./agent-wallet-direct-floor.js";
import { type BalanceFloorApproval, ERC20_TRANSFER_ABI } from "./delegated-floor-gate.js";
import { canonicalSha256, type ExecutionRequest } from "./pre-execution-gate.js";

const wallet = `0x${"11".repeat(20)}` as const;
const token = `0x${"22".repeat(20)}` as const;
const recipient = `0x${"33".repeat(20)}` as const;
const transactionHash = `0x${"99".repeat(32)}` as const;

function approval(floor = "500"): BalanceFloorApproval {
  const policy = {
    schemaVersion: 1 as const,
    kind: "assetBalanceFloor" as const,
    policyId: "direct-usdc-floor",
    chainId: 11155111,
    walletAddress: wallet,
    tokenAddress: token,
    assetBalanceFloor: floor,
  };
  const proposal = {
    schemaVersion: 1 as const,
    kind: "policy-proposal" as const,
    proposalId: "direct-proposal",
    requestSha256: `0x${"44".repeat(32)}` as const,
    intentText: "USDC를 0.5개 이상 남겨줘",
    compiler: { provider: "google-gemini", model: "gemini-3.5-flash-lite" },
    policy,
    policySha256: canonicalSha256(policy),
    rationales: ["승인된 잔고 하한입니다."],
    assumptions: [],
    unsupportedItems: [],
  };
  const proposalSha256 = canonicalSha256(proposal);
  return {
    schemaVersion: 1,
    kind: "approved-policy-envelope",
    approvalId: "direct-approval",
    approvalScope: "user",
    approvedBy: "owner",
    proposal,
    proposalSha256,
    policySha256: proposal.policySha256,
    confirmation: `APPROVE ${proposalSha256}`,
  };
}

function candidate(inputApproval: BalanceFloorApproval, amount = "100"): DirectFloorCandidate {
  const transferAmount = BigInt(amount);
  return {
    schemaVersion: 1,
    kind: "agent-wallet-direct-floor-candidate",
    candidateId: "direct-transfer-1",
    approvalSha256: canonicalSha256(inputApproval),
    policySha256: inputApproval.policySha256,
    context: {
      chainId: 11155111,
      currentBlockNumber: "100",
      currentBlockHash: `0x${"55".repeat(32)}`,
      senderNonce: "2",
      walletAddress: wallet,
      tokenAddress: token,
      assetBalance: "1000",
    },
    execution: {
      chainId: 11155111,
      fromAddress: wallet,
      toAddress: token,
      value: "0",
      data: encodeFunctionData({
        abi: ERC20_TRANSFER_ABI,
        functionName: "transfer",
        args: [recipient, transferAmount],
      }),
      gas: "70000",
      nonce: "2",
    },
    effect: {
      walletAddress: wallet,
      tokenAddress: token,
      recipientAddress: recipient,
      transferAmount: amount,
      afterAssetBalance: (1000n - transferAmount).toString(),
    },
  };
}

function bundle(floor = "500", amount = "100"): AgentWalletDirectBundle {
  const inputApproval = approval(floor);
  return {
    schemaVersion: 1,
    kind: "agent-wallet-direct-floor-bundle",
    approval: inputApproval,
    candidate: candidate(inputApproval, amount),
  };
}

class FakeRpc implements DirectFloorRpc {
  simulations = 0;
  contextReads = 0;
  receiptChecks = 0;

  constructor(public context: DirectFloorContext) {}

  async simulate(_execution: Readonly<ExecutionRequest>): Promise<void> {
    this.simulations += 1;
  }

  async readContext(_candidate: Readonly<DirectFloorCandidate>): Promise<DirectFloorContext> {
    this.contextReads += 1;
    return structuredClone(this.context);
  }

  async verifyReceipt(
    _sent: Readonly<AgentWalletCliSendResult>,
    input: Readonly<DirectFloorCandidate>,
  ): Promise<DirectFloorReceiptEvidence> {
    this.receiptChecks += 1;
    return {
      transactionHash,
      blockNumber: "102",
      blockHash: `0x${"66".repeat(32)}`,
      receiptStatus: "success",
      assetBalanceAfter: input.effect.afterAssetBalance,
    };
  }
}

test("strict parser accepts an exact direct Agent Wallet transfer bundle", () => {
  assert.equal(parseAgentWalletDirectBundle(bundle()).candidate.kind, "agent-wallet-direct-floor-candidate");
});

test("direct evaluator binds approval, floor, sender, target, calldata, amount and nonce", () => {
  const input = bundle();
  assert.deepEqual(evaluateDirectFloor(input.approval, input.candidate).reasonCodes, []);

  const changed = structuredClone(input.candidate);
  changed.effect.recipientAddress = `0x${"77".repeat(20)}`;
  const result = evaluateDirectFloor(input.approval, changed);
  assert.equal(result.accepted, false);
  assert.ok(result.reasonCodes.includes("DIRECT_TRANSFER_BINDING_MISMATCH"));
});

test("dry preflight allows block advance while nonce and balance remain unchanged", async () => {
  const input = bundle();
  const rpc = new FakeRpc({
    ...input.candidate.context,
    currentBlockNumber: "101",
    currentBlockHash: `0x${"88".repeat(32)}`,
  });
  let sends = 0;
  const result = await executeAgentWalletDirectBundle({
    bundle: input,
    rpc,
    broadcast: false,
    send: async () => {
      sends += 1;
      return { transactionHash, walletAddress: wallet };
    },
  });
  assert.equal(result.kind, "agent-wallet-direct-preflight-result");
  assert.equal(result.eligibleForBroadcast, true);
  assert.equal(rpc.simulations, 1);
  assert.equal(rpc.contextReads, 1);
  assert.equal(sends, 0);
});

test("broadcast sends the exact execution once and verifies the receipt", async () => {
  const input = bundle();
  const rpc = new FakeRpc(input.candidate.context);
  const sent: ExecutionRequest[] = [];
  const result = await executeAgentWalletDirectBundle({
    bundle: input,
    rpc,
    broadcast: true,
    send: async (execution) => {
      sent.push({ ...execution });
      return { transactionHash, walletAddress: wallet };
    },
  });
  assert.equal(result.kind, "agent-wallet-direct-broadcast-result");
  assert.deepEqual(sent, [input.candidate.execution]);
  assert.equal(rpc.receiptChecks, 1);
});

test("floor rejection and state drift both keep the sender at zero calls", async () => {
  let sends = 0;
  const rejected = bundle("901", "100");
  const rejectedRpc = new FakeRpc(rejected.candidate.context);
  await assert.rejects(
    executeAgentWalletDirectBundle({
      bundle: rejected,
      rpc: rejectedRpc,
      broadcast: true,
      send: async () => {
        sends += 1;
        return { transactionHash, walletAddress: wallet };
      },
    }),
    /not eligible for broadcast/,
  );
  assert.equal(rejectedRpc.simulations, 0);

  const accepted = bundle();
  const driftedRpc = new FakeRpc({ ...accepted.candidate.context, senderNonce: "3" });
  await assert.rejects(
    executeAgentWalletDirectBundle({
      bundle: accepted,
      rpc: driftedRpc,
      broadcast: true,
      send: async () => {
        sends += 1;
        return { transactionHash, walletAddress: wallet };
      },
    }),
    /context drifted/,
  );
  assert.equal(driftedRpc.simulations, 1);
  assert.equal(driftedRpc.receiptChecks, 0);
  assert.equal(sends, 0);
});
