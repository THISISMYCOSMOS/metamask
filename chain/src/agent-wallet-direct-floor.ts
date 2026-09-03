import {
  createPublicClient,
  decodeFunctionData,
  encodeFunctionData,
  http,
  parseAbi,
  type Address,
  type Hex,
  type PublicClient,
} from "viem";

import { type AgentWalletCliSendResult, buildAgentWalletTransactionPayload } from "./agent-wallet-cli.js";
import { validateApproval } from "./agent-wallet-runtime.js";
import { type BalanceFloorApproval, ERC20_TRANSFER_ABI } from "./delegated-floor-gate.js";
import { canonicalSha256, PreExecutionGateError, type ExecutionRequest } from "./pre-execution-gate.js";

const ADDRESS_PATTERN = /^0x[0-9a-fA-F]{40}$/;
const HASH_PATTERN = /^0x[0-9a-fA-F]{64}$/;
const BYTES_PATTERN = /^0x(?:[0-9a-fA-F]{2})*$/;
const UINT_PATTERN = /^(0|[1-9][0-9]*)$/;
const BALANCE_OF_ABI = parseAbi(["function balanceOf(address owner) view returns (uint256)"]);

export interface DirectFloorContext {
  chainId: number;
  currentBlockNumber: string;
  currentBlockHash: `0x${string}`;
  senderNonce: string;
  walletAddress: `0x${string}`;
  tokenAddress: `0x${string}`;
  assetBalance: string;
}

export interface DirectFloorCandidate {
  schemaVersion: 1;
  kind: "agent-wallet-direct-floor-candidate";
  candidateId: string;
  approvalSha256: `0x${string}`;
  policySha256: `0x${string}`;
  context: DirectFloorContext;
  execution: ExecutionRequest;
  effect: {
    walletAddress: `0x${string}`;
    tokenAddress: `0x${string}`;
    recipientAddress: `0x${string}`;
    transferAmount: string;
    afterAssetBalance: string;
  };
}

export interface DirectFloorDecision {
  schemaVersion: 1;
  kind: "agent-wallet-direct-floor-decision";
  candidateId: string;
  candidateSha256: `0x${string}`;
  executionSha256: `0x${string}`;
  approvalSha256: `0x${string}`;
  policySha256: `0x${string}`;
  accepted: boolean;
  reasonCodes: string[];
}

export interface AgentWalletDirectBundle {
  schemaVersion: 1;
  kind: "agent-wallet-direct-floor-bundle";
  approval: BalanceFloorApproval;
  candidate: DirectFloorCandidate;
}

export interface DirectFloorReceiptEvidence {
  transactionHash: `0x${string}`;
  blockNumber: string;
  blockHash: `0x${string}`;
  receiptStatus: "success";
  assetBalanceAfter: string;
}

export interface DirectFloorRpc {
  simulate(execution: Readonly<ExecutionRequest>): Promise<void>;
  readContext(candidate: Readonly<DirectFloorCandidate>): Promise<DirectFloorContext>;
  verifyReceipt(
    sent: Readonly<AgentWalletCliSendResult>,
    candidate: Readonly<DirectFloorCandidate>,
  ): Promise<DirectFloorReceiptEvidence>;
}

type RecordValue = Record<string, unknown>;

function record(value: unknown, field: string): RecordValue {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new PreExecutionGateError(`${field} must be an object`);
  }
  return value as RecordValue;
}

function exactKeys(value: RecordValue, field: string, keys: readonly string[]): void {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new PreExecutionGateError(`${field} contains missing or unknown fields`);
  }
}

function literal(value: unknown, expected: unknown, field: string): void {
  if (value !== expected) throw new PreExecutionGateError(`${field} is invalid`);
}

function text(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) throw new PreExecutionGateError(`${field} must be a non-empty string`);
  return value;
}

function match(value: unknown, pattern: RegExp, field: string): string {
  const result = text(value, field);
  if (!pattern.test(result)) throw new PreExecutionGateError(`${field} has an invalid format`);
  return result;
}

function uint(value: unknown, field: string): string {
  const result = match(value, UINT_PATTERN, field);
  if (BigInt(result) >= 1n << 256n) throw new PreExecutionGateError(`${field} exceeds uint256`);
  return result;
}

function positiveInteger(value: unknown, field: string): number {
  if (!Number.isSafeInteger(value) || Number(value) < 1) throw new PreExecutionGateError(`${field} is invalid`);
  return Number(value);
}

function sameAddress(left: string, right: string): boolean {
  return left.toLowerCase() === right.toLowerCase();
}

function address(value: string): Address {
  if (!ADDRESS_PATTERN.test(value)) throw new PreExecutionGateError("runtime address is invalid");
  return value.toLowerCase() as Address;
}

