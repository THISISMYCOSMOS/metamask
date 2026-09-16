import assert from "node:assert/strict";
import type { SpawnSyncReturns } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import {
  PreExecutionGateError,
  canonicalSha256,
  evaluateBeforeBroadcast,
  sendGuardedTransaction,
  type ExecutionContext,
  type PreExecutionCandidate,
  type PreExecutionDecision,
} from "./pre-execution-gate.js";

const REPO_ROOT = join(import.meta.dirname, "..", "..");

const context: ExecutionContext = {
  chainId: 1,
  currentBlockNumber: "25700019",
  currentBlockHash: `0x${"ab".repeat(32)}`,
  senderNonce: "19",
};

function candidate(): PreExecutionCandidate {
  return {
    schemaVersion: 1,
    kind: "pre-execution-candidate",
    candidateId: "g3-step-020",
    historySha256: `0x${"cd".repeat(32)}`,
    context: { ...context },
    execution: {
      chainId: 1,
      fromAddress: `0x${"11".repeat(20)}`,
      toAddress: `0x${"22".repeat(20)}`,
      value: "0",
      data: "0x1234",
      gas: "3000000",
      nonce: "19",
    },
    portfolioCandidate: { kind: "portfolio-candidate" },
  };
}

function decision(input: PreExecutionCandidate, accepted: boolean): PreExecutionDecision {
  return {
    schemaVersion: 1,
    kind: "pre-execution-decision",
    candidateId: input.candidateId,
    candidateSha256: canonicalSha256(input),
    executionSha256: canonicalSha256(input.execution),
    historySha256: input.historySha256,
    approvalSha256: `0x${"ef".repeat(32)}`,
    policySha256: `0x${"01".repeat(32)}`,
    accepted,
    evaluations: [],
  };
}

test("a rejected candidate never calls the wallet", async () => {
  let sends = 0;
  const input = candidate();
  await assert.rejects(
    sendGuardedTransaction({
      candidate: input,
      evaluate: (value) => decision(value, false),
      readContext: () => context,
      verifyExecutionBinding: () => true,
      send: () => {
        sends += 1;
        return "tx";
      },
    }),
    PreExecutionGateError,
  );
  assert.equal(sends, 0);
});

test("an accepted candidate sends the exact frozen request once", async () => {
  let received: Readonly<PreExecutionCandidate["execution"]> | undefined;
  const input = candidate();
  const result = await sendGuardedTransaction({
    candidate: input,
    evaluate: (value) => decision(value, true),
    readContext: () => context,
    verifyExecutionBinding: () => true,
    send: (execution) => {
      received = execution;
      return "0xtx";
    },
  });
  assert.equal(result, "0xtx");
  assert.deepEqual(received, input.execution);
  assert.equal(Object.isFrozen(received), true);
});

test("block or nonce drift fails closed", async () => {
  let sends = 0;
  const input = candidate();
  await assert.rejects(
    sendGuardedTransaction({
      candidate: input,
      evaluate: (value) => decision(value, true),
      readContext: () => ({ ...context, senderNonce: "20" }),
      verifyExecutionBinding: () => true,
      send: () => {
        sends += 1;
        return "tx";
      },
    }),
    /context drifted/,
  );
  assert.equal(sends, 0);
});

test("a decision for a different execution fails closed", async () => {
  let sends = 0;
  const input = candidate();
  await assert.rejects(
    sendGuardedTransaction({
      candidate: input,
      evaluate: (value) => ({ ...decision(value, true), executionSha256: `0x${"00".repeat(32)}` }),
      readContext: () => context,
      verifyExecutionBinding: () => true,
      send: () => {
        sends += 1;
        return "tx";
      },
    }),
    /execution changed/,
  );
  assert.equal(sends, 0);
});

test("an unproven execution-to-candidate binding never calls the wallet", async () => {
  let sends = 0;
  const input = candidate();
  await assert.rejects(
    sendGuardedTransaction({
      candidate: input,
      evaluate: (value) => decision(value, true),
      readContext: () => context,
      verifyExecutionBinding: () => false,
      send: () => {
        sends += 1;
        return "tx";
      },
    }),
    /not bound/,
  );
  assert.equal(sends, 0);
});

function mockResult(overrides: Partial<SpawnSyncReturns<string>>): SpawnSyncReturns<string> {
  return {
    pid: 1,
    output: [null, "", ""],
    stdout: "",
    stderr: "",
    status: 0,
    signal: null,
    error: undefined,
    ...overrides,
  } as SpawnSyncReturns<string>;
}

test("evaluateBeforeBroadcast rejects invalid JSON from the evaluator", () => {
  const input = candidate();
  assert.throws(
    () =>
      evaluateBeforeBroadcast(input, {
        runner: () => mockResult({ status: 0, stdout: "not json" }),
      }),
    /invalid JSON/,
  );
});

test("evaluateBeforeBroadcast rejects on spawn error (timeout)", () => {
  const input = candidate();
  assert.throws(
    () =>
      evaluateBeforeBroadcast(input, {
        runner: () => mockResult({ error: new Error("ETIMEDOUT") }),
      }),
    /evaluator failed: ETIMEDOUT/,
  );
});

test("evaluateBeforeBroadcast rejects an unexpected exit status", () => {
  const input = candidate();
  assert.throws(
    () =>
      evaluateBeforeBroadcast(input, {
        runner: () => mockResult({ status: 3, stdout: JSON.stringify(decision(input, false)) }),
      }),
    /failed closed/,
  );
});

test("evaluateBeforeBroadcast rejects a status/accepted mismatch", () => {
  const input = candidate();
  assert.throws(
    () =>
      evaluateBeforeBroadcast(input, {
        runner: () => mockResult({ status: 0, stdout: JSON.stringify(decision(input, false)) }),
      }),
    /disagrees with evaluator exit status/,
  );
});

test("evaluateBeforeBroadcast rejects a candidate hash mismatch", () => {
  const input = candidate();
  const tampered = { ...decision(input, true), candidateSha256: `0x${"00".repeat(32)}` as const };
  assert.throws(
    () =>
      evaluateBeforeBroadcast(input, {
        runner: () => mockResult({ status: 0, stdout: JSON.stringify(tampered) }),
      }),
    /candidate hash mismatch/,
  );
});

test("evaluateBeforeBroadcast rejects a candidateId/history link mismatch", () => {
  const input = candidate();
  const tampered = { ...decision(input, true), candidateId: "different-id" };
  assert.throws(
    () =>
      evaluateBeforeBroadcast(input, {
        runner: () => mockResult({ status: 0, stdout: JSON.stringify(tampered) }),
      }),
    /decision links do not match/,
  );
});

test("the TypeScript bridge receives a deterministic Python rejection", () => {
  const portfolioCandidate = JSON.parse(
    readFileSync(join(REPO_ROOT, "traces", "mvp-candidate-reject.json"), "utf8"),
  ) as Record<string, unknown>;
  const input = candidate();
  input.historySha256 = portfolioCandidate.sourceTraceHashedContentSha256 as `0x${string}`;
  input.portfolioCandidate = portfolioCandidate;

  const result = evaluateBeforeBroadcast(input, { timeoutMs: 10_000 });
  assert.equal(result.accepted, false);
  assert.equal(result.candidateSha256, canonicalSha256(input));
  assert.equal(result.executionSha256, canonicalSha256(input.execution));
});
