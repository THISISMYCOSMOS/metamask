import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

import {
  createPublicClient,
  createWalletClient,
  decodeEventLog,
  defineChain,
  encodeFunctionData,
  http,
  keccak256,
  parseAbi,
  type Address,
  type Hex,
  type TransactionReceipt,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";

import {
  type AgentWalletDirectBundle,
  type DirectFloorCandidate,
  evaluateDirectFloor,
  parseAgentWalletDirectBundleForEvaluation,
} from "./agent-wallet-direct-floor.js";
import { ANVIL_DEFAULT_PRIVATE_KEYS } from "./config.js";
import { type BalanceFloorApproval, ERC20_TRANSFER_ABI } from "./delegated-floor-gate.js";
import { canonicalSha256 } from "./pre-execution-gate.js";

const CHAIN_ID = 31_337;
const INITIAL_BALANCE = 1_000_000n;
const FLOOR = 500_000n;
const GAS = 70_000n;
const MAX_FEE_PER_GAS = 2_000_000_000n;
const MAX_PRIORITY_FEE_PER_GAS = 1_000_000_000n;
const BASE_PORT = 18_560;
const OUTPUT_PATH = resolve(process.cwd(), "..", "traces", "gate-position-comparison.json");

const tokenAbi = parseAbi([
  "constructor(address owner, address spender, uint256 initialBalance)",
  "event Transfer(address indexed from, address indexed to, uint256 value)",
  "function balanceOf(address owner) view returns (uint256)",
  "function allowance(address owner, address spender) view returns (uint256)",
  "function transfer(address recipient, uint256 amount) returns (bool)",
  "function transferFrom(address owner, address recipient, uint256 amount) returns (bool)",
]);

const accounts = {
  deployer: privateKeyToAccount(ANVIL_DEFAULT_PRIVATE_KEYS[0]),
  owner: privateKeyToAccount(ANVIL_DEFAULT_PRIVATE_KEYS[1]),
  spender: privateKeyToAccount(ANVIL_DEFAULT_PRIVATE_KEYS[2]),
  recipient: privateKeyToAccount(ANVIL_DEFAULT_PRIVATE_KEYS[3]),
  externalRecipient: privateKeyToAccount(ANVIL_DEFAULT_PRIVATE_KEYS[4]),
} as const;

type Route = "no-application-gate" | "pre-submit" | "pre-sign";
type ScenarioName = "benign-stable" | "floor-violating-stable" | "late-external-debit";

interface Scenario {
  name: ScenarioName;
  candidateAmount: bigint;
  externalDebit: bigint;
}

interface EventEvidence {
  sequence: number;
  event: string;
  ownerBalance: string;
  ownerNonce: string;
  detail?: string;
  transactionHash?: Hex;
  receiptStatus?: "success" | "reverted";
}

interface TransferEventEvidence {
  contractAddress: Address;
  transactionHash: Hex;
  blockNumber: string;
  blockHash: Hex;
  logIndex: number;
  from: Address;
  to: Address;
  value: string;
}

interface CellEvidence {
  scenario: ScenarioName;
  route: Route;
  rpcUrl: string;
  policySha256: Hex;
  candidateSha256: Hex;
  executionSha256: Hex;
  candidateTransaction: {
    chainId: number;
    from: Address;
    to: Address;
    value: string;
    data: Hex;
    gas: string;
    nonce: number;
    maxFeePerGas: string;
    maxPriorityFeePerGas: string;
    type: "eip1559";
  };
  initial: { ownerBalance: string; ownerNonce: string; allowance: string; blockNumber: string; blockHash: Hex };
  gate: {
    position: Route;
    invocations: number;
    simulations: number;
    contextReads: number;
    floorEvaluationCalls: number;
    accepted: boolean | null;
    reasonCodes: string[];
  };
  externalDebit: {
    scheduledAmount: string;
    transactionHash: Hex | null;
    receiptStatus: "success" | null;
    transferEvent: TransferEventEvidence | null;
  };
  candidateSignerCalls: number;
  signedRawTransaction: Hex | null;
  rawTransactionHash: Hex | null;
  candidateReceipt: {
    transactionHash: Hex;
    status: "success" | "reverted";
    blockNumber: string;
    blockHash: Hex;
    gasUsed: string;
    transferEvent: TransferEventEvidence;
  } | null;
  confirmedTransaction: {
    chainId: number;
    from: Address;
    to: Address;
    value: string;
    input: Hex;
    gas: string;
    nonce: number;
    maxFeePerGas: string | null;
    maxPriorityFeePerGas: string | null;
    type: string;
  } | null;
  postState: { ownerBalance: string; recipientBalance: string; externalRecipientBalance: string; floorViolated: boolean };
  outcome: "executed" | "rejected";
  events: EventEvidence[];
}

interface ExperimentRuntime {
  rpcUrl: string;
  chain: ReturnType<typeof defineChain>;
  publicClient: ReturnType<typeof createPublicClient>;
  token: Address;
  initialBlock: { number: bigint; hash: Hex };
}

const routes: Route[] = ["no-application-gate", "pre-submit", "pre-sign"];
const scenarios: Scenario[] = [
  { name: "benign-stable", candidateAmount: 100_000n, externalDebit: 0n },
  { name: "floor-violating-stable", candidateAmount: 600_000n, externalDebit: 0n },
  { name: "late-external-debit", candidateAmount: 400_000n, externalDebit: 200_000n },
];

function requireTransferEvent(
  receipt: TransactionReceipt,
  token: Address,
  expected: { from: Address; to: Address; value: bigint },
): TransferEventEvidence {
  const transfers = receipt.logs.flatMap((log) => {
    if (log.address.toLowerCase() !== token.toLowerCase()) return [];
    try {
      const decoded = decodeEventLog({ abi: tokenAbi, data: log.data, topics: log.topics });
      if (decoded.eventName !== "Transfer") return [];
      const args = decoded.args as { from: Address; to: Address; value: bigint };
      return [{
        contractAddress: log.address,
        transactionHash: log.transactionHash,
        blockNumber: log.blockNumber.toString(),
        blockHash: log.blockHash,
        logIndex: log.logIndex,
        from: args.from,
        to: args.to,
        value: args.value.toString(),
      } satisfies TransferEventEvidence];
    } catch {
      return [];
    }
  });
  if (transfers.length !== 1) throw new Error(`expected one token Transfer event, observed ${transfers.length}`);
  const [transfer] = transfers;
  if (transfer.transactionHash !== receipt.transactionHash
      || transfer.from.toLowerCase() !== expected.from.toLowerCase()
      || transfer.to.toLowerCase() !== expected.to.toLowerCase()
      || transfer.value !== expected.value.toString()) {
    throw new Error("confirmed Transfer event does not match the scheduled token movement");
  }
  return transfer;
}

function compileFixture(): Hex {
  const result = spawnSync(
    "forge",
    ["inspect", "src/GatePositionToken.sol:GatePositionToken", "bytecode", "--root", "."],
    { cwd: process.cwd(), encoding: "utf8", windowsHide: true },
  );
  if (result.status !== 0) throw new Error(`forge fixture compile failed: ${result.stderr.trim()}`);
  const bytecode = result.stdout.trim();
  if (!/^0x[0-9a-fA-F]+$/.test(bytecode)) throw new Error("forge returned invalid fixture bytecode");
  return bytecode as Hex;
}

async function waitForAnvil(rpcUrl: string, process: ChildProcess): Promise<void> {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (process.exitCode !== null) throw new Error(`anvil exited before ready with code ${process.exitCode}`);
    try {
      const response = await fetch(rpcUrl, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "eth_chainId", params: [] }),
      });
      if (response.ok) return;
    } catch {
      // The process may still be binding its local port.
    }
    await delay(50);
  }
  throw new Error("anvil did not become ready");
}

