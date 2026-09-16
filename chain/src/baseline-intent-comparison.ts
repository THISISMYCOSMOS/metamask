/** Local matched experiment: real six-caveat DF vs the same DF + existing floor gate.
 * Run from chain/: npx tsx src/baseline-intent-comparison.ts
 * No remote RPC, provider credentials, impersonation, mocked enforcer, or product edits.
 */
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import {
  createPublicClient, createWalletClient, decodeAbiParameters, decodeEventLog,
  decodeFunctionData, encodeAbiParameters, encodeFunctionData, http, keccak256,
  parseAbi, type Address, type Hex,
} from "viem";
import { mainnet } from "viem/chains";
import { privateKeyToAccount } from "viem/accounts";
import { ANVIL_DEFAULT_PRIVATE_KEYS, ENTRYPOINT_ADDRESS, PINNED_COMMIT } from "./config.js";
import { assertFrameworkPinnedAndClean } from "./deploy.js";
import {
  buildRootDelegation, CAVEAT_ORDER, encodeAllowedMethods, encodeAllowedTargets,
  encodeERC20BalanceChange, encodeERC20PeriodTransfer, encodeSingleExecution,
  encodeTimestamp, encodeValueLte, hashDelegationStruct, signDelegation, toOnchainDelegation,
} from "./delegation.js";
import {
  DelegatedFloorGate, evaluateDelegatedFloor, verifyDelegatedExecutionBinding,
  DELEGATION_ARRAY_ABI_TYPE, ERC20_TRANSFER_ABI, MODE_CODE_SIMPLE_SINGLE,
  REDEEM_DELEGATIONS_ABI, type BalanceFloorApproval, type DelegatedFloorCandidate,
  type DelegatedTransferContext,
} from "./delegated-floor-gate.js";
import { canonicalSha256, PreExecutionGateError, type ExecutionRequest } from "./pre-execution-gate.js";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const FRAMEWORK = join(ROOT, "chain/lib/delegation-framework");
const OUTPUT = join(ROOT, "traces/baseline-intent-comparison.json");
const PORT = 18571;
const RPC = `http://127.0.0.1:${PORT}`;
const START = 1786068491n;
const INITIAL = 10_000_000_000n;
const FLOOR = 9_700_000_000n;
const CAP = 500_000_000n;
const PERIOD_CAP = 2_000_000_000n;
const account = ANVIL_DEFAULT_PRIVATE_KEYS.slice(0, 4).map(key => privateKeyToAccount(key));
const [deployer, owner, delegate, recipient] = account;
const tokenAbi = parseAbi([
  "constructor(address holder, uint256 amount)",
  "function balanceOf(address holder) view returns (uint256)",
  "function transfer(address to, uint256 amount) returns (bool)",
  "event Transfer(address indexed from, address indexed to, uint256 value)",
]);
const serialize = (value: unknown) => JSON.stringify(value, (_, item) => typeof item === "bigint" ? item.toString() : item, 2);
const plain = <T = any>(value: unknown): T => JSON.parse(serialize(value));
const hashFile = (path: string) => createHash("sha256").update(readFileSync(path)).digest("hex");
const commandRecords: object[] = [];
function command(binary: string, args: string[], cwd = ROOT): string {
  const startedAt = new Date().toISOString();
  const result = spawnSync(binary, args, { cwd, encoding: "utf8", windowsHide: true, maxBuffer: 64 * 1024 * 1024 });
  commandRecords.push({ binary, args, cwd, startedAt, finishedAt: new Date().toISOString(), exitCode: result.status });
  if (result.status !== 0) throw new Error(`${binary} failed: ${result.stderr || result.error}`);
  return result.stdout.trim();
}

