// Step 2 — 재현 가능한 배포 파이프라인.
// 업스트림 forge script 2개를 재사용해 DelegationManager + 구체 enforcer 37종을 배포하고,
// delegator 스마트계정(ERC1967Proxy + HybridDeleGator)을 배포한 뒤 매니페스트를 남긴다.
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { encodeFunctionData, keccak256, encodeAbiParameters, pad, toHex, type Address, type Hex } from "viem";

import {
  ANVIL_RPC,
  ANVIL_DEFAULT_PRIVATE_KEYS,
  BASE_TIMESTAMP,
  DEPLOY_SALT,
  ENTRYPOINT_ADDRESS,
  EXPECTED_ADDRESSES,
  EXPECTED_ENFORCER_COUNT,
  FORK_BLOCK_HASH,
  FORK_BLOCK_NUMBER,
  FORK_CHAIN_ID,
  PINNED_COMMIT,
  STARTING_ETH_BALANCE,
  STARTING_USDC_BALANCE,
  USDC_ADDRESS,
} from "./config.js";
import { assertLocalAnvilFork } from "./guard.js";
import { accounts, addressOf, publicClient, walletFor } from "./accounts.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = resolve(__dirname, "..", "..");
export const FRAMEWORK_DIR = join(REPO_ROOT, "chain", "lib", "delegation-framework");
export const MANIFEST_PATH = join(REPO_ROOT, "chain", "deployments", "manifest.json");

const FOUNDRY_BIN = join(process.env.HOME ?? process.env.USERPROFILE ?? "", ".foundry", "bin");

export interface Manifest {
  pinnedCommit: string;
  fork: { chainId: number; blockNumber: string; blockHash: string };
  salt: string;
  deployer: Address;
  delegationManager: Address;
  hybridDeleGatorImpl: Address;
  multiSigDeleGatorImpl: Address;
  delegator: Address;
  delegatorOwner: Address;
  enforcers: Record<string, Address>;
}

/** forge script를 돌리고 stdout을 돌려준다. 업스트림 스크립트의 console2.log가 정본 출력이다. */
function runForgeScript(script: string, env: Record<string, string>): string {
  return execFileSync(
    join(FOUNDRY_BIN, "forge"),
    [
      "script",
      script,
      "--rpc-url",
      ANVIL_RPC,
      "--private-key",
      ANVIL_DEFAULT_PRIVATE_KEYS[0],
      "--broadcast",
      "--skip-simulation",
    ],
    {
      cwd: FRAMEWORK_DIR,
      encoding: "utf8",
      maxBuffer: 64 * 1024 * 1024,
      env: { ...process.env, ...env, PATH: `${FOUNDRY_BIN}:${process.env.PATH ?? ""}` },
    },
  );
}

/**
 * "  Name: 0xabc..." 형태의 console2.log 출력만 뽑는다.
 * 업스트림 배포 스크립트의 import 목록이 배포 대상의 정본이므로, out/을 훑지 않는다
 * (out/에는 MockCaveatEnforcer 같은 테스트 목이 섞여 있다).
 */
function parseDeployedAddresses(stdout: string): Record<string, Address> {
  const out: Record<string, Address> = {};
  for (const line of stdout.split(/\r?\n/)) {
    const m = line.match(/^\s{2}([A-Za-z0-9_]+):\s+(0x[0-9a-fA-F]{40})\s*$/);
    if (m) out[m[1]] = m[2] as Address;
  }
  return out;
}

function eqAddr(a: string, b: string): boolean {
  return a.toLowerCase() === b.toLowerCase();
}

