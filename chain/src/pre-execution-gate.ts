import { spawnSync, type SpawnSyncReturns } from "node:child_process";
import { createHash } from "node:crypto";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const HASH_PATTERN = /^0x[0-9a-f]{64}$/;

export interface ExecutionRequest {
  chainId: number;
  fromAddress: `0x${string}`;
  toAddress: `0x${string}`;
  value: string;
  data: `0x${string}`;
  gas: string;
  nonce: string;
}

export interface ExecutionContext {
  chainId: number;
  currentBlockNumber: string;
  currentBlockHash: `0x${string}`;
  senderNonce: string;
}

export interface PreExecutionCandidate {
  schemaVersion: 1;
  kind: "pre-execution-candidate";
  candidateId: string;
  historySha256: `0x${string}`;
  context: ExecutionContext;
  execution: ExecutionRequest;
  portfolioCandidate: Record<string, unknown>;
}

export interface PreExecutionDecision {
  schemaVersion: 1;
  kind: "pre-execution-decision";
  candidateId: string;
  candidateSha256: `0x${string}`;
  executionSha256: `0x${string}`;
  historySha256: `0x${string}`;
  approvalSha256: `0x${string}`;
  policySha256: `0x${string}`;
  accepted: boolean;
  evaluations: unknown[];
}

type Runner = (
  command: string,
  args: readonly string[],
  options: Parameters<typeof spawnSync>[2],
) => SpawnSyncReturns<string>;

export class PreExecutionGateError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PreExecutionGateError";
  }
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    const result: Record<string, unknown> = {};
    for (const key of Object.keys(value as Record<string, unknown>).sort()) {
      result[key] = canonical((value as Record<string, unknown>)[key]);
    }
    return result;
  }
  return value;
}

export function canonicalJson(value: unknown): string {
  return JSON.stringify(canonical(value));
}

export function canonicalSha256(value: unknown): `0x${string}` {
  return `0x${createHash("sha256").update(canonicalJson(value), "utf8").digest("hex")}`;
}

function parseDecision(stdout: string): PreExecutionDecision {
  let value: unknown;
  try {
    value = JSON.parse(stdout);
  } catch {
    throw new PreExecutionGateError("pre-execution evaluator returned invalid JSON");
  }
  if (!value || typeof value !== "object") {
    throw new PreExecutionGateError("pre-execution evaluator returned an invalid decision");
  }
  const decision = value as PreExecutionDecision;
  if (
    decision.schemaVersion !== 1 ||
    decision.kind !== "pre-execution-decision" ||
    typeof decision.accepted !== "boolean" ||
    !HASH_PATTERN.test(decision.candidateSha256) ||
    !HASH_PATTERN.test(decision.executionSha256) ||
    !HASH_PATTERN.test(decision.approvalSha256) ||
    !HASH_PATTERN.test(decision.policySha256)
  ) {
    throw new PreExecutionGateError("pre-execution evaluator returned an invalid decision");
  }
  return decision;
}

export function evaluateBeforeBroadcast(
  candidate: PreExecutionCandidate,
  options: {
    approvalPath?: string;
    timeoutMs?: number;
    runner?: Runner;
  } = {},
): PreExecutionDecision {
  const approvalPath = options.approvalPath ?? join(REPO_ROOT, "specs", "mvp-user-policy-approval.json");
  const runner = options.runner ?? ((command, args, runOptions) => spawnSync(command, args, runOptions) as SpawnSyncReturns<string>);
  const result = runner(
    "uv",
    [
      "run",
      "--offline",
      "--cache-dir",
      "tmp/uv-cache",
      "--project",
      "verifier",
      "python",
      "-B",
      "verifier/evaluate_pre_execution.py",
      approvalPath,
      "-",
    ],
    {
      cwd: REPO_ROOT,
      input: canonicalJson(candidate),
      encoding: "utf8",
      timeout: options.timeoutMs ?? 5_000,
      windowsHide: true,
      env: process.env,
    },
  );

  if (result.error) throw new PreExecutionGateError(`pre-execution evaluator failed: ${result.error.message}`);
  if (result.status !== 0 && result.status !== 2) {
    throw new PreExecutionGateError("pre-execution evaluator failed closed");
  }
  const decision = parseDecision(result.stdout);
  if (decision.accepted !== (result.status === 0)) {
    throw new PreExecutionGateError("pre-execution decision disagrees with evaluator exit status");
  }
  if (decision.candidateSha256 !== canonicalSha256(candidate)) {
    throw new PreExecutionGateError("pre-execution candidate hash mismatch");
  }
  if (decision.executionSha256 !== canonicalSha256(candidate.execution)) {
    throw new PreExecutionGateError("pre-execution execution hash mismatch");
  }
  if (decision.candidateId !== candidate.candidateId || decision.historySha256 !== candidate.historySha256) {
    throw new PreExecutionGateError("pre-execution decision links do not match the candidate");
  }
  return decision;
}

function cloneCandidate(candidate: PreExecutionCandidate): PreExecutionCandidate {
  return JSON.parse(JSON.stringify(candidate)) as PreExecutionCandidate;
}

export async function sendGuardedTransaction<T>(options: {
  candidate: PreExecutionCandidate;
  evaluate: (candidate: PreExecutionCandidate) => PreExecutionDecision | Promise<PreExecutionDecision>;
  readContext: () => ExecutionContext | Promise<ExecutionContext>;
  verifyExecutionBinding: (candidate: PreExecutionCandidate) => boolean | Promise<boolean>;
  send: (execution: Readonly<ExecutionRequest>) => T | Promise<T>;
}): Promise<T> {
  const snapshot = cloneCandidate(options.candidate);
  const decision = await options.evaluate(snapshot);
  if (decision.candidateSha256 !== canonicalSha256(snapshot)) {
    throw new PreExecutionGateError("candidate changed after evaluation");
  }
  if (decision.executionSha256 !== canonicalSha256(snapshot.execution)) {
    throw new PreExecutionGateError("execution changed after evaluation");
  }
  if (decision.candidateId !== snapshot.candidateId || decision.historySha256 !== snapshot.historySha256) {
    throw new PreExecutionGateError("decision links do not match the candidate");
  }
  if (!decision.accepted) throw new PreExecutionGateError("transaction rejected before broadcast");

  const currentContext = await options.readContext();
  if (canonicalJson(currentContext) !== canonicalJson(snapshot.context)) {
    throw new PreExecutionGateError("execution context drifted before broadcast");
  }
  if (!(await options.verifyExecutionBinding(snapshot))) {
    throw new PreExecutionGateError("execution is not bound to the evaluated portfolio candidate");
  }
  return options.send(Object.freeze({ ...snapshot.execution }));
}
