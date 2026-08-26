import {
  createPublicClient,
  http,
  parseAbi,
  type Address,
  type Hex,
  type PublicClient,
} from "viem";

import {
  DelegatedFloorGate,
  evaluateDelegatedFloor,
  verifyDelegatedExecutionBinding,
  type BalanceFloorApproval,
  type DelegatedFloorCandidate,
  type DelegatedTransferContext,
} from "./delegated-floor-gate.js";
import {
  buildAgentWalletTransactionPayload,
  type AgentWalletCliSendResult,
} from "./agent-wallet-cli.js";
import { canonicalJson, canonicalSha256, PreExecutionGateError, type ExecutionRequest } from "./pre-execution-gate.js";

const ADDRESS_PATTERN = /^0x[0-9a-fA-F]{40}$/;
const HASH_PATTERN = /^0x[0-9a-fA-F]{64}$/;
const BYTES_PATTERN = /^0x(?:[0-9a-fA-F]{2})*$/;
const UINT_PATTERN = /^(0|[1-9][0-9]*)$/;
const BALANCE_OF_ABI = parseAbi(["function balanceOf(address owner) view returns (uint256)"]);

export interface AgentWalletExecutionBundle {
  schemaVersion: 1;
  kind: "agent-wallet-execution-bundle";
  approval: BalanceFloorApproval;
  candidate: DelegatedFloorCandidate;
}

export interface AgentWalletReceiptEvidence {
  transactionHash: `0x${string}`;
  blockNumber: string;
  blockHash: `0x${string}`;
  receiptStatus: "success";
  assetBalanceAfter: string;
}

export interface AgentWalletRuntimeRpc {
  simulate(execution: Readonly<ExecutionRequest>): Promise<void>;
  readContext(candidate: Readonly<DelegatedFloorCandidate>): Promise<DelegatedTransferContext>;
  verifyReceipt(
    sent: Readonly<AgentWalletCliSendResult>,
    candidate: Readonly<DelegatedFloorCandidate>,
  ): Promise<AgentWalletReceiptEvidence>;
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

function textValue(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) throw new PreExecutionGateError(`${field} must be a non-empty string`);
  return value;
}

function match(value: unknown, pattern: RegExp, field: string): string {
  const text = textValue(value, field);
  if (!pattern.test(text)) throw new PreExecutionGateError(`${field} has an invalid format`);
  return text;
}

function uint(value: unknown, field: string): string {
  const text = match(value, UINT_PATTERN, field);
  if (BigInt(text) >= 1n << 256n) throw new PreExecutionGateError(`${field} exceeds uint256`);
  return text;
}

function numberValue(value: unknown, field: string): number {
  if (!Number.isSafeInteger(value) || Number(value) < 1) throw new PreExecutionGateError(`${field} is invalid`);
  return Number(value);
}

function stringArray(value: unknown, field: string, mustBeEmpty = false): void {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string" || !item.trim())) {
    throw new PreExecutionGateError(`${field} must be an array of non-empty strings`);
  }
  if (mustBeEmpty && value.length !== 0) throw new PreExecutionGateError(`${field} must be empty`);
}