async function main() {
  const startedAt = new Date().toISOString();
  assertFrameworkPinnedAndClean();
  // Compiler outputs use a new temp directory; existing submodule broadcasts/out/cache are preserved.
  const buildDirectory = mkdtempSync(join(tmpdir(), "baseline-intent-comparison-"));
  const frameworkOut = join(buildDirectory, "framework-out");
  command("forge", ["build", "--force", "--quiet", "--out", frameworkOut, "--cache-path", join(buildDirectory, "framework-cache")], FRAMEWORK);
  const fixtureOut = join(buildDirectory, "fixture-out");
  command("forge", ["build", "src/BaselineIntentComparisonToken.sol", "--root", join(ROOT, "chain"), "--out", fixtureOut,
    "--cache-path", join(buildDirectory, "fixture-cache"), "--use", "0.8.23", "--quiet"]);
  const artifact = (name: string, fixture = false) => JSON.parse(readFileSync(join(fixture ? fixtureOut : frameworkOut, `${name}.sol`, `${name}.json`), "utf8"));
  const client = createPublicClient({ chain: mainnet, transport: http(RPC), cacheTime: 0 });
  try {
    await client.getChainId();
    throw new Error(`Refusing to reuse occupied local port ${PORT}`);
  } catch (error) {
    if (String(error).includes("Refusing")) throw error;
  }
  const anvilArgs = ["--host", "127.0.0.1", "--port", String(PORT), "--chain-id", "1", "--timestamp", String(START), "--silent"];
  const anvil = spawn("anvil", anvilArgs, { windowsHide: true, stdio: "ignore" });
  const cells: any[] = [];
  const deployments: any[] = [];
  const evidence: any = { schemaVersion: 1, kind: "baseline-intent-comparison", startedAt, commands: commandRecords,
    scope: { network: "isolated local Anvil, chainId 1, no fork; no remote transactions", policy: "new assetBalanceFloor example, NOT old G3 portfolioValueFloor/cumulativeLossCap or its 20-step oracle trace",
      baseline: "actual signed redeemDelegations through pinned DelegationManager, HybridDeleGator proxy and six real enforcer classes",
      approval: "controlled local test approval envelope authorized for research; no claim of live user cryptographic policy approval",
      effect: "exact deterministic fixture transfer semantics and measured balances; no general ERC20 or multi-asset simulation claim",
      signerBoundary: "one root-delegation owner signature created before all cells; rejected guarded cell skips OUTER transaction signing and sending",
      entryPoint: "constructor address only; direct redeemDelegations path does not use EntryPoint/UserOperations" },
    parameters: plain({ initialBalance: INITIAL, floor: FLOOR, perRedemptionCap: CAP, periodCap: PERIOD_CAP, periodDuration: 86400n, timestampStart: START }),
    buildDirectory, anvil: { binary: "anvil", args: anvilArgs }, deployments, cells };
  try {
    let ready = false;
    for (let i = 0; i < 100; i++) {
      if (anvil.exitCode !== null) throw new Error("Anvil exited during startup");
      try { if (await client.getChainId() === 1) { ready = true; break; } } catch { /* startup */ }
      await delay(50);
    }
    assert(ready, "local Anvil startup timeout");
    assert.match(await client.request({ method: "web3_clientVersion" }), /anvil/i);
    const rpc = (method: string, params: unknown[] = []) => client.request({ method: method as never, params: params as never });
    await rpc("anvil_setBlockTimestampInterval", [1]);
    const wallet = createWalletClient({ account: deployer, chain: mainnet, transport: http(RPC) });
    const libraries = new Map<string, Address>();
    async function deploy(name: string, args: unknown[] = [], fixture = false, sourceFile = `${name}.sol`): Promise<Address> {
      const artifactPath = join(fixture ? fixtureOut : frameworkOut, basename(sourceFile), `${name}.json`);
      const compiled = JSON.parse(readFileSync(artifactPath, "utf8"));
      let bytecode = compiled.bytecode.object as string;
      const linkedLibraries: Record<string, Address> = {};
      for (const [source, refs] of Object.entries(compiled.bytecode.linkReferences ?? {})) {
        for (const [library, locations] of Object.entries(refs as Record<string, { start: number; length: number }[]>)) {
          const key = `${source}:${library}`;
          let address = libraries.get(key);
          if (!address) {
            address = await deploy(library, [], false, source);
            libraries.set(key, address);
          }
          linkedLibraries[key] = address;
          for (const { start, length } of locations) {
            assert.equal(length, 20, `unexpected library address length for ${key}`);
            const offset = (bytecode.startsWith("0x") ? 2 : 0) + start * 2;
            bytecode = bytecode.slice(0, offset) + address.slice(2) + bytecode.slice(offset + length * 2);
          }
        }
      }
      if (!bytecode.startsWith("0x")) bytecode = `0x${bytecode}`;
      assert.match(bytecode, /^0x(?:[0-9a-fA-F]{2})+$/, `${name} contains unresolved library references`);
      const tx = await wallet.deployContract({ abi: compiled.abi, bytecode: bytecode as Hex, args, gas: 12_000_000n });
      const receipt = await client.waitForTransactionReceipt({ hash: tx });
      assert.equal(receipt.status, "success", `${name} deployment failed`);
      assert(receipt.contractAddress);
      const runtime = await client.getCode({ address: receipt.contractAddress });
      assert(runtime && runtime !== "0x");
      deployments.push({ name, address: receipt.contractAddress, receipt: plain(receipt), creationBytecodeKeccak256: keccak256(bytecode as Hex), runtimeKeccak256: keccak256(runtime),
        linkedLibraries, artifactSha256: hashFile(artifactPath) });
      return receipt.contractAddress;
    }
    const manager = await deploy("DelegationManager", [deployer.address]);
    const hybrid = await deploy("HybridDeleGator", [manager, ENTRYPOINT_ADDRESS]);
    const initialize = encodeFunctionData({ abi: artifact("HybridDeleGator").abi, functionName: "initialize", args: [owner.address, [], [], []] });
    const delegator = await deploy("ERC1967Proxy", [hybrid, initialize]);
    assert.equal((await client.readContract({ address: delegator, abi: artifact("HybridDeleGator").abi, functionName: "owner" }) as string).toLowerCase(), owner.address.toLowerCase());
    const enforcers: Record<string, Address> = {};
    for (const name of CAVEAT_ORDER) enforcers[name] = await deploy(name);
    const token = await deploy("BaselineIntentComparisonToken", [delegator, INITIAL], true);
    const caveats = [
      { enforcer: enforcers.AllowedTargetsEnforcer, terms: encodeAllowedTargets([token]) },
      { enforcer: enforcers.AllowedMethodsEnforcer, terms: encodeAllowedMethods(["0xa9059cbb"]) },
      { enforcer: enforcers.ValueLteEnforcer, terms: encodeValueLte(0n) },
      { enforcer: enforcers.TimestampEnforcer, terms: encodeTimestamp(START, START + 604800n) },
      { enforcer: enforcers.ERC20PeriodTransferEnforcer, terms: encodeERC20PeriodTransfer(token, PERIOD_CAP, 86400n, START) },
      { enforcer: enforcers.ERC20BalanceChangeEnforcer, terms: encodeERC20BalanceChange(true, token, delegator, CAP) },
    ];
    const signed = await signDelegation(owner, manager, buildRootDelegation(delegate.address, delegator, caveats, 20260908n));
    const onchain = toOnchainDelegation(signed);
    const permissionContext = encodeAbiParameters([DELEGATION_ARRAY_ABI_TYPE], [[onchain]]);
    const delegationHash = hashDelegationStruct(signed);
    const protectionSha256 = canonicalSha256(plain(onchain));
    const policy = { schemaVersion: 1 as const, kind: "assetBalanceFloor" as const, policyId: "baseline-intent-comparison-floor", chainId: 1,
      walletAddress: delegator, tokenAddress: token, assetBalanceFloor: String(FLOOR) };
    const proposal = { schemaVersion: 1 as const, kind: "policy-proposal" as const, policySha256: canonicalSha256(policy), policy,
      intentText: "Keep at least 9700 local fixture tokens", source: "controlled test fixture; no model invocation" };
    const approval: BalanceFloorApproval = { schemaVersion: 1, kind: "approved-policy-envelope", approvalId: "baseline-intent-comparison-approval", approvalScope: "user",
      approvedBy: "controlled-local-test-owner", proposal, proposalSha256: canonicalSha256(proposal), policySha256: proposal.policySha256, confirmation: `APPROVE ${canonicalSha256(proposal)}` };
    Object.assign(evidence, { approval, signedDelegation: plain(onchain), delegationHash, protectionSha256, permissionContext,
      caveats: CAVEAT_ORDER.map((name, i) => ({ name, ...caveats[i] })) });
    const balance = (holder: Address) => client.readContract({ address: token, abi: tokenAbi, functionName: "balanceOf", args: [holder] });
    async function state() {
      const block = await client.getBlock({ blockTag: "latest" });
      return plain({ blockNumber: block.number, blockHash: block.hash, stateRoot: block.stateRoot, timestamp: block.timestamp,
        delegatorBalance: await balance(delegator), recipientBalance: await balance(recipient.address),
        delegateNonce: await client.getTransactionCount({ address: delegate.address, blockTag: "pending" }),
        delegateNativeBalance: await client.getBalance({ address: delegate.address }),
        periodicAllowance: await client.readContract({ address: enforcers.ERC20PeriodTransferEnforcer, abi: artifact("ERC20PeriodTransferEnforcer").abi,
          functionName: "periodicAllowances", args: [manager, delegationHash] }) });
    }
    async function context(): Promise<DelegatedTransferContext> {
      const current = await state();
      return { chainId: 1, currentBlockNumber: current.blockNumber, currentBlockHash: current.blockHash, delegateNonce: String(current.delegateNonce),
        delegationManagerAddress: manager, walletAddress: delegator, tokenAddress: token, assetBalance: current.delegatorBalance };
    }
    const initial = await state();
    evidence.initialState = initial;
    let snapshot = await rpc("evm_snapshot");
    const dataFor = (amount: bigint) => encodeFunctionData({ abi: REDEEM_DELEGATIONS_ABI, functionName: "redeemDelegations", args: [[permissionContext], [MODE_CODE_SIMPLE_SINGLE],
      [encodeSingleExecution(token, 0n, encodeFunctionData({ abi: ERC20_TRANSFER_ABI, functionName: "transfer", args: [recipient.address, amount] }))]] });
    // One hypothesis-specific negative control establishes that the existing per-call cap is active.
    let capRejection = "";
    try {
      await client.call({ account: delegate.address, to: manager, data: dataFor(CAP + 1_000_000n), gas: 1_500_000n });
    } catch (error) { capRejection = String(error); }
    assert.match(capRejection, /ERC20BalanceChangeEnforcer:exceeded-balance-decrease/);
    evidence.baselineNegativeControl = { amount: String(CAP + 1_000_000n), kind: "exact signed redeemDelegations eth_call", rejection: capRejection,
      unchangedState: await state(), expectedReason: "ERC20BalanceChangeEnforcer:exceeded-balance-decrease" };
    assert.deepEqual(await state(), initial);
    evidence.simulations = [];
    for (const scenario of [{ name: "benign", amount: 100_000_000n }, { name: "violating", amount: 500_000_000n }]) {
      assert.equal(await rpc("evm_revert", [snapshot]), true);
      snapshot = await rpc("evm_snapshot");
      assert.deepEqual(await state(), initial);
      const simulationData = dataFor(scenario.amount);
      // Observe effects by executing the EXACT signed delegation call on an isolated snapshot.
      // These simulation transactions are separate from operational cell signer/sender counters.
      const simulationRaw = await delegate.signTransaction({ chainId: 1, to: manager, data: simulationData, value: 0n, gas: 1_500_000n,
        nonce: Number(initial.delegateNonce), type: "eip1559", maxFeePerGas: 2_000_000_000n, maxPriorityFeePerGas: 1_000_000_000n });
      const simulationHash = await client.sendRawTransaction({ serializedTransaction: simulationRaw });
      const simulationReceipt = await client.waitForTransactionReceipt({ hash: simulationHash });
      assert.equal(simulationReceipt.status, "success");
      const simulatedPost = await state();
      const simulationTransfers = simulationReceipt.logs.filter(log => log.address.toLowerCase() === token.toLowerCase()).map(log => decodeEventLog({ abi: tokenAbi, topics: log.topics, data: log.data }));
      assert.equal(simulationTransfers.length, 1);
      assert.equal(simulationTransfers[0].eventName, "Transfer");
      assert.equal(simulationTransfers[0].args.from.toLowerCase(), delegator.toLowerCase());
      assert.equal(simulationTransfers[0].args.to.toLowerCase(), recipient.address.toLowerCase());
      assert.equal(simulationTransfers[0].args.value, scenario.amount);
      assert.equal(simulatedPost.delegatorBalance, String(INITIAL - scenario.amount));
      assert.equal(simulatedPost.recipientBalance, String(scenario.amount));
      assert.equal(await rpc("evm_revert", [snapshot]), true);
      snapshot = await rpc("evm_snapshot");
      assert.deepEqual(await state(), initial);
      evidence.simulations.push({ scenario: scenario.name, initial, exactExecutionData: simulationData, signedRawTransaction: simulationRaw,
        signerCalls: 1, senderCalls: 1, receipt: plain(simulationReceipt), decodedTransfers: plain(simulationTransfers), observedPost: simulatedPost,
        restorationSucceeded: true, restoredState: await state() });
      let matchedCandidate: DelegatedFloorCandidate | undefined;
      for (const route of ["df-caveats-only", "df-caveats-plus-result-gate"]) {
        assert.equal(await rpc("evm_revert", [snapshot]), true);
        snapshot = await rpc("evm_snapshot");
        assert.deepEqual(await state(), initial, "full observed initial state differs after reset");
        const candidate: DelegatedFloorCandidate = {
          schemaVersion: 1, kind: "delegated-floor-candidate", candidateId: `baseline-intent-comparison-${scenario.name}`,
          approvalSha256: canonicalSha256(approval), policySha256: approval.policySha256, context: await context(),
          execution: { chainId: 1, fromAddress: delegate.address, toAddress: manager, value: "0", gas: "1500000", nonce: String(initial.delegateNonce),
            data: simulationData },
          effect: { walletAddress: delegator, tokenAddress: token, recipientAddress: recipient.address, transferAmount: String(scenario.amount), afterAssetBalance: simulatedPost.delegatorBalance },
        };
        if (matchedCandidate) assert.deepEqual(candidate, matchedCandidate, "matched routes changed candidate");
        matchedCandidate = candidate;
        assert(verifyDelegatedExecutionBinding(candidate));
        const decodedOuter = decodeFunctionData({ abi: REDEEM_DELEGATIONS_ABI, data: candidate.execution.data });
        const [decodedDelegations] = decodeAbiParameters([DELEGATION_ARRAY_ABI_TYPE], decodedOuter.args[0][0]);
        assert.equal(encodeAbiParameters([DELEGATION_ARRAY_ABI_TYPE], [decodedDelegations]), permissionContext,
          "decoded signed delegation did not round-trip to the exact permission bytes");
        const cell: any = { scenario: scenario.name, route, initial: await state(), candidate, candidateSha256: canonicalSha256(candidate),
          policySha256: approval.policySha256, approvalSha256: canonicalSha256(approval), protectionSha256, executionSha256: canonicalSha256(candidate.execution),
          decodedDelegation: plain(decodedDelegations[0]), calldataRoundtripPassed: true,
          candidateSignerCalls: 0, candidateSenderCalls: 0, gateInvocations: 0, gateContextReads: 0, signedRawTransaction: null, receipt: null, events: [] };
        cells.push(cell);
        async function send(execution: Readonly<ExecutionRequest>) {
          assert.deepEqual(execution, candidate.execution);
          cell.events.push("outer-transaction-signing");
          cell.candidateSignerCalls++;
          const raw = await delegate.signTransaction({ chainId: 1, to: execution.toAddress, data: execution.data, value: BigInt(execution.value), gas: BigInt(execution.gas),
            nonce: Number(execution.nonce), type: "eip1559", maxFeePerGas: 2_000_000_000n, maxPriorityFeePerGas: 1_000_000_000n });
          cell.signedRawTransaction = raw;
          cell.candidateSenderCalls++;
          const tx = await client.sendRawTransaction({ serializedTransaction: raw });
          assert.equal(tx, keccak256(raw));
          const receipt = await client.waitForTransactionReceipt({ hash: tx });
          cell.receipt = plain(receipt);
          assert.equal(receipt.status, "success");
          const confirmed = await client.getTransaction({ hash: tx });
          assert.equal(confirmed.input, execution.data);
          assert.equal(confirmed.from.toLowerCase(), delegate.address.toLowerCase());
          assert.equal(confirmed.to?.toLowerCase(), manager.toLowerCase());
          assert.equal(confirmed.nonce, Number(execution.nonce));
          assert.equal(confirmed.value, 0n);
          assert.equal(confirmed.chainId, 1);
          assert.equal(confirmed.gas, BigInt(execution.gas));
          cell.confirmedTransaction = plain(confirmed);
          cell.decodedEvents = receipt.logs.flatMap(log => {
            const abi = log.address.toLowerCase() === token.toLowerCase() ? tokenAbi
              : log.address.toLowerCase() === enforcers.ERC20PeriodTransferEnforcer.toLowerCase() ? artifact("ERC20PeriodTransferEnforcer").abi
              : log.address.toLowerCase() === manager.toLowerCase() ? artifact("DelegationManager").abi : null;
            if (!abi) return [];
            return [plain({ address: log.address, logIndex: log.logIndex, ...(decodeEventLog({ abi, topics: log.topics, data: log.data }) as object) })];
          });
          const transfer = cell.decodedEvents.filter((e: any) => e.eventName === "Transfer");
          assert.equal(transfer.length, 1);
          assert.equal(transfer[0].args.from.toLowerCase(), delegator.toLowerCase());
          assert.equal(transfer[0].args.to.toLowerCase(), recipient.address.toLowerCase());
          assert.equal(transfer[0].args.value, String(scenario.amount));
          const period = cell.decodedEvents.filter((e: any) => e.eventName === "TransferredInPeriod");
          assert.equal(period.length, 1);
          assert.equal(period[0].args.delegationHash, delegationHash);
          assert.equal(period[0].args.transferredInCurrentPeriod, String(scenario.amount));
          assert.equal(period[0].args.periodAmount, String(PERIOD_CAP));
          const callTrace: any = await rpc("debug_traceTransaction", [tx, { tracer: "callTracer" }]);
          const visited: string[] = [];
          function visit(call: any) { if (call.to) visited.push(call.to.toLowerCase()); for (const child of call.calls ?? []) visit(child); }
          visit(callTrace);
          cell.enforcerCallCounts = Object.fromEntries(CAVEAT_ORDER.map(name => [name, visited.filter(address => address === enforcers[name].toLowerCase()).length]));
          assert(Object.values(cell.enforcerCallCounts).every(value => Number(value) >= 2), "actual enforcer hooks absent from call trace");
          cell.callTrace = callTrace;
          cell.events.push("outer-transaction-confirmed");
          return tx;
        }
        if (route === "df-caveats-only") {
          await send(candidate.execution);
          cell.outcome = "executed";
        } else {
          cell.gateInvocations++;
          cell.events.push("result-gate-entered");
          cell.decision = evaluateDelegatedFloor(approval, candidate);
          try {
            await new DelegatedFloorGate().execute({ approval, candidate, readContext: async () => { cell.gateContextReads++; return context(); }, send });
            cell.outcome = "executed";
          } catch (error) {
            if (!(error instanceof PreExecutionGateError)) throw error;
            cell.outcome = "rejected";
            cell.rejection = error.message;
            cell.events.push("result-gate-rejected-before-outer-sign-and-send");
          }
        }
        cell.post = await state();
        cell.floorViolated = BigInt(cell.post.delegatorBalance) < FLOOR;
        if (scenario.name === "violating" && route === "df-caveats-plus-result-gate") {
          assert.equal(cell.outcome, "rejected");
          assert.deepEqual(cell.decision.reasonCodes, ["ASSET_BALANCE_FLOOR_VIOLATION"]);
          assert.equal(cell.candidateSignerCalls, 0); assert.equal(cell.candidateSenderCalls, 0);
          assert.equal(cell.signedRawTransaction, null); assert.equal(cell.receipt, null);
          assert.deepEqual(cell.post, cell.initial, "rejected cell changed chain state");
        } else {
          assert.equal(cell.outcome, "executed");
          assert.equal(cell.candidateSignerCalls, 1); assert.equal(cell.candidateSenderCalls, 1);
          assert.equal(cell.post.delegatorBalance, String(INITIAL - scenario.amount));
          assert.equal(cell.post.recipientBalance, String(scenario.amount));
          assert.equal(cell.floorViolated, scenario.name === "violating");
          assert.equal(cell.post.delegateNonce, initial.delegateNonce + 1);
        }
        console.log(`${scenario.name}/${route}: ${cell.outcome}; balance=${cell.post.delegatorBalance}; sign=${cell.candidateSignerCalls}; send=${cell.candidateSenderCalls}`);
      }
    }
    assert.equal(cells.length, 4);
    for (const scenario of ["benign", "violating"]) {
      const pair = cells.filter(cell => cell.scenario === scenario);
      for (const field of ["candidateSha256", "policySha256", "approvalSha256", "protectionSha256", "executionSha256"]) assert.equal(pair[0][field], pair[1][field]);
      assert.deepEqual(pair[0].initial, pair[1].initial);
    }
    evidence.assertions = "passed";
    evidence.provenance = {
      rootRevision: command("git", ["rev-parse", "HEAD"]), frameworkRevision: command("git", ["rev-parse", "HEAD"], FRAMEWORK), expectedFrameworkRevision: PINNED_COMMIT,
      rootDirtyStatus: command("git", ["status", "--short"]), frameworkDirtyStatus: command("git", ["status", "--short"], FRAMEWORK),
      node: process.version, anvil: command("anvil", ["--version"]), forge: command("forge", ["--version"]),
      viem: JSON.parse(readFileSync(join(ROOT, "chain/node_modules/viem/package.json"), "utf8")).version,
      sourceSha256: Object.fromEntries(["chain/src/baseline-intent-comparison.ts", "chain/src/BaselineIntentComparisonToken.sol", "chain/src/delegated-floor-gate.ts", "chain/src/delegation.ts", "chain/src/pre-execution-gate.ts"].map(path => [path, hashFile(join(ROOT, path))])),
    };
  } catch (error) {
    evidence.assertions = "failed";
    evidence.error = String(error);
    throw error;
  } finally {
    evidence.finishedAt = new Date().toISOString();
    mkdirSync(dirname(OUTPUT), { recursive: true });
    const destination = evidence.assertions === "passed" ? OUTPUT : OUTPUT.replace(/\.json$/, ".failed.json");
    writeFileSync(destination, `${serialize(evidence)}\n`);
    anvil.kill();
    await Promise.race([new Promise<void>(done => anvil.once("exit", () => done())), delay(2000)]);
    console.log(`Evidence: ${destination}`);
  }
}
await main();
