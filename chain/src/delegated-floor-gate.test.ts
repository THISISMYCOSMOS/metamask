import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { decodeAbiParameters, decodeFunctionData, encodeAbiParameters, encodeFunctionData } from "viem";

import {
  DELEGATION_ARRAY_ABI_TYPE,
  DelegatedFloorGate,
  ERC20_TRANSFER_ABI,
  MODE_CODE_SIMPLE_SINGLE,
  REDEEM_DELEGATIONS_ABI,
  evaluateDelegatedFloor,
  verifyDelegatedExecutionBinding,
  type BalanceFloorApproval,
  type DelegatedFloorCandidate,
} from "./delegated-floor-gate.js";
import { encodeSingleExecution } from "./delegation.js";
import { createAgentWalletCliSender, type AgentWalletCliRunner } from "./agent-wallet-cli.js";
import { canonicalSha256 } from "./pre-execution-gate.js";

const wallet = `0x${"11".repeat(20)}` as const;
const token = `0x${"22".repeat(20)}` as const;
const delegate = `0x${"33".repeat(20)}` as const;
const manager = `0x${"44".repeat(20)}` as const;
const recipient = `0x${"77".repeat(20)}` as const;

interface G3TraceFixture {
  hashed: {
    fork: { chainId: number };
    baseline: {
      delegationManager: `0x${string}`;
      delegator: `0x${string}`;
      delegate: `0x${string}`;
      counterparty: `0x${string}`;
      caveats: { enforcer: `0x${string}`; terms: `0x${string}` }[];
    };
    delegation: {
      authority: `0x${string}`;
      salt: string;
      signature: `0x${string}`;
    };
    steps: {
      blockNumber: string;
      transferAmount: string;
      usdcBefore: string;
      usdcAfter: string;
    }[];
  };
}

function delegatedTransferData(amount = 100n, to = recipient): `0x${string}` {
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
  const transferData = encodeFunctionData({ abi: ERC20_TRANSFER_ABI, functionName: "transfer", args: [to, amount] });
  const executionCallData = encodeSingleExecution(token, 0n, transferData);
  return encodeFunctionData({
    abi: REDEEM_DELEGATIONS_ABI,
    functionName: "redeemDelegations",
    args: [[permissionContext], [MODE_CODE_SIMPLE_SINGLE], [executionCallData]],
  });
}