function validateProposal(value: unknown): void {
  const proposal = record(value, "approval.proposal");
  const revised = proposal.kind === "revised-policy-proposal";
  if (!revised && proposal.kind !== "policy-proposal") throw new PreExecutionGateError("approval.proposal.kind is invalid");
  exactKeys(
    proposal,
    "approval.proposal",
    [
      "schemaVersion", "kind", "proposalId", "requestSha256", "intentText", "compiler", "policy",
      "policySha256", "rationales", "assumptions", "unsupportedItems", ...(revised ? ["revision"] : []),
    ],
  );
  literal(proposal.schemaVersion, 1, "approval.proposal.schemaVersion");
  textValue(proposal.proposalId, "approval.proposal.proposalId");
  match(proposal.requestSha256, HASH_PATTERN, "approval.proposal.requestSha256");
  textValue(proposal.intentText, "approval.proposal.intentText");
  match(proposal.policySha256, HASH_PATTERN, "approval.proposal.policySha256");
  stringArray(proposal.rationales, "approval.proposal.rationales");
  if ((proposal.rationales as unknown[]).length === 0) throw new PreExecutionGateError("approval.proposal.rationales must not be empty");
  stringArray(proposal.assumptions, "approval.proposal.assumptions");
  stringArray(proposal.unsupportedItems, "approval.proposal.unsupportedItems", true);

  const compiler = record(proposal.compiler, "approval.proposal.compiler");
  exactKeys(compiler, "approval.proposal.compiler", ["provider", "model"]);
  if (compiler.provider !== "google-gemini" && compiler.provider !== "anthropic") {
    throw new PreExecutionGateError("approval.proposal.compiler.provider is invalid");
  }
  textValue(compiler.model, "approval.proposal.compiler.model");

  const policy = record(proposal.policy, "approval.proposal.policy");
  exactKeys(policy, "approval.proposal.policy", [
    "schemaVersion", "kind", "policyId", "chainId", "walletAddress", "tokenAddress", "assetBalanceFloor",
  ]);
  literal(policy.schemaVersion, 1, "approval.proposal.policy.schemaVersion");
  literal(policy.kind, "assetBalanceFloor", "approval.proposal.policy.kind");
  textValue(policy.policyId, "approval.proposal.policy.policyId");
  numberValue(policy.chainId, "approval.proposal.policy.chainId");
  match(policy.walletAddress, ADDRESS_PATTERN, "approval.proposal.policy.walletAddress");
  match(policy.tokenAddress, ADDRESS_PATTERN, "approval.proposal.policy.tokenAddress");
  uint(policy.assetBalanceFloor, "approval.proposal.policy.assetBalanceFloor");

  if (revised) {
    const revision = record(proposal.revision, "approval.proposal.revision");
    exactKeys(revision, "approval.proposal.revision", [
      "schemaVersion", "kind", "sourceProposalSha256", "revisedBy", "assetBalanceFloorBefore", "assetBalanceFloorAfter",
    ]);
    literal(revision.schemaVersion, 1, "approval.proposal.revision.schemaVersion");
    literal(revision.kind, "user-policy-revision", "approval.proposal.revision.kind");
    match(revision.sourceProposalSha256, HASH_PATTERN, "approval.proposal.revision.sourceProposalSha256");
    textValue(revision.revisedBy, "approval.proposal.revision.revisedBy");
    const before = uint(revision.assetBalanceFloorBefore, "approval.proposal.revision.assetBalanceFloorBefore");
    const after = uint(revision.assetBalanceFloorAfter, "approval.proposal.revision.assetBalanceFloorAfter");
    if (before === after || after !== policy.assetBalanceFloor) {
      throw new PreExecutionGateError("approval.proposal.revision does not bind the revised policy value");
    }
  }
}

function validateApproval(value: unknown): void {
  const approval = record(value, "approval");
  exactKeys(approval, "approval", [
    "schemaVersion", "kind", "approvalId", "approvalScope", "approvedBy", "proposal", "proposalSha256",
    "policySha256", "confirmation",
  ]);
  literal(approval.schemaVersion, 1, "approval.schemaVersion");
  literal(approval.kind, "approved-policy-envelope", "approval.kind");
  literal(approval.approvalScope, "user", "approval.approvalScope");
  textValue(approval.approvalId, "approval.approvalId");
  textValue(approval.approvedBy, "approval.approvedBy");
  match(approval.proposalSha256, HASH_PATTERN, "approval.proposalSha256");
  match(approval.policySha256, HASH_PATTERN, "approval.policySha256");
  textValue(approval.confirmation, "approval.confirmation");
  validateProposal(approval.proposal);
}

function validateCandidate(value: unknown): void {
  const candidate = record(value, "candidate");
  exactKeys(candidate, "candidate", [
    "schemaVersion", "kind", "candidateId", "approvalSha256", "policySha256", "context", "execution", "effect",
  ]);
  literal(candidate.schemaVersion, 1, "candidate.schemaVersion");
  literal(candidate.kind, "delegated-floor-candidate", "candidate.kind");
  textValue(candidate.candidateId, "candidate.candidateId");
  match(candidate.approvalSha256, HASH_PATTERN, "candidate.approvalSha256");
  match(candidate.policySha256, HASH_PATTERN, "candidate.policySha256");

  const context = record(candidate.context, "candidate.context");
  exactKeys(context, "candidate.context", [
    "chainId", "currentBlockNumber", "currentBlockHash", "delegateNonce", "delegationManagerAddress",
    "walletAddress", "tokenAddress", "assetBalance",
  ]);
  numberValue(context.chainId, "candidate.context.chainId");
  uint(context.currentBlockNumber, "candidate.context.currentBlockNumber");
  match(context.currentBlockHash, HASH_PATTERN, "candidate.context.currentBlockHash");
  uint(context.delegateNonce, "candidate.context.delegateNonce");
  for (const key of ["delegationManagerAddress", "walletAddress", "tokenAddress"] as const) {
    match(context[key], ADDRESS_PATTERN, `candidate.context.${key}`);
  }
  uint(context.assetBalance, "candidate.context.assetBalance");

  const execution = record(candidate.execution, "candidate.execution");
  exactKeys(execution, "candidate.execution", ["chainId", "fromAddress", "toAddress", "value", "data", "gas", "nonce"]);
  numberValue(execution.chainId, "candidate.execution.chainId");
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
  for (const key of ["walletAddress", "tokenAddress", "recipientAddress"] as const) {
    match(effect[key], ADDRESS_PATTERN, `candidate.effect.${key}`);
  }
  uint(effect.transferAmount, "candidate.effect.transferAmount");
  uint(effect.afterAssetBalance, "candidate.effect.afterAssetBalance");
}