async function stopAnvil(process: ChildProcess): Promise<void> {
  if (process.exitCode !== null) return;
  process.kill();
  await Promise.race([
    new Promise<void>((done) => process.once("exit", () => done())),
    delay(2_000).then(() => undefined),
  ]);
}

function approval(token: Address): BalanceFloorApproval {
  const policy = {
    schemaVersion: 1 as const,
    kind: "assetBalanceFloor" as const,
    policyId: "local-gate-position-floor",
    chainId: CHAIN_ID,
    walletAddress: accounts.owner.address,
    tokenAddress: token,
    assetBalanceFloor: FLOOR.toString(),
  };
  const proposal = {
    schemaVersion: 1 as const,
    kind: "policy-proposal" as const,
    proposalId: "local-gate-position-proposal",
    requestSha256: `0x${"44".repeat(32)}` as Hex,
    intentText: "로컬 테스트 토큰을 0.5개 이상 남겨줘",
    compiler: { provider: "google-gemini", model: "controlled-fixed-input" },
    policy,
    policySha256: canonicalSha256(policy),
    rationales: ["게이트 위치만 비교하기 위한 고정 하한이다."],
    assumptions: ["로컬 GPTT 6 decimals"],
    unsupportedItems: [],
  };
  const proposalSha256 = canonicalSha256(proposal);
  return {
    schemaVersion: 1,
    kind: "approved-policy-envelope",
    approvalId: "local-gate-position-approval",
    approvalScope: "user",
    approvedBy: "controlled-local-owner",
    proposal,
    proposalSha256,
    policySha256: proposal.policySha256,
    confirmation: `APPROVE ${proposalSha256}`,
  };
}