export function verifyDirectTransferBinding(candidate: Readonly<DirectFloorCandidate>): boolean {
  try {
    const decoded = decodeFunctionData({ abi: ERC20_TRANSFER_ABI, data: candidate.execution.data });
    if (decoded.functionName !== "transfer") return false;
    const [recipient, amount] = decoded.args;
    if (!sameAddress(candidate.execution.fromAddress, candidate.effect.walletAddress)) return false;
    if (!sameAddress(candidate.execution.toAddress, candidate.effect.tokenAddress)) return false;
    if (!sameAddress(recipient, candidate.effect.recipientAddress)) return false;
    if (amount.toString() !== candidate.effect.transferAmount) return false;
    return encodeFunctionData({
      abi: ERC20_TRANSFER_ABI,
      functionName: "transfer",
      args: decoded.args,
    }).toLowerCase() === candidate.execution.data.toLowerCase();
  } catch {
    return false;
  }
}

export function evaluateDirectFloor(
  approval: Readonly<BalanceFloorApproval>,
  candidate: Readonly<DirectFloorCandidate>,
): DirectFloorDecision {
  const reasons: string[] = [];
  const proposalHash = canonicalSha256(approval.proposal);
  const approvalHash = canonicalSha256(approval);
  const policy = approval.proposal.policy;
  const policyHash = canonicalSha256(policy);

  if (approval.proposalSha256 !== proposalHash) reasons.push("APPROVAL_PROPOSAL_HASH_MISMATCH");
  if (approval.confirmation !== `APPROVE ${proposalHash}`) reasons.push("APPROVAL_CONFIRMATION_MISMATCH");
  if (approval.policySha256 !== policyHash || approval.proposal.policySha256 !== policyHash) {
    reasons.push("APPROVAL_POLICY_HASH_MISMATCH");
  }
  if (candidate.approvalSha256 !== approvalHash) reasons.push("CANDIDATE_APPROVAL_HASH_MISMATCH");
  if (candidate.policySha256 !== policyHash) reasons.push("CANDIDATE_POLICY_HASH_MISMATCH");

  const before = BigInt(candidate.context.assetBalance);
  const amount = BigInt(candidate.effect.transferAmount);
  const after = BigInt(candidate.effect.afterAssetBalance);
  const floor = BigInt(policy.assetBalanceFloor);
  if (amount === 0n) reasons.push("ZERO_TRANSFER_AMOUNT");
  if (before < amount || before - amount !== after) reasons.push("ASSET_BALANCE_EFFECT_MISMATCH");
  if (after < floor) reasons.push("ASSET_BALANCE_FLOOR_VIOLATION");

  if (candidate.context.chainId !== policy.chainId || candidate.execution.chainId !== policy.chainId) {
    reasons.push("POLICY_CHAIN_MISMATCH");
  }
  if (!sameAddress(candidate.context.walletAddress, policy.walletAddress)
      || !sameAddress(candidate.effect.walletAddress, policy.walletAddress)) {
    reasons.push("POLICY_WALLET_MISMATCH");
  }
  if (!sameAddress(candidate.context.tokenAddress, policy.tokenAddress)
      || !sameAddress(candidate.effect.tokenAddress, policy.tokenAddress)) {
    reasons.push("POLICY_TOKEN_MISMATCH");
  }
  if (candidate.execution.nonce !== candidate.context.senderNonce) reasons.push("SENDER_NONCE_MISMATCH");
  if (!sameAddress(candidate.execution.fromAddress, policy.walletAddress)) reasons.push("EXECUTION_SENDER_MISMATCH");
  if (!sameAddress(candidate.execution.toAddress, policy.tokenAddress)) reasons.push("EXECUTION_TARGET_MISMATCH");
  if (candidate.execution.value !== "0") reasons.push("NONZERO_NATIVE_VALUE");
  if (!verifyDirectTransferBinding(candidate)) reasons.push("DIRECT_TRANSFER_BINDING_MISMATCH");

  return {
    schemaVersion: 1,
    kind: "agent-wallet-direct-floor-decision",
    candidateId: candidate.candidateId,
    candidateSha256: canonicalSha256(candidate),
    executionSha256: canonicalSha256(candidate.execution),
    approvalSha256: approvalHash,
    policySha256: policyHash,
    accepted: reasons.length === 0,
    reasonCodes: reasons,
  };
}