function approval(
  floor = "900",
  binding: {
    chainId: number;
    walletAddress: `0x${string}`;
    tokenAddress: `0x${string}`;
  } = { chainId: 1, walletAddress: wallet, tokenAddress: token },
): BalanceFloorApproval {
  const policy = {
    schemaVersion: 1 as const,
    kind: "assetBalanceFloor" as const,
    policyId: "usdc-floor",
    chainId: binding.chainId,
    walletAddress: binding.walletAddress,
    tokenAddress: binding.tokenAddress,
    assetBalanceFloor: floor,
  };
  const proposal = {
    schemaVersion: 1 as const,
    kind: "policy-proposal" as const,
    proposalId: "proposal-1",
    requestSha256: `0x${"55".repeat(32)}`,
    intentText: "USDC를 900 base-units 이상 남겨줘",
    compiler: { provider: "google-gemini", model: "gemini-3.5-flash-lite" },
    policy,
    policySha256: canonicalSha256(policy),
    rationales: ["잔고 하한"],
    assumptions: [],
    unsupportedItems: [],
  };
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

function candidate(inputApproval = approval()): DelegatedFloorCandidate {
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

test("inclusive floor accepts and binds the outer delegated execution", () => {
  const inputApproval = approval();
  const input = candidate(inputApproval);
  const decision = evaluateDelegatedFloor(inputApproval, input);
  assert.equal(decision.accepted, true);
  assert.equal(decision.candidateSha256, canonicalSha256(input));
  assert.equal(decision.executionSha256, canonicalSha256(input.execution));
  assert.equal(verifyDelegatedExecutionBinding(input), true);
});

test("committed G3 signed delegation binds all six caveats into the product calldata", () => {
  const tracePath = new URL("../../traces/cumulative-loss.json", import.meta.url);
  const trace = JSON.parse(readFileSync(tracePath, "utf8")) as G3TraceFixture;
  const { baseline, delegation, fork, steps } = trace.hashed;
  const step = steps[0];
  const tokenAddress = baseline.caveats[0].terms;

  assert.equal(baseline.caveats.length, 6);
  assert.equal((delegation.signature.length - 2) / 2, 65);

  const permissionContext = encodeAbiParameters(
    [DELEGATION_ARRAY_ABI_TYPE],
    [[{
      delegate: baseline.delegate,
      delegator: baseline.delegator,
      authority: delegation.authority,
      caveats: baseline.caveats.map((caveat) => ({ ...caveat, args: "0x" as const })),
      salt: BigInt(delegation.salt),
      signature: delegation.signature,
    }]],
  );
  const transferData = encodeFunctionData({
    abi: ERC20_TRANSFER_ABI,
    functionName: "transfer",
    args: [baseline.counterparty, BigInt(step.transferAmount)],
  });
  const executionData = encodeFunctionData({
    abi: REDEEM_DELEGATIONS_ABI,
    functionName: "redeemDelegations",
    args: [[permissionContext], [MODE_CODE_SIMPLE_SINGLE], [encodeSingleExecution(tokenAddress, 0n, transferData)]],
  });
  const decodedExecution = decodeFunctionData({ abi: REDEEM_DELEGATIONS_ABI, data: executionData });
  assert.equal(decodedExecution.functionName, "redeemDelegations");
  const [decodedDelegations] = decodeAbiParameters(
    [DELEGATION_ARRAY_ABI_TYPE],
    decodedExecution.args[0][0],
  );
  const decodedDelegation = decodedDelegations[0];
  assert.equal(decodedDelegation.signature, delegation.signature);
  assert.deepEqual(
    decodedDelegation.caveats.map((caveat) => [caveat.enforcer.toLowerCase(), caveat.terms.toLowerCase()]),
    baseline.caveats.map((caveat) => [caveat.enforcer.toLowerCase(), caveat.terms.toLowerCase()]),
  );
  const inputApproval = approval(step.usdcAfter, {
    chainId: fork.chainId,
    walletAddress: baseline.delegator,
    tokenAddress,
  });
  const input: DelegatedFloorCandidate = {
    schemaVersion: 1,
    kind: "delegated-floor-candidate",
    candidateId: "g3-step-1-signed-delegation",
    approvalSha256: canonicalSha256(inputApproval),
    policySha256: inputApproval.policySha256,
    context: {
      chainId: fork.chainId,
      currentBlockNumber: step.blockNumber,
      currentBlockHash: `0x${"66".repeat(32)}`,
      delegateNonce: "0",
      delegationManagerAddress: baseline.delegationManager,
      walletAddress: baseline.delegator,
      tokenAddress,
      assetBalance: step.usdcBefore,
    },
    execution: {
      chainId: fork.chainId,
      fromAddress: baseline.delegate,
      toAddress: baseline.delegationManager,
      value: "0",
      data: executionData,
      gas: "3000000",
      nonce: "0",
    },
    effect: {
      walletAddress: baseline.delegator,
      tokenAddress,
      recipientAddress: baseline.counterparty,
      transferAmount: step.transferAmount,
      afterAssetBalance: step.usdcAfter,
    },
  };

  const decision = evaluateDelegatedFloor(inputApproval, input);
  assert.equal(decision.accepted, true);
  assert.equal(verifyDelegatedExecutionBinding(input), true);
});

test("accepted delegated execution reaches the MetaMask Agent Wallet CLI sender", async () => {
  const inputApproval = approval();
  const input = candidate(inputApproval);
  const calls: readonly string[][] = [];
  const mutableCalls = calls as string[][];
  const runner: AgentWalletCliRunner = async (_command, args) => {
    mutableCalls.push([...args]);
    if (args[1] === "address") return { stdout: JSON.stringify({ address: delegate }), stderr: "" };
    return { stdout: JSON.stringify({ transactionHash: `0x${"99".repeat(32)}` }), stderr: "" };
  };
  const gate = new DelegatedFloorGate();
  const result = await gate.execute({
    approval: inputApproval,
    candidate: input,
    readContext: () => input.context,
    send: createAgentWalletCliSender({ executable: "mm-test", runner }),
  });

  assert.equal(result.decision.accepted, true);
  assert.equal(result.sendResult.walletAddress, delegate);
  assert.equal(result.sendResult.transactionHash, `0x${"99".repeat(32)}`);
  assert.equal(calls.length, 2);
  assert.equal(calls[1][0], "wallet");
  assert.equal(calls[1][1], "send-transaction");
});

test("one unit below the floor rejects", () => {
  const inputApproval = approval("901");
  const decision = evaluateDelegatedFloor(inputApproval, candidate(inputApproval));
  assert.equal(decision.accepted, false);
  assert.deepEqual(decision.reasonCodes, ["ASSET_BALANCE_FLOOR_VIOLATION"]);
});

test("forged effect, policy binding and approval confirmation reject", () => {
  const inputApproval = approval();
  const forged = candidate(inputApproval);
  forged.effect.afterAssetBalance = "950";
  forged.effect.tokenAddress = `0x${"88".repeat(20)}`;
  forged.execution.nonce = "8";
  const brokenApproval = { ...inputApproval, confirmation: "APPROVE 0x00" };
  const decision = evaluateDelegatedFloor(brokenApproval, forged);
  assert.equal(decision.accepted, false);
  assert.ok(decision.reasonCodes.includes("APPROVAL_CONFIRMATION_MISMATCH"));
  assert.ok(decision.reasonCodes.includes("ASSET_BALANCE_EFFECT_MISMATCH"));
  assert.ok(decision.reasonCodes.includes("POLICY_TOKEN_MISMATCH"));
  assert.ok(decision.reasonCodes.includes("DELEGATE_NONCE_MISMATCH"));
});

test("rejection and malformed execution binding call send zero times", async () => {
  let sends = 0;
  const rejectedApproval = approval("901");
  await assert.rejects(
    new DelegatedFloorGate().execute({
      approval: rejectedApproval,
      candidate: candidate(rejectedApproval),
      readContext: () => candidate(rejectedApproval).context,
      send: () => ++sends,
    }),
    /rejected/,
  );
  assert.equal(sends, 0);

  const acceptedApproval = approval();
  const acceptedCandidate = candidate(acceptedApproval);
  acceptedCandidate.execution.data = "0x1234";
  await assert.rejects(
    new DelegatedFloorGate().execute({
      approval: acceptedApproval,
      candidate: acceptedCandidate,
      readContext: () => acceptedCandidate.context,
      send: () => ++sends,
    }),
    /not bound/,
  );
  assert.equal(sends, 0);
});

test("calldata recipient, amount, delegate and manager substitution fail closed", async () => {
  const inputApproval = approval();
  const mutations: DelegatedFloorCandidate[] = [];

  const changedRecipient = candidate(inputApproval);
  changedRecipient.execution.data = delegatedTransferData(100n, `0x${"88".repeat(20)}`);
  mutations.push(changedRecipient);

  const changedAmount = candidate(inputApproval);
  changedAmount.execution.data = delegatedTransferData(101n);
  mutations.push(changedAmount);

  const changedDelegate = candidate(inputApproval);
  changedDelegate.execution.fromAddress = `0x${"99".repeat(20)}`;
  mutations.push(changedDelegate);

  const changedManager = candidate(inputApproval);
  changedManager.execution.toAddress = `0x${"aa".repeat(20)}`;
  mutations.push(changedManager);

  for (const mutation of mutations) {
    let sends = 0;
    await assert.rejects(
      new DelegatedFloorGate().execute({
        approval: inputApproval,
        candidate: mutation,
        readContext: () => mutation.context,
        send: () => ++sends,
      }),
    );
    assert.equal(sends, 0);
  }
});

test("context drift calls send zero times", async () => {
  const inputApproval = approval();
  const input = candidate(inputApproval);
  let sends = 0;
  await assert.rejects(
    new DelegatedFloorGate().execute({
      approval: inputApproval,
      candidate: input,
      readContext: () => ({ ...input.context, delegateNonce: "8" }),
      send: () => ++sends,
    }),
    /context drifted/,
  );
  assert.equal(sends, 0);
});

test("accepted exact execution sends once and cannot be replayed in the same gate", async () => {
  const inputApproval = approval();
  const input = candidate(inputApproval);
  const gate = new DelegatedFloorGate();
  const sent: unknown[] = [];
  const options = {
    approval: inputApproval,
    candidate: input,
    readContext: () => input.context,
    send: (execution: unknown) => {
      sent.push(execution);
      return "0xtx";
    },
  };
  const outcome = await gate.execute(options);
  assert.equal(outcome.sendResult, "0xtx");
  assert.deepEqual(sent, [input.execution]);
  assert.equal(Object.isFrozen(sent[0]), true);
  await assert.rejects(gate.execute(options), /already consumed/);
  assert.equal(sent.length, 1);
});