function candidate(inputApproval: BalanceFloorApproval, token: Address, amount: bigint, blockNumber: bigint, blockHash: Hex): DirectFloorCandidate {
  return {
    schemaVersion: 1,
    kind: "agent-wallet-direct-floor-candidate",
    candidateId: `local-transfer-${amount}`,
    approvalSha256: canonicalSha256(inputApproval),
    policySha256: inputApproval.policySha256,
    context: {
      chainId: CHAIN_ID,
      currentBlockNumber: blockNumber.toString(),
      currentBlockHash: blockHash,
      senderNonce: "0",
      walletAddress: accounts.owner.address,
      tokenAddress: token,
      assetBalance: INITIAL_BALANCE.toString(),
    },
    execution: {
      chainId: CHAIN_ID,
      fromAddress: accounts.owner.address,
      toAddress: token,
      value: "0",
      data: encodeFunctionData({ abi: ERC20_TRANSFER_ABI, functionName: "transfer", args: [accounts.recipient.address, amount] }),
      gas: GAS.toString(),
      nonce: "0",
    },
    effect: {
      walletAddress: accounts.owner.address,
      tokenAddress: token,
      recipientAddress: accounts.recipient.address,
      transferAmount: amount.toString(),
      afterAssetBalance: (INITIAL_BALANCE - amount).toString(),
    },
  };
}