function validateCandidate(value: unknown): void {
  const candidate = record(value, "candidate");
  exactKeys(candidate, "candidate", [
    "schemaVersion", "kind", "candidateId", "approvalSha256", "policySha256", "context", "execution", "effect",
  ]);
  literal(candidate.schemaVersion, 1, "candidate.schemaVersion");
  literal(candidate.kind, "agent-wallet-direct-floor-candidate", "candidate.kind");
  text(candidate.candidateId, "candidate.candidateId");
  match(candidate.approvalSha256, HASH_PATTERN, "candidate.approvalSha256");
  match(candidate.policySha256, HASH_PATTERN, "candidate.policySha256");

  const context = record(candidate.context, "candidate.context");
  exactKeys(context, "candidate.context", [
    "chainId", "currentBlockNumber", "currentBlockHash", "senderNonce", "walletAddress", "tokenAddress", "assetBalance",
  ]);
  positiveInteger(context.chainId, "candidate.context.chainId");
  uint(context.currentBlockNumber, "candidate.context.currentBlockNumber");
  match(context.currentBlockHash, HASH_PATTERN, "candidate.context.currentBlockHash");
  uint(context.senderNonce, "candidate.context.senderNonce");
  match(context.walletAddress, ADDRESS_PATTERN, "candidate.context.walletAddress");
  match(context.tokenAddress, ADDRESS_PATTERN, "candidate.context.tokenAddress");
  uint(context.assetBalance, "candidate.context.assetBalance");

  const execution = record(candidate.execution, "candidate.execution");
  exactKeys(execution, "candidate.execution", ["chainId", "fromAddress", "toAddress", "value", "data", "gas", "nonce"]);
  positiveInteger(execution.chainId, "candidate.execution.chainId");
  match(execution.fromAddress, ADDRESS_PATTERN, "candidate.execution.fromAddress");
  match(execution.toAddress, ADDRESS_PATTERN, "candidate.execution.toAddress");
  uint(execution.value, "candidate.execution.value");
  match(execution.data, BYTES_PATTERN, "candidate.execution.data");
  uint(execution.gas, "candidate.execution.gas");
  uint(execution.nonce, "candidate.execution.nonce");

  const effect = record(candidate.effect, "candidate.effect");
  exactKeys(effect, "candidate.effect", [
    "walletAddress", "tokenAddress", "recipientAddress", "transferAmount", "afterAssetBalance",
  ]);
  match(effect.walletAddress, ADDRESS_PATTERN, "candidate.effect.walletAddress");
  match(effect.tokenAddress, ADDRESS_PATTERN, "candidate.effect.tokenAddress");
  match(effect.recipientAddress, ADDRESS_PATTERN, "candidate.effect.recipientAddress");
  uint(effect.transferAmount, "candidate.effect.transferAmount");
  uint(effect.afterAssetBalance, "candidate.effect.afterAssetBalance");
}

export function parseAgentWalletDirectBundleForEvaluation(value: unknown): AgentWalletDirectBundle {
  const bundle = record(value, "bundle");
  exactKeys(bundle, "bundle", ["schemaVersion", "kind", "approval", "candidate"]);
  literal(bundle.schemaVersion, 1, "bundle.schemaVersion");
  literal(bundle.kind, "agent-wallet-direct-floor-bundle", "bundle.kind");
  validateApproval(bundle.approval);
  validateCandidate(bundle.candidate);
  return bundle as unknown as AgentWalletDirectBundle;
}

export function parseAgentWalletDirectBundle(value: unknown): AgentWalletDirectBundle {
  const parsed = parseAgentWalletDirectBundleForEvaluation(value);
  const decision = evaluateDirectFloor(parsed.approval, parsed.candidate);
  if (!decision.accepted) {
    throw new PreExecutionGateError(`bundle is not eligible for broadcast: ${decision.reasonCodes.join(",")}`);
  }
  return parsed;
}

function contextStillValid(captured: Readonly<DirectFloorContext>, current: Readonly<DirectFloorContext>): boolean {
  return current.chainId === captured.chainId
    && BigInt(current.currentBlockNumber) >= BigInt(captured.currentBlockNumber)
    && current.senderNonce === captured.senderNonce
    && sameAddress(current.walletAddress, captured.walletAddress)
    && sameAddress(current.tokenAddress, captured.tokenAddress)
    && current.assetBalance === captured.assetBalance;
}

export class ViemDirectFloorRpc implements DirectFloorRpc {
  private readonly client: PublicClient;

