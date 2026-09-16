import assert from "node:assert/strict";
import test from "node:test";

import {
  buildAgentWalletTransactionPayload,
  createAgentWalletCliSender,
  type AgentWalletCliRunner,
} from "./agent-wallet-cli.js";
import { PreExecutionGateError, type ExecutionRequest } from "./pre-execution-gate.js";

const wallet = `0x${"11".repeat(20)}` as const;
const manager = `0x${"22".repeat(20)}` as const;
const transactionHash = `0x${"66".repeat(32)}` as const;

function execution(overrides: Partial<ExecutionRequest> = {}): ExecutionRequest {
  return {
    chainId: 11155111,
    fromAddress: wallet,
    toAddress: manager,
    value: "0",
    data: "0x1234",
    gas: "65000",
    nonce: "7",
    ...overrides,
  };
}

test("builds the exact raw transaction payload expected by Agent Wallet", () => {
  assert.deepEqual(buildAgentWalletTransactionPayload(execution()), {
    to: manager,
    value: "0x0",
    data: "0x1234",
    gas: "0xfde8",
    nonce: "0x7",
  });
});

test("resolves the active wallet before sending the exact payload once", async () => {
  const calls: Array<{ command: string; args: readonly string[] }> = [];
  const runner: AgentWalletCliRunner = async (command, args) => {
    calls.push({ command, args });
    if (args[1] === "address") return { stdout: JSON.stringify({ address: wallet }), stderr: "" };
    return { stdout: JSON.stringify({ transactionHash }), stderr: "" };
  };
  const sender = createAgentWalletCliSender({ executable: "mm-test", runner, intent: "Approved transfer" });
  const result = await sender(execution());

  assert.deepEqual(result, { transactionHash, walletAddress: wallet });
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[0].args, ["wallet", "address"]);
  assert.deepEqual(calls[1].args.slice(0, 4), ["wallet", "send-transaction", "--chain-id", "11155111"]);
  assert.equal(calls[1].args.includes("--wait"), true);
  assert.equal(calls[1].args.includes("--intent"), true);
  const payloadIndex = calls[1].args.indexOf("--payload");
  assert.deepEqual(JSON.parse(calls[1].args[payloadIndex + 1]), buildAgentWalletTransactionPayload(execution()));
});

test("wallet mismatch blocks before the transaction command", async () => {
  let calls = 0;
  const runner: AgentWalletCliRunner = async () => {
    calls += 1;
    return { stdout: `0x${"99".repeat(20)}`, stderr: "" };
  };
  const sender = createAgentWalletCliSender({ runner });
  await assert.rejects(sender(execution()), /does not match/);
  assert.equal(calls, 1);
});

test("missing or ambiguous transaction hash fails closed", async () => {
  const outputs = [JSON.stringify({ address: wallet }), JSON.stringify({ status: "pending" })];
  const runner: AgentWalletCliRunner = async () => ({ stdout: outputs.shift() ?? "", stderr: "" });
  const sender = createAgentWalletCliSender({ runner });
  await assert.rejects(sender(execution()), /confirmed transaction hash/);
});

test("invalid quantities and calldata are rejected before invoking the CLI", async () => {
  const runner: AgentWalletCliRunner = async () => {
    throw new Error("must not run");
  };
  assert.throws(() => buildAgentWalletTransactionPayload(execution({ gas: "01" })), PreExecutionGateError);
  assert.throws(() => buildAgentWalletTransactionPayload(execution({ data: "0x123" })), PreExecutionGateError);
  const sender = createAgentWalletCliSender({ runner });
  await assert.rejects(sender(execution({ fromAddress: "0x1234" })), PreExecutionGateError);
});