async function runCell(runtime: ExperimentRuntime, scenario: Scenario, route: Route): Promise<CellEvidence> {
  const { rpcUrl, chain, publicClient, token, initialBlock } = runtime;
  const spenderWallet = createWalletClient({ account: accounts.spender, chain, transport: http(rpcUrl) });
  try {
    const inputApproval = approval(token);
    const inputCandidate = candidate(inputApproval, token, scenario.candidateAmount, initialBlock.number, initialBlock.hash);
    const bundle = parseAgentWalletDirectBundleForEvaluation({
      schemaVersion: 1,
      kind: "agent-wallet-direct-floor-bundle",
      approval: inputApproval,
      candidate: inputCandidate,
    } satisfies AgentWalletDirectBundle);
    if (JSON.stringify(bundle).includes(ANVIL_DEFAULT_PRIVATE_KEYS[1].slice(2))) {
      throw new Error("candidate proposal unexpectedly contains the candidate signer key");
    }

    const readOwnerBalance = () => publicClient.readContract({ address: token, abi: tokenAbi, functionName: "balanceOf", args: [accounts.owner.address] });
    const readOwnerNonce = () => publicClient.getTransactionCount({ address: accounts.owner.address, blockTag: "pending" });
    const readBalance = (owner: Address) => publicClient.readContract({ address: token, abi: tokenAbi, functionName: "balanceOf", args: [owner] });
    const [actualInitialBalance, actualInitialNonce, initialAllowance, actualInitialBlock] = await Promise.all([
      readOwnerBalance(),
      readOwnerNonce(),
      publicClient.readContract({ address: token, abi: tokenAbi, functionName: "allowance", args: [accounts.owner.address, accounts.spender.address] }),
      publicClient.getBlock({ blockTag: "latest" }),
    ]);
    if (actualInitialBalance !== INITIAL_BALANCE || actualInitialNonce !== 0 || initialAllowance !== INITIAL_BALANCE
        || actualInitialBlock.number !== initialBlock.number || actualInitialBlock.hash !== initialBlock.hash) {
      throw new Error("cell did not start from the captured post-deployment baseline");
    }
    const events: EventEvidence[] = [];
    const recordEvent = async (event: string, detail?: string, transactionHash?: Hex, receiptStatus?: "success" | "reverted") => {
      events.push({
        sequence: events.length + 1,
        event,
        ownerBalance: (await readOwnerBalance()).toString(),
        ownerNonce: (await readOwnerNonce()).toString(),
        ...(detail ? { detail } : {}),
        ...(transactionHash ? { transactionHash } : {}),
        ...(receiptStatus ? { receiptStatus } : {}),
      });
    };

    await recordEvent("bundle-shape-validated", "No floor decision was made by the shape-only parser.");
    let gateInvocations = 0;
    let simulations = 0;
    let contextReads = 0;
    let floorEvaluationCalls = 0;
    let gateAccepted: boolean | null = null;
    let reasonCodes: string[] = [];
    let externalHash: Hex | null = null;
    let externalStatus: "success" | null = null;
    let externalTransferEvent: TransferEventEvidence | null = null;
    let signerCalls = 0;
    let signedRaw: Hex | null = null;
    let rawHash: Hex | null = null;
    let candidateReceipt: CellEvidence["candidateReceipt"] = null;
    let confirmedTransaction: CellEvidence["confirmedTransaction"] = null;

    const runSharedGate = async (): Promise<boolean> => {
      gateInvocations += 1;
      await publicClient.call({
        account: accounts.owner.address,
        to: token,
        data: bundle.candidate.execution.data,
        gas: GAS,
      });
      simulations += 1;
      await recordEvent("gate-simulation-completed", "Exact candidate call simulated successfully.");
      const [currentBalance, currentNonce] = await Promise.all([readOwnerBalance(), readOwnerNonce()]);
      contextReads += 1;
      if (currentBalance.toString() !== bundle.candidate.context.assetBalance || currentNonce.toString() !== bundle.candidate.context.senderNonce) {
        gateAccepted = false;
        reasonCodes = ["STALE_CAPTURED_CONTEXT"];
        await recordEvent(
          "gate-context-rejected",
          `capturedBalance=${bundle.candidate.context.assetBalance}; currentBalance=${currentBalance}; capturedNonce=${bundle.candidate.context.senderNonce}; currentNonce=${currentNonce}`,
        );
        return false;
      }
      floorEvaluationCalls += 1;
      const decision = evaluateDirectFloor(bundle.approval, bundle.candidate);
      gateAccepted = decision.accepted;
      reasonCodes = [...decision.reasonCodes];
      await recordEvent("gate-evaluated", `accepted=${decision.accepted}; reasons=${decision.reasonCodes.join(",") || "none"}`);
      return decision.accepted;
    };

    const applyExternalSchedule = async (): Promise<void> => {
      if (scenario.externalDebit === 0n) {
        await recordEvent("external-debit-skipped", "scheduled amount=0");
        return;
      }
      externalHash = await spenderWallet.writeContract({
        account: accounts.spender,
        chain,
        address: token,
        abi: tokenAbi,
        functionName: "transferFrom",
        args: [accounts.owner.address, accounts.externalRecipient.address, scenario.externalDebit],
        gas: GAS,
      });
      const receipt = await publicClient.waitForTransactionReceipt({ hash: externalHash });
      externalStatus = receipt.status === "success" ? "success" : null;
      if (receipt.status !== "success") throw new Error("scheduled external debit reverted");
      externalTransferEvent = requireTransferEvent(receipt, token, {
        from: accounts.owner.address,
        to: accounts.externalRecipient.address,
        value: scenario.externalDebit,
      });
      await recordEvent("external-debit-confirmed", `amount=${scenario.externalDebit}`, externalHash, "success");
    };

    let maySign = true;
    if (route === "no-application-gate") {
      await recordEvent("gate-position-control", "No application floor evaluation or context revalidation.");
      await applyExternalSchedule();
    } else if (route === "pre-submit") {
      maySign = await runSharedGate();
      if (maySign) await applyExternalSchedule();
    } else {
      await recordEvent("pre-submit-position", "No evaluation at this position.");
      await applyExternalSchedule();
      maySign = await runSharedGate();
    }

    const transactionFields = {
      chainId: CHAIN_ID,
      from: accounts.owner.address,
      to: token,
      value: "0",
      data: bundle.candidate.execution.data,
      gas: GAS.toString(),
      nonce: 0,
      maxFeePerGas: MAX_FEE_PER_GAS.toString(),
      maxPriorityFeePerGas: MAX_PRIORITY_FEE_PER_GAS.toString(),
      type: "eip1559" as const,
    };

    if (maySign) {
      signerCalls += 1;
      signedRaw = await accounts.owner.signTransaction({
        chainId: CHAIN_ID,
        to: token,
        value: 0n,
        data: bundle.candidate.execution.data,
        gas: GAS,
        nonce: 0,
        maxFeePerGas: MAX_FEE_PER_GAS,
        maxPriorityFeePerGas: MAX_PRIORITY_FEE_PER_GAS,
        type: "eip1559",
      });
      rawHash = keccak256(signedRaw);
      await recordEvent("candidate-signed", `rawTransactionHash=${rawHash}`);
      const sentHash = await publicClient.sendRawTransaction({ serializedTransaction: signedRaw });
      if (sentHash !== rawHash) throw new Error("raw transaction hash mismatch");
      const receipt = await publicClient.waitForTransactionReceipt({ hash: sentHash });
      const confirmed = await publicClient.getTransaction({ hash: sentHash });
      if (confirmed.chainId === undefined) throw new Error("confirmed candidate transaction omitted chainId");
      const transferEvent = requireTransferEvent(receipt, token, {
        from: accounts.owner.address,
        to: accounts.recipient.address,
        value: scenario.candidateAmount,
      });
      candidateReceipt = {
        transactionHash: sentHash,
        status: receipt.status,
        blockNumber: receipt.blockNumber.toString(),
        blockHash: receipt.blockHash,
        gasUsed: receipt.gasUsed.toString(),
        transferEvent,
      };
      confirmedTransaction = {
        chainId: confirmed.chainId,
        from: confirmed.from,
        to: confirmed.to as Address,
        value: confirmed.value.toString(),
        input: confirmed.input,
        gas: confirmed.gas.toString(),
        nonce: confirmed.nonce,
        maxFeePerGas: confirmed.maxFeePerGas?.toString() ?? null,
        maxPriorityFeePerGas: confirmed.maxPriorityFeePerGas?.toString() ?? null,
        type: confirmed.type,
      };
      await recordEvent("candidate-receipt-confirmed", `status=${receipt.status}`, sentHash, receipt.status);
    } else {
      await recordEvent("candidate-signing-skipped", `reasons=${reasonCodes.join(",")}`);
    }

    const [ownerBalance, recipientBalance, externalRecipientBalance] = await Promise.all([
      readOwnerBalance(), readBalance(accounts.recipient.address), readBalance(accounts.externalRecipient.address),
    ]);
    return {
      scenario: scenario.name,
      route,
      rpcUrl,
      policySha256: bundle.approval.policySha256,
      candidateSha256: canonicalSha256(bundle.candidate),
      executionSha256: canonicalSha256(bundle.candidate.execution),
      candidateTransaction: transactionFields,
      initial: {
        ownerBalance: actualInitialBalance.toString(),
        ownerNonce: actualInitialNonce.toString(),
        allowance: initialAllowance.toString(),
        blockNumber: initialBlock.number.toString(),
        blockHash: initialBlock.hash,
      },
      gate: {
        position: route,
        invocations: gateInvocations,
        simulations,
        contextReads,
        floorEvaluationCalls,
        accepted: gateAccepted,
        reasonCodes,
      },
      externalDebit: {
        scheduledAmount: scenario.externalDebit.toString(),
        transactionHash: externalHash,
        receiptStatus: externalStatus,
        transferEvent: externalTransferEvent,
      },
      candidateSignerCalls: signerCalls,
      signedRawTransaction: signedRaw,
      rawTransactionHash: rawHash,
      candidateReceipt,
      confirmedTransaction,
      postState: {
        ownerBalance: ownerBalance.toString(),
        recipientBalance: recipientBalance.toString(),
        externalRecipientBalance: externalRecipientBalance.toString(),
        floorViolated: ownerBalance < FLOOR,
      },
      outcome: candidateReceipt?.status === "success" ? "executed" : "rejected",
      events,
    };
  } catch (error) {
    throw new Error(`${scenario.name}/${route} failed: ${String(error)}`);
  }
}