  constructor(rpcUrl: string) {
    let parsed: URL;
    try {
      parsed = new URL(rpcUrl);
    } catch {
      throw new PreExecutionGateError("AGENT_WALLET_RPC_URL is not a valid URL");
    }
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
      throw new PreExecutionGateError("AGENT_WALLET_RPC_URL must use http or https");
    }
    this.client = createPublicClient({ transport: http(rpcUrl) });
  }

  private async assertChain(chainId: number): Promise<void> {
    if (await this.client.getChainId() !== chainId) throw new PreExecutionGateError("RPC chain does not match execution chainId");
  }

  async simulate(execution: Readonly<ExecutionRequest>): Promise<void> {
    await this.assertChain(execution.chainId);
    const payload = buildAgentWalletTransactionPayload(execution);
    await this.client.request({
      method: "eth_call",
      params: [{ from: address(execution.fromAddress), ...payload }, "latest"],
    });
  }

  async readContext(candidate: Readonly<DirectFloorCandidate>): Promise<DirectFloorContext> {
    await this.assertChain(candidate.context.chainId);
    const [block, nonce, balance] = await Promise.all([
      this.client.getBlock({ blockTag: "latest" }),
      this.client.getTransactionCount({ address: address(candidate.context.walletAddress), blockTag: "pending" }),
      this.client.readContract({
        address: address(candidate.context.tokenAddress),
        abi: BALANCE_OF_ABI,
        functionName: "balanceOf",
        args: [address(candidate.context.walletAddress)],
        blockTag: "latest",
      }),
    ]);
    if (!block.hash) throw new PreExecutionGateError("latest RPC block has no hash");
    return {
      chainId: candidate.context.chainId,
      currentBlockNumber: block.number.toString(),
      currentBlockHash: block.hash,
      senderNonce: nonce.toString(),
      walletAddress: candidate.context.walletAddress,
      tokenAddress: candidate.context.tokenAddress,
      assetBalance: balance.toString(),
    };
  }

  async verifyReceipt(
    sent: Readonly<AgentWalletCliSendResult>,
    candidate: Readonly<DirectFloorCandidate>,
  ): Promise<DirectFloorReceiptEvidence> {
    const hash = sent.transactionHash as Hex;
    const receipt = await this.client.waitForTransactionReceipt({ hash, timeout: 120_000 });
    if (receipt.status !== "success") throw new PreExecutionGateError("Agent Wallet transaction receipt reverted");
    const transaction = await this.client.getTransaction({ hash });
    const execution = candidate.execution;
    if (!sameAddress(sent.walletAddress, execution.fromAddress)
      || !sameAddress(transaction.from, execution.fromAddress)
      || !transaction.to
      || !sameAddress(transaction.to, execution.toAddress)
      || transaction.input.toLowerCase() !== execution.data.toLowerCase()
      || transaction.value !== BigInt(execution.value)
      || transaction.nonce !== Number(BigInt(execution.nonce))) {
      throw new PreExecutionGateError("confirmed transaction does not match the approved exact execution");
    }
    const balance = await this.client.readContract({
      address: address(candidate.context.tokenAddress),
      abi: BALANCE_OF_ABI,
      functionName: "balanceOf",
      args: [address(candidate.context.walletAddress)],
      blockNumber: receipt.blockNumber,
    });
    if (balance.toString() !== candidate.effect.afterAssetBalance) {
      throw new PreExecutionGateError("confirmed token balance does not match the simulated effect");
    }
    return {
      transactionHash: sent.transactionHash,
      blockNumber: receipt.blockNumber.toString(),
      blockHash: receipt.blockHash,
      receiptStatus: "success",
      assetBalanceAfter: balance.toString(),
    };
  }
}

export async function executeAgentWalletDirectBundle(options: {
  bundle: AgentWalletDirectBundle;
  rpc: DirectFloorRpc;
  broadcast: boolean;
  send: (execution: Readonly<ExecutionRequest>) => Promise<AgentWalletCliSendResult>;
}): Promise<Record<string, unknown>> {
  const bundle = parseAgentWalletDirectBundleForEvaluation(JSON.parse(JSON.stringify(options.bundle)));
  await options.rpc.simulate(bundle.candidate.execution);
  const current = await options.rpc.readContext(bundle.candidate);
  if (!contextStillValid(bundle.candidate.context, current)) {
    throw new PreExecutionGateError("direct execution context drifted before broadcast");
  }
  const decision = evaluateDirectFloor(bundle.approval, bundle.candidate);

  if (!options.broadcast) {
    return {
      schemaVersion: 1,
      kind: "agent-wallet-direct-preflight-result",
      broadcast: false,
      eligibleForBroadcast: decision.accepted,
      candidateSha256: decision.candidateSha256,
      decisionSha256: canonicalSha256(decision),
      reasonCodes: decision.reasonCodes,
      revalidatedAtBlockNumber: current.currentBlockNumber,
      revalidatedAtBlockHash: current.currentBlockHash,
    };
  }

  if (!decision.accepted) throw new PreExecutionGateError("direct transaction rejected before broadcast");

  const sent = await options.send(Object.freeze({ ...bundle.candidate.execution }));
  const receipt = await options.rpc.verifyReceipt(sent, bundle.candidate);
  return {
    schemaVersion: 1,
    kind: "agent-wallet-direct-broadcast-result",
    broadcast: true,
    eligibleForBroadcast: true,
    candidateSha256: decision.candidateSha256,
    decisionSha256: canonicalSha256(decision),
    transactionHash: sent.transactionHash,
    receipt,
  };
}
