// G1 — 결정론 증거로 쓸 정본 상태 다이제스트.
//
// 왜 블록 헤더의 stateRoot를 쓰지 않는가:
//   anvil은 포크 모드에서 머클 상태 루트를 계산하지 않는다 (헤더의 stateRoot가 전부 0이다).
//   따라서 stateRoot를 정본으로 쓸 수 없다. 대신 다음 둘을 합쳐서 해시한다.
//
//   (1) 노드가 실제로 계산한 트라이 루트 — 로컬에서 채굴된 모든 블록의
//       transactionsRoot / receiptsRoot / gasUsed / timestamp / hash.
//       이건 손으로 고른 값이 아니라 실행 결과 전체를 담는 머클 루트다.
//   (2) 명시적으로 열거한 계정 상태 스냅샷 — 주소순 정렬, nonce/balance/codeHash,
//       USDC 잔고, 그리고 ERC20PeriodTransferEnforcer에 누적된 기간 사용량.
//
//   (2)의 마지막 항목이 중요하다. 일일 한도에 얼마가 소진됐는지가 누적 손실 논거의
//   핵심 상태이므로, 재실행 시 이 값까지 같아야 결정론이 성립한다.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { hashStruct, keccak256, toHex, type Address, type Hex } from "viem";

import { FORK_BLOCK_NUMBER, USDC_ADDRESS } from "./config.js";
import { assertLocalAnvilFork } from "./guard.js";
import { addressOf, publicClient } from "./accounts.js";
import { REPO_ROOT, readManifest, readUsdcBalance } from "./deploy.js";

const TRACE_PATH = join(REPO_ROOT, "traces", "negative-control.json");

const DELEGATION_TYPES = {
  Caveat: [
    { name: "enforcer", type: "address" },
    { name: "terms", type: "bytes" },
  ],
  Delegation: [
    { name: "delegate", type: "address" },
    { name: "delegator", type: "address" },
    { name: "authority", type: "bytes32" },
    { name: "caveats", type: "Caveat[]" },
    { name: "salt", type: "uint256" },
  ],
} as const;

/** 키를 재귀적으로 정렬해 JSON 직렬화를 정본화한다. */
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(value as Record<string, unknown>).sort()) {
      out[k] = canonical((value as Record<string, unknown>)[k]);
    }
    return out;
  }
  if (typeof value === "bigint") return value.toString();
  return value;
}

async function main(): Promise<void> {
  await assertLocalAnvilFork(publicClient);

  const manifest = readManifest();
  const trace = JSON.parse(readFileSync(TRACE_PATH, "utf8"));

  // ── (1) 로컬 채굴 블록의 노드 계산 트라이 루트 ──────────────────────
  const latest = await publicClient.getBlockNumber();
  const blocks: unknown[] = [];
  for (let n = FORK_BLOCK_NUMBER + 1n; n <= latest; n++) {
    const b = await publicClient.getBlock({ blockNumber: n, includeTransactions: false });
    blocks.push({
      number: b.number?.toString(),
      hash: b.hash,
      timestamp: b.timestamp.toString(),
      transactionsRoot: b.transactionsRoot,
      receiptsRoot: b.receiptsRoot,
      gasUsed: b.gasUsed.toString(),
      txCount: b.transactions.length,
    });
  }

  // ── (2) 명시적 계정 상태 스냅샷 (주소순 정렬) ────────────────────────
  const watched: Record<string, Address> = {
    deployer: addressOf.deployer,
    owner: addressOf.owner,
    delegate: addressOf.delegate,
    counterparty: addressOf.counterparty,
    disallowedTarget: addressOf.disallowedTarget,
    delegator: manifest.delegator,
    delegationManager: manifest.delegationManager,
    hybridDeleGatorImpl: manifest.hybridDeleGatorImpl,
    multiSigDeleGatorImpl: manifest.multiSigDeleGatorImpl,
    usdc: USDC_ADDRESS,
    ...manifest.enforcers,
  };

  const accounts: unknown[] = [];
  for (const addr of [...new Set(Object.values(watched))].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()))) {
    const [balance, nonce, code] = await Promise.all([
      publicClient.getBalance({ address: addr }),
      publicClient.getTransactionCount({ address: addr }),
      publicClient.getCode({ address: addr }),
    ]);
    accounts.push({
      address: addr.toLowerCase(),
      balance: balance.toString(),
      nonce,
      codeHash: code && code !== "0x" ? keccak256(code) : null,
    });
  }

  // USDC 잔고 (관련 주체만)
  const usdcHolders: Record<string, string> = {};
  for (const [name, addr] of Object.entries({
    delegator: manifest.delegator,
    counterparty: addressOf.counterparty,
    delegate: addressOf.delegate,
  })) {
    usdcHolders[name] = (await readUsdcBalance(addr as Address)).toString();
  }

  // ── ERC20PeriodTransferEnforcer에 누적된 기간 사용량 ────────────────
  const caveatsForHash = (trace.hashed.baseline.caveats as { enforcer: Address; terms: Hex }[]).map((c) => ({
    enforcer: c.enforcer,
    terms: c.terms,
  }));
  const delegationHash = hashStruct({
    data: {
      delegate: trace.hashed.delegation.delegate,
      delegator: trace.hashed.delegation.delegator,
      authority: trace.hashed.delegation.authority,
      caveats: caveatsForHash,
      salt: BigInt(trace.hashed.delegation.salt),
    },
    primaryType: "Delegation",
    types: DELEGATION_TYPES,
  });

  const allowance = (await publicClient.readContract({
    address: manifest.enforcers["ERC20PeriodTransferEnforcer"],
    abi: [
      {
        type: "function",
        name: "periodicAllowances",
        inputs: [
          { name: "delegationManager", type: "address" },
          { name: "delegationHash", type: "bytes32" },
        ],
        outputs: [
          { name: "periodAmount", type: "uint256" },
          { name: "periodDuration", type: "uint256" },
          { name: "startDate", type: "uint256" },
          { name: "lastTransferPeriod", type: "uint256" },
          { name: "transferredInCurrentPeriod", type: "uint256" },
        ],
        stateMutability: "view",
      },
    ],
    functionName: "periodicAllowances",
    args: [manifest.delegationManager, delegationHash],
  })) as readonly bigint[];

  const snapshot = {
    forkBlock: FORK_BLOCK_NUMBER.toString(),
    finalBlock: latest.toString(),
    blocks,
    accounts,
    usdcBalances: usdcHolders,
    delegationHash,
    periodicAllowance: {
      periodAmount: allowance[0].toString(),
      periodDuration: allowance[1].toString(),
      startDate: allowance[2].toString(),
      lastTransferPeriod: allowance[3].toString(),
      transferredInCurrentPeriod: allowance[4].toString(),
    },
  };

  const json = JSON.stringify(canonical(snapshot), null, 2);
  const digest = keccak256(toHex(json));

  // 진단용: 재실행 간 diff를 뜰 수 있도록 스냅샷 원본을 남긴다 (경로는 환경변수로 지정).
  if (process.env.SNAPSHOT_OUT) {
    (await import("node:fs")).writeFileSync(process.env.SNAPSHOT_OUT, `${json}\n`, "utf8");
  }

  console.log(`FINAL_BLOCK=${latest}`);
  console.log(`DELEGATION_HASH=${delegationHash}`);
  console.log(`PERIOD_CONSUMED=${allowance[4].toString()}`);
  console.log(`STATE_DIGEST=${digest}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