function assertOutcomes(cells: CellEvidence[]): void {
  const find = (scenario: ScenarioName, route: Route) => {
    const cell = cells.find((value) => value.scenario === scenario && value.route === route);
    if (!cell) throw new Error(`missing cell ${scenario}/${route}`);
    return cell;
  };
  for (const route of routes) {
    const benign = find("benign-stable", route);
    if (benign.outcome !== "executed" || benign.postState.ownerBalance !== "900000" || benign.postState.floorViolated) {
      throw new Error(`unexpected benign outcome for ${route}`);
    }
  }
  const violatingControl = find("floor-violating-stable", "no-application-gate");
  if (violatingControl.outcome !== "executed" || !violatingControl.postState.floorViolated) throw new Error("control did not expose stable floor violation");
  for (const route of ["pre-submit", "pre-sign"] as const) {
    const cell = find("floor-violating-stable", route);
    if (cell.outcome !== "rejected" || cell.candidateSignerCalls !== 0 || !cell.gate.reasonCodes.includes("ASSET_BALANCE_FLOOR_VIOLATION")) {
      throw new Error(`stable violation was not rejected before signing by ${route}`);
    }
  }
  for (const route of ["no-application-gate", "pre-submit"] as const) {
    const cell = find("late-external-debit", route);
    if (cell.outcome !== "executed" || cell.postState.ownerBalance !== "400000" || !cell.postState.floorViolated) {
      throw new Error(`late drift did not expose unsafe execution for ${route}`);
    }
  }
  const preSign = find("late-external-debit", "pre-sign");
  if (preSign.outcome !== "rejected" || preSign.candidateSignerCalls !== 0 || preSign.postState.ownerBalance !== "800000"
      || preSign.postState.floorViolated || !preSign.gate.reasonCodes.includes("STALE_CAPTURED_CONTEXT")) {
    throw new Error("pre-sign route did not reject stale context before candidate signing");
  }
  for (const cell of cells) {
    if (cell.route === "no-application-gate") {
      if (cell.gate.invocations !== 0 || cell.gate.simulations !== 0 || cell.gate.contextReads !== 0 || cell.gate.floorEvaluationCalls !== 0) {
        throw new Error(`control unexpectedly invoked the application gate for ${cell.scenario}`);
      }
    } else if (cell.gate.invocations !== 1 || cell.gate.simulations !== 1 || cell.gate.contextReads !== 1
        || cell.gate.floorEvaluationCalls !== (cell.scenario === "late-external-debit" && cell.route === "pre-sign" ? 0 : 1)) {
      throw new Error(`protected route did not invoke the same complete gate once for ${cell.scenario}/${cell.route}`);
    }
    if (cell.outcome === "executed") {
      if (cell.candidateSignerCalls !== 1 || !cell.signedRawTransaction || !cell.rawTransactionHash
          || !cell.candidateReceipt || cell.candidateReceipt.status !== "success" || !cell.confirmedTransaction
          || cell.confirmedTransaction.from.toLowerCase() !== cell.candidateTransaction.from.toLowerCase()
          || cell.confirmedTransaction.to.toLowerCase() !== cell.candidateTransaction.to.toLowerCase()
          || cell.confirmedTransaction.input.toLowerCase() !== cell.candidateTransaction.data.toLowerCase()
          || cell.confirmedTransaction.value !== cell.candidateTransaction.value
          || cell.confirmedTransaction.gas !== cell.candidateTransaction.gas
          || cell.confirmedTransaction.nonce !== cell.candidateTransaction.nonce
          || cell.confirmedTransaction.chainId !== cell.candidateTransaction.chainId
          || cell.confirmedTransaction.maxFeePerGas !== cell.candidateTransaction.maxFeePerGas
          || cell.confirmedTransaction.maxPriorityFeePerGas !== cell.candidateTransaction.maxPriorityFeePerGas
          || cell.confirmedTransaction.type !== cell.candidateTransaction.type) {
        throw new Error(`executed cell lacks exact real-sign/receipt evidence for ${cell.scenario}/${cell.route}`);
      }
      const scenarioInput = scenarios.find((value) => value.name === cell.scenario);
      const transfer = cell.candidateReceipt.transferEvent;
      if (!scenarioInput || transfer.transactionHash !== cell.candidateReceipt.transactionHash
          || transfer.contractAddress.toLowerCase() !== cell.candidateTransaction.to.toLowerCase()
          || transfer.from.toLowerCase() !== cell.candidateTransaction.from.toLowerCase()
          || transfer.to.toLowerCase() !== accounts.recipient.address.toLowerCase()
          || transfer.value !== scenarioInput.candidateAmount.toString()) {
        throw new Error(`executed cell lacks matching Transfer-log evidence for ${cell.scenario}/${cell.route}`);
      }
    } else if (cell.candidateSignerCalls !== 0 || cell.signedRawTransaction !== null || cell.rawTransactionHash !== null
        || cell.candidateReceipt !== null || cell.confirmedTransaction !== null) {
      throw new Error(`rejected cell reached candidate signing or broadcast for ${cell.scenario}/${cell.route}`);
    }
  }
  const lateCells = routes.map((route) => find("late-external-debit", route));
  if (new Set(lateCells.map((cell) => cell.externalDebit.transactionHash)).size !== 1
      || lateCells.some((cell) => {
        const transfer = cell.externalDebit.transferEvent;
        return cell.externalDebit.receiptStatus !== "success" || cell.externalDebit.scheduledAmount !== "200000"
          || !transfer || transfer.transactionHash !== cell.externalDebit.transactionHash
          || transfer.contractAddress.toLowerCase() !== cell.candidateTransaction.to.toLowerCase()
          || transfer.from.toLowerCase() !== cell.candidateTransaction.from.toLowerCase()
          || transfer.to.toLowerCase() !== accounts.externalRecipient.address.toLowerCase()
          || transfer.value !== "200000";
      })) {
    throw new Error("late external debit schedule was not identical and confirmed across routes");
  }
  if (cells.filter((cell) => cell.scenario !== "late-external-debit")
      .some((cell) => cell.externalDebit.transactionHash !== null || cell.externalDebit.receiptStatus !== null
        || cell.externalDebit.transferEvent !== null)) {
    throw new Error("unscheduled scenarios unexpectedly contain external-debit evidence");
  }
  const eventNames = (cell: CellEvidence) => cell.events.map((event) => event.event);
  const before = eventNames(find("late-external-debit", "pre-submit"));
  if (!(before.indexOf("gate-evaluated") < before.indexOf("external-debit-confirmed")
      && before.indexOf("external-debit-confirmed") < before.indexOf("candidate-signed"))) {
    throw new Error("pre-submit late-debit event order is invalid");
  }
  const after = eventNames(preSign);
  if (!(after.indexOf("external-debit-confirmed") < after.indexOf("gate-simulation-completed")
      && after.indexOf("gate-context-rejected") < after.indexOf("candidate-signing-skipped"))) {
    throw new Error("pre-sign late-debit event order is invalid");
  }
  for (const scenario of scenarios) {
    const compared = routes.map((route) => find(scenario.name, route));
    if (new Set(compared.map((cell) => cell.policySha256)).size !== 1
        || new Set(compared.map((cell) => cell.executionSha256)).size !== 1
        || new Set(compared.map((cell) => cell.candidateSha256)).size !== 1
        || new Set(compared.map((cell) => JSON.stringify(cell.candidateTransaction))).size !== 1
        || new Set(compared.map((cell) => JSON.stringify(cell.initial))).size !== 1) {
      throw new Error(`route inputs were not identical for ${scenario.name}`);
    }
  }
}