async function main(): Promise<void> {
  // 1. fail-closed 가드. 어떤 트랜잭션보다 먼저.
  await assertLocalAnvilFork(publicClient);
  console.log("[deploy] fork guard 통과");

  // 1-a. 결정론: 배포 블록의 타임스탬프에도 벽시계가 새어 들어오면 안 된다.
  //      블록 간격을 1초로 못박고 첫 블록 시각을 명시한다. 이걸 빼면 배포 블록 시각이
  //      실제 경과 시간에 따라 달라지고, 부모 해시 연쇄로 이후 모든 블록 해시가 어긋난다.
  await publicClient.request({ method: "anvil_setBlockTimestampInterval" as any, params: [1] as any });
  await publicClient.request({
    method: "evm_setNextBlockTimestamp" as any,
    params: [toHex(BASE_TIMESTAMP + 1n)] as any,
  });

  // 2. 프레임워크 코어 배포
  const frameworkOut = runForgeScript("script/DeployDelegationFramework.s.sol", {
    SALT: DEPLOY_SALT,
    ENTRYPOINT_ADDRESS,
  });
  const core = parseDeployedAddresses(frameworkOut);
  const delegationManager = core["DelegationManager"];
  const hybridDeleGatorImpl = core["HybridDeleGatorImpl"];
  const multiSigDeleGatorImpl = core["MultiSigDeleGatorImpl"];
  if (!delegationManager || !hybridDeleGatorImpl || !multiSigDeleGatorImpl) {
    throw new Error(`코어 배포 주소 파싱 실패:\n${frameworkOut}`);
  }
  console.log(`[deploy] DelegationManager=${delegationManager}`);

  // 3. 구체 enforcer 37종 배포
  const enforcerOut = runForgeScript("script/DeployCaveatEnforcers.s.sol", {
    SALT: DEPLOY_SALT,
    DELEGATION_MANAGER_ADDRESS: delegationManager,
  });
  const parsed = parseDeployedAddresses(enforcerOut);
  // 코어 스크립트가 찍는 헤더(Deployer/Delegation Manager)는 위 정규식에 안 걸리지만 방어적으로 제거
  delete parsed["Deployer"];
  delete parsed["Delegation Manager"];
  const enforcers: Record<string, Address> = {};
  for (const [name, addr] of Object.entries(parsed)) {
    if (name.endsWith("Enforcer")) enforcers[name] = addr;
  }

  // 4. enforcer 개수 검증 — 37이 아니면 fail closed.
  //    src/enforcers/의 .sol 파일은 38개지만 CaveatEnforcer는 abstract 베이스로 배포 대상이 아니다.
  const count = Object.keys(enforcers).length;
  if (count !== EXPECTED_ENFORCER_COUNT) {
    throw new Error(
      `enforcer 개수 불일치: 기대 ${EXPECTED_ENFORCER_COUNT}, 실측 ${count}\n` +
        `${Object.keys(enforcers).sort().join(", ")}`,
    );
  }
  if (enforcers["CaveatEnforcer"]) {
    throw new Error("abstract 베이스 CaveatEnforcer가 배포 목록에 있다 — 배포 경로가 잘못됐다");
  }
  console.log(`[deploy] enforcer ${count}종 배포 확인`);

  // 5. delegator 스마트계정 = ERC1967Proxy(HybridDeleGator, initialize(owner, [], [], []))
  //    정본: test/utils/BaseTest.t.sol 의 deployDeleGator_Hybrid
  const proxyArtifact = JSON.parse(
    readFileSync(join(FRAMEWORK_DIR, "out", "ERC1967Proxy.sol", "ERC1967Proxy.json"), "utf8"),
  );
  const initData = encodeFunctionData({
    abi: [
      {
        type: "function",
        name: "initialize",
        inputs: [
          { name: "_owner", type: "address" },
          { name: "_keyIds", type: "string[]" },
          { name: "_xValues", type: "uint256[]" },
          { name: "_yValues", type: "uint256[]" },
        ],
        outputs: [],
        stateMutability: "nonpayable",
      },
    ],
    functionName: "initialize",
    args: [addressOf.owner, [], [], []],
  });

  const deployerWallet = walletFor(accounts.deployer);
  const proxyHash = await deployerWallet.deployContract({
    abi: proxyArtifact.abi,
    bytecode: proxyArtifact.bytecode.object as Hex,
    args: [hybridDeleGatorImpl, initData],
  });
  const proxyReceipt = await publicClient.waitForTransactionReceipt({ hash: proxyHash });
  if (proxyReceipt.status !== "success" || !proxyReceipt.contractAddress) {
    throw new Error(`delegator 프록시 배포 실패: status=${proxyReceipt.status}`);
  }
  const delegator = proxyReceipt.contractAddress as Address;
  console.log(`[deploy] delegator(스마트계정)=${delegator}`);

  // 소유자가 실제로 설정됐는지 온체인에서 확인 (ERC-1271 서명 경로의 전제)
  const onchainOwner = (await publicClient.readContract({
    address: delegator,
    abi: [{ type: "function", name: "owner", inputs: [], outputs: [{ type: "address" }], stateMutability: "view" }],
    functionName: "owner",
  })) as Address;
  if (!eqAddr(onchainOwner, addressOf.owner)) {
    throw new Error(`delegator owner 불일치: 기대 ${addressOf.owner}, 실측 ${onchainOwner}`);
  }

  // 6. 자금 세팅 — 실제로 구하지 않고 포크 상태를 덮어쓴다.
  await fundUsdc(delegator, STARTING_USDC_BALANCE);
  await publicClient.request({
    method: "anvil_setBalance" as any,
    params: [delegator, toHex(STARTING_ETH_BALANCE)] as any,
  });
  console.log(`[deploy] delegator 자금: USDC ${STARTING_USDC_BALANCE}, ETH ${STARTING_ETH_BALANCE}`);

  // 7. CREATE2 결정론 검증 — PM 실측 주소와 대조
  const actual: Record<string, string> = {
    DelegationManager: delegationManager,
    HybridDeleGatorImpl: hybridDeleGatorImpl,
    MultiSigDeleGatorImpl: multiSigDeleGatorImpl,
    ...enforcers,
  };
  const mismatches: string[] = [];
  for (const [name, expected] of Object.entries(EXPECTED_ADDRESSES)) {
    if (!actual[name]) mismatches.push(`${name}: 배포 목록에 없음`);
    else if (!eqAddr(actual[name], expected)) mismatches.push(`${name}: 기대 ${expected}, 실측 ${actual[name]}`);
  }
  if (mismatches.length > 0) {
    throw new Error(`CREATE2 주소 결정론 실패:\n${mismatches.join("\n")}`);
  }
  console.log("[deploy] CREATE2 주소 결정론 검증 통과 (PM 실측값과 일치)");

  // 8. 매니페스트
  const manifest: Manifest = {
    pinnedCommit: PINNED_COMMIT,
    fork: {
      chainId: FORK_CHAIN_ID,
      blockNumber: FORK_BLOCK_NUMBER.toString(),
      blockHash: FORK_BLOCK_HASH,
    },
    salt: DEPLOY_SALT,
    deployer: addressOf.deployer,
    delegationManager,
    hybridDeleGatorImpl,
    multiSigDeleGatorImpl,
    delegator,
    delegatorOwner: addressOf.owner,
    enforcers,
  };
  mkdirSync(dirname(MANIFEST_PATH), { recursive: true });
  writeFileSync(MANIFEST_PATH, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  console.log(`[deploy] 매니페스트 기록: ${MANIFEST_PATH}`);
}

/**
 * USDC 잔고를 치트코드로 덮어쓴다. balances 매핑 슬롯은 9로 알려져 있으나 믿지 않는다 —
 * 쓴 다음 balanceOf를 eth_call로 읽어 기대값과 다르면 throw(fail closed).
 */
export async function fundUsdc(holder: Address, amount: bigint): Promise<void> {
  const CANDIDATE_SLOTS = [9n, 0n, 1n, 2n, 3n, 4n, 5n, 6n, 7n, 8n, 10n, 11n];
  for (const slot of CANDIDATE_SLOTS) {
    const key = keccak256(
      encodeAbiParameters([{ type: "address" }, { type: "uint256" }], [holder, slot]),
    );
    await publicClient.request({
      method: "anvil_setStorageAt" as any,
      params: [USDC_ADDRESS, key, pad(toHex(amount), { size: 32 })] as any,
    });
    const balance = await readUsdcBalance(holder);
    if (balance === amount) return;
  }
  throw new Error(`USDC 잔고 슬롯을 찾지 못했다 — ${holder}의 balanceOf가 ${amount}가 되지 않음`);
}

export async function readUsdcBalance(holder: Address): Promise<bigint> {
  return (await publicClient.readContract({
    address: USDC_ADDRESS,
    abi: [
      {
        type: "function",
        name: "balanceOf",
        inputs: [{ name: "account", type: "address" }],
        outputs: [{ type: "uint256" }],
        stateMutability: "view",
      },
    ],
    functionName: "balanceOf",
    args: [holder],
  })) as bigint;
}

export function readManifest(): Manifest {
  return JSON.parse(readFileSync(MANIFEST_PATH, "utf8")) as Manifest;
}

// 직접 실행될 때만 main
if (import.meta.url === `file://${process.argv[1]?.replace(/\\/g, "/")}` || process.argv[1]?.endsWith("deploy.ts")) {
  main().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