export function parseAgentWalletExecutionBundle(value: unknown): AgentWalletExecutionBundle {
  const bundle = record(value, "bundle");
  exactKeys(bundle, "bundle", ["schemaVersion", "kind", "approval", "candidate"]);
  literal(bundle.schemaVersion, 1, "bundle.schemaVersion");
  literal(bundle.kind, "agent-wallet-execution-bundle", "bundle.kind");
  validateApproval(bundle.approval);
  validateCandidate(bundle.candidate);
  const parsed = bundle as unknown as AgentWalletExecutionBundle;
  let decision;
  try {
    decision = evaluateDelegatedFloor(parsed.approval, parsed.candidate);
  } catch {
    throw new PreExecutionGateError("bundle cannot be evaluated as a delegated balance-floor execution");
  }
  if (!decision.accepted) {
    throw new PreExecutionGateError(`bundle is not eligible for broadcast: ${decision.reasonCodes.join(",")}`);
  }
  if (!verifyDelegatedExecutionBinding(parsed.candidate)) {
    throw new PreExecutionGateError("bundle execution is not bound to its signed delegation and simulated effect");
  }
  return parsed;
}

function sameAddress(left: string, right: string): boolean {
  return left.toLowerCase() === right.toLowerCase();
}

function address(value: string): Address {
  if (!ADDRESS_PATTERN.test(value)) throw new PreExecutionGateError("runtime address is invalid");
  return value.toLowerCase() as Address;
}

export class ViemAgentWalletRpc implements AgentWalletRuntimeRpc {
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

  async readContext(candidate: Readonly<DelegatedFloorCandidate>): Promise<DelegatedTransferContext> {
    await this.assertChain(candidate.context.chainId);
    const [block, nonce, balance] = await Promise.all([
      this.client.getBlock({ blockTag: "latest" }),
      this.client.getTransactionCount({ address: address(candidate.execution.fromAddress), blockTag: "pending" }),
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
      delegateNonce: nonce.toString(),
      delegationManagerAddress: candidate.context.delegationManagerAddress,
      walletAddress: candidate.context.walletAddress,
      tokenAddress: candidate.context.tokenAddress,
      assetBalance: balance.toString(),
    };
  }

  async verifyReceipt(
    sent: Readonly<AgentWalletCliSendResult>,
    candidate: Readonly<DelegatedFloorCandidate>,
  ): Promise<AgentWalletReceiptEvidence> {
    const hash = sent.transactionHash as Hex;
    const receipt = await this.client.waitForTransactionReceipt({ hash, timeout: 60_000 });
    if (receipt.status !== "success") throw new PreExecutionGateError("Agent Wallet transaction receipt reverted");
    const transaction = await this.client.getTransaction({ hash });
    const execution = candidate.execution;
    if (
      !sameAddress(transaction.from, execution.fromAddress)
      || !transaction.to
      || !sameAddress(transaction.to, execution.toAddress)
      || transaction.input.toLowerCase() !== execution.data.toLowerCase()
      || transaction.value !== BigInt(execution.value)
      || transaction.gas !== BigInt(execution.gas)
      || transaction.nonce !== Number(BigInt(execution.nonce))
    ) {
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

export async function executeAgentWalletBundle(options: {
  bundle: AgentWalletExecutionBundle;
  rpc: AgentWalletRuntimeRpc;
  broadcast: boolean;
  send: (execution: Readonly<ExecutionRequest>) => Promise<AgentWalletCliSendResult>;
}): Promise<Record<string, unknown>> {
  const bundle = parseAgentWalletExecutionBundle(JSON.parse(JSON.stringify(options.bundle)));
  await options.rpc.simulate(bundle.candidate.execution);

  if (!options.broadcast) {
    const context = await options.rpc.readContext(bundle.candidate);
    if (canonicalJson(context) !== canonicalJson(bundle.candidate.context)) {
      throw new PreExecutionGateError("delegated execution context drifted after simulation");
    }
    const decision = evaluateDelegatedFloor(bundle.approval, bundle.candidate);
    return {
      schemaVersion: 1,
      kind: "agent-wallet-preflight-result",
      broadcast: false,
      eligibleForBroadcast: true,
      candidateSha256: canonicalSha256(bundle.candidate),
      decisionSha256: canonicalSha256(decision),
    };
  }

  const result = await new DelegatedFloorGate().execute({
    approval: bundle.approval,
    candidate: bundle.candidate,
    readContext: () => options.rpc.readContext(bundle.candidate),
    send: options.send,
  });
  const receipt = await options.rpc.verifyReceipt(result.sendResult, bundle.candidate);
  return {
    schemaVersion: 1,
    kind: "agent-wallet-broadcast-result",
    broadcast: true,
    eligibleForBroadcast: true,
    candidateSha256: result.decision.candidateSha256,
    decisionSha256: canonicalSha256(result.decision),
    transactionHash: result.sendResult.transactionHash,
    receipt,
  };
}