async function main(): Promise<void> {
  const bytecode = compileFixture();
  const rpcUrl = `http://127.0.0.1:${BASE_PORT}`;
  const anvil = spawn(
    "anvil",
    ["--port", BASE_PORT.toString(), "--chain-id", CHAIN_ID.toString(), "--timestamp", "1786068491", "--silent"],
    { stdio: "ignore", windowsHide: true },
  );
  try {
    await waitForAnvil(rpcUrl, anvil);
    const chain = defineChain({
      id: CHAIN_ID,
      name: "Gate Position Local Anvil",
      nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
      rpcUrls: { default: { http: [rpcUrl] } },
    });
    const publicClient = createPublicClient({ chain, transport: http(rpcUrl) });
    const deployerWallet = createWalletClient({ account: accounts.deployer, chain, transport: http(rpcUrl) });
    const spenderWallet = createWalletClient({ account: accounts.spender, chain, transport: http(rpcUrl) });
    const deploymentHash = await deployerWallet.deployContract({
      abi: tokenAbi,
      account: accounts.deployer,
      bytecode,
      args: [accounts.owner.address, accounts.spender.address, INITIAL_BALANCE],
    });
    const deploymentReceipt = await publicClient.waitForTransactionReceipt({ hash: deploymentHash });
    if (deploymentReceipt.status !== "success" || !deploymentReceipt.contractAddress) throw new Error("fixture deployment failed");
    const initialBlock = await publicClient.getBlock({ blockNumber: deploymentReceipt.blockNumber });
    if (!initialBlock.hash) throw new Error("initial block has no hash");
    const runtime: ExperimentRuntime = {
      rpcUrl,
      chain,
      publicClient,
      token: deploymentReceipt.contractAddress,
      initialBlock: { number: initialBlock.number, hash: initialBlock.hash },
    };
    let snapshotId = await publicClient.request({ method: "evm_snapshot" as never, params: [] as never }) as string;
    const cells: CellEvidence[] = [];
    for (const scenario of scenarios) {
      for (const route of routes) {
        const reverted = await publicClient.request({ method: "evm_revert" as never, params: [snapshotId] as never }) as boolean;
        if (!reverted) throw new Error(`failed to reset state before ${scenario.name}/${route}`);
        snapshotId = await publicClient.request({ method: "evm_snapshot" as never, params: [] as never }) as string;
        cells.push(await runCell(runtime, scenario, route));
      }
    }
    assertOutcomes(cells);
    const git = (args: string[]) => {
      const result = spawnSync("git", args, { cwd: resolve(process.cwd(), ".."), encoding: "utf8", windowsHide: true });
      if (result.status !== 0) throw new Error(`git ${args.join(" ")} failed`);
      return result.stdout.trim();
    };
    const fileSha256 = async (path: string) => createHash("sha256").update(await readFile(path)).digest("hex");
    const sourceFiles = {
      harness: resolve(process.cwd(), "src", "gate-position-comparison.ts"),
      token: resolve(process.cwd(), "src", "GatePositionToken.sol"),
      evaluator: resolve(process.cwd(), "src", "agent-wallet-direct-floor.ts"),
    };
    const evidence = {
      schemaVersion: 1,
      kind: "local-gate-position-comparison",
      generatedAt: new Date().toISOString(),
      sourceRevision: git(["rev-parse", "HEAD"]),
      sourceDirtyProvenance: git(["status", "--short"]).split(/\r?\n/).filter(Boolean),
      sourceSha256: {
        harness: await fileSha256(sourceFiles.harness),
        token: await fileSha256(sourceFiles.token),
        evaluator: await fileSha256(sourceFiles.evaluator),
      },
      scope: {
        network: "one isolated local Anvil node, reverted to the same post-deployment snapshot before every cell",
        token: "locally deployed GatePositionToken (GPTT), 6 decimals; not Sepolia USDC",
        control: "no-application-gate is an unguarded local signing control, not Agent Wallet Guard or Delegation Framework caveats",
        signer: "real viem LocalAccount.signTransaction using Anvil public test key #1 held outside the proposal",
      },
      parameters: {
        chainId: CHAIN_ID,
        initialBalance: INITIAL_BALANCE.toString(),
        floor: FLOOR.toString(),
        scenarios: scenarios.map((scenario) => ({ name: scenario.name, candidateAmount: scenario.candidateAmount.toString(), externalDebit: scenario.externalDebit.toString() })),
      },
      cells,
    };
    await mkdir(dirname(OUTPUT_PATH), { recursive: true });
    await writeFile(OUTPUT_PATH, `${JSON.stringify(evidence, null, 2)}\n`, "utf8");
    console.log(JSON.stringify({ output: OUTPUT_PATH, cells: cells.length, assertions: "passed" }));
  } finally {
    await stopAnvil(anvil);
  }
}

await main();
