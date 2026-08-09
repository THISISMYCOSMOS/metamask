// Step 5 — G2 네거티브 컨트롤.
//
// 단 하나의 delegation(6종 caveat, 파라미터 고정)을 서명해서 모든 회차에 재사용한다.
// 회차마다 다른 delegation을 쓰면 "baseline을 그때만 켰다"는 반박에 노출되기 때문이다.
//
// revert는 반드시 노드에서 관측한다. TS에서 위반을 미리 계산해 예외를 던지는 것은 실패다.
// 각 회차마다 txHash / blockNumber / receipt.status 를 남긴다.
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import {
  decodeAbiParameters,
  encodeAbiParameters,
  encodeFunctionData,
  parseAbiItem,
  toHex,
  type Address,
  type Hex,
} from "viem";

import {
  BASE_TIMESTAMP,
  CAVEAT_PARAMS,
  DELEGATION_SALT,
  FORK_BLOCK_HASH,
  FORK_BLOCK_NUMBER,
  FORK_CHAIN_ID,
  PINNED_COMMIT,
  STEP_TIMESTAMP_OFFSET,
  USDC_ADDRESS,
} from "./config.js";
import { assertLocalAnvilFork } from "./guard.js";
import { accounts, addressOf, publicClient, walletFor } from "./accounts.js";
import { REPO_ROOT, readManifest, readUsdcBalance } from "./deploy.js";
import {
  MODE_CODE_SIMPLE_SINGLE,
  buildRootDelegation,
  encodeAllowedMethods,
  encodeAllowedTargets,
  encodeERC20BalanceChange,
  encodeERC20PeriodTransfer,
  encodeSingleExecution,
  encodeTimestamp,
  encodeValueLte,
  hashDelegationStruct,
  signDelegation,
  toOnchainDelegation,
  type CaveatInput,
} from "./delegation.js";

const TRACE_PATH = join(REPO_ROOT, "traces", "negative-control.json");

const ERC20_TRANSFER = parseAbiItem("function transfer(address to, uint256 amount) returns (bool)");
const ERC20_APPROVE = parseAbiItem("function approve(address spender, uint256 amount) returns (bool)");

// DelegationManager.redeemDelegations(bytes[],bytes32[],bytes[])
const REDEEM_ABI = [
  {
    type: "function",
    name: "redeemDelegations",
    inputs: [
      { name: "_permissionContexts", type: "bytes[]" },
      { name: "_modes", type: "bytes32[]" },
      { name: "_executionCallDatas", type: "bytes[]" },
    ],
    outputs: [],
    stateMutability: "nonpayable",
  },
] as const;

// abi.decode(_permissionContexts[i], (Delegation[]))
const DELEGATION_ARRAY_ABI_TYPE = {
  type: "tuple[]",
  components: [
    { name: "delegate", type: "address" },
    { name: "delegator", type: "address" },
    { name: "authority", type: "bytes32" },
    {
      name: "caveats",
      type: "tuple[]",
      components: [
        { name: "enforcer", type: "address" },
        { name: "terms", type: "bytes" },
        { name: "args", type: "bytes" },
      ],
    },
    { name: "salt", type: "uint256" },
    { name: "signature", type: "bytes" },
  ],
} as const;

interface StepSpec {
  id: string;
  kind: "positive" | "negative";
  intent: string;
  target: Address;
  value: bigint;
  callData: Hex;
  timestamp: bigint;
  /** docs/caveat-encoding.md 정답지. null이면 성공을 기대한다. */
  expectedRevert: string | null;
  /** 이 회차가 살아 있음을 증명하는 enforcer */
  provesEnforcer: string | null;
}

interface StepResult {
  id: string;
  kind: string;
  intent: string;
  provesEnforcer: string | null;
  target: Address;
  value: string;
  callData: Hex;
  blockTimestamp: string;
  txHash: Hex;
  blockNumber: string;
  receiptStatus: string;
  gasUsed: string;
  expectedRevert: string | null;
  observedRevert: string | null;
  revertSource: string | null;
  matchesExpectation: boolean;
  balancesBefore: { usdc: string; eth: string };
  balancesAfter: { usdc: string; eth: string };
  logCount: number;
  sawPeriodEnforcerEvent: boolean;
}

/** Error(string) 셀렉터 */
const ERROR_STRING_SELECTOR = "0x08c379a0";

function decodeErrorString(data: Hex): string | null {
  if (!data || !data.startsWith(ERROR_STRING_SELECTOR)) return null;
  try {
    const [reason] = decodeAbiParameters([{ type: "string" }], `0x${data.slice(10)}` as Hex);
    return reason as string;
  } catch {
    return null;
  }
}

/**
 * revert 사유를 노드에서 뽑는다. 손으로 문자열을 만들지 않는다.
 * 1순위: debug_traceTransaction(callTracer)의 revertReason / output
 * 2순위: 같은 입력으로 직전 블록에서 eth_call 재현
 */
async function extractRevertReason(
  txHash: Hex,
  blockNumber: bigint,
  from: Address,
  to: Address,
  data: Hex,
): Promise<{ reason: string | null; source: string | null }> {
  // 1순위 — debug_traceTransaction
  try {
    const trace = (await publicClient.request({
      method: "debug_traceTransaction" as any,
      params: [txHash, { tracer: "callTracer" }] as any,
    })) as { revertReason?: string; output?: Hex; error?: string };

    if (trace?.revertReason) return { reason: trace.revertReason, source: "debug_traceTransaction.revertReason" };
    if (trace?.output) {
      const decoded = decodeErrorString(trace.output);
      if (decoded) return { reason: decoded, source: "debug_traceTransaction.output" };
    }
  } catch {
    /* 폴백으로 넘어간다 */
  }

  // 2순위 — 직전 블록 상태에서 eth_call 재현 (블록당 트랜잭션 1건이므로 직전 블록 = 실행 직전 상태)
  try {
    await publicClient.call({
      account: from,
      to,
      data,
      blockNumber: blockNumber - 1n,
    });
  } catch (err: any) {
    const raw: string = err?.details ?? err?.shortMessage ?? String(err);
    const m = raw.match(/[A-Za-z0-9]+Enforcer:[a-z-]+/);
    if (m) return { reason: m[0], source: "eth_call replay" };
    const walk = (e: any): string | null => {
      if (!e) return null;
      if (typeof e?.data === "string") {
        const d = decodeErrorString(e.data as Hex);
        if (d) return d;
      }
      return walk(e.cause);
    };
    const decoded = walk(err);
    if (decoded) return { reason: decoded, source: "eth_call replay (decoded)" };
    return { reason: raw, source: "eth_call replay (raw)" };
  }

  return { reason: null, source: null };
}

async function main(): Promise<void> {
  await assertLocalAnvilFork(publicClient);
  console.log("[g2] fork guard 통과");

  const manifest = readManifest();
  const dm = manifest.delegationManager;
  const delegator = manifest.delegator;

  // ── caveat 6종. 순서는 docs/caveat-encoding.md 고정표. 순서가 revert 문자열을 결정한다.
  const caveats: CaveatInput[] = [
    { enforcer: manifest.enforcers["AllowedTargetsEnforcer"], terms: encodeAllowedTargets([...CAVEAT_PARAMS.allowedTargets]) },
    {
      enforcer: manifest.enforcers["AllowedMethodsEnforcer"],
      terms: encodeAllowedMethods([...CAVEAT_PARAMS.allowedMethodSelectors] as Hex[]),
    },
    { enforcer: manifest.enforcers["ValueLteEnforcer"], terms: encodeValueLte(CAVEAT_PARAMS.valueLteMax) },
    {
      enforcer: manifest.enforcers["TimestampEnforcer"],
      terms: encodeTimestamp(CAVEAT_PARAMS.timestampAfter, CAVEAT_PARAMS.timestampBefore),
    },
    {
      enforcer: manifest.enforcers["ERC20PeriodTransferEnforcer"],
      terms: encodeERC20PeriodTransfer(
        CAVEAT_PARAMS.erc20Period.token,
        CAVEAT_PARAMS.erc20Period.periodAmount,
        CAVEAT_PARAMS.erc20Period.periodDuration,
        CAVEAT_PARAMS.erc20Period.startDate,
      ),
    },
    {
      enforcer: manifest.enforcers["ERC20BalanceChangeEnforcer"],
      terms: encodeERC20BalanceChange(
        CAVEAT_PARAMS.erc20BalanceChange.enforceDecrease,
        CAVEAT_PARAMS.erc20BalanceChange.token,
        delegator, // recipient = delegator 스마트계정 (사용자 자산 감소를 본다)
        CAVEAT_PARAMS.erc20BalanceChange.amount,
      ),
    },
  ];
  for (const [i, c] of caveats.entries()) {
    if (!c.enforcer) throw new Error(`caveat[${i}] enforcer 주소가 매니페스트에 없다`);
  }

  const delegation = buildRootDelegation(addressOf.delegate, delegator, caveats, DELEGATION_SALT);
  const signed = await signDelegation(accounts.owner, dm, delegation);
  const onchain = toOnchainDelegation(signed);
  const permissionContext = encodeAbiParameters([DELEGATION_ARRAY_ABI_TYPE as any], [[onchain] as any]);
  console.log("[g2] delegation 서명 완료 (caveat 6종)");

  // ── 회차 정의 ────────────────────────────────────────────────────────
  const ts = (n: number) => BASE_TIMESTAMP + STEP_TIMESTAMP_OFFSET * BigInt(n);
  const xfer = (to: Address, amount: bigint) =>
    encodeFunctionData({ abi: [ERC20_TRANSFER], functionName: "transfer", args: [to, amount] });

  const steps: StepSpec[] = [
    {
      id: "PC0",
      kind: "positive",
      intent: "회차 400 USDC — 회차 한도(500)·일일 한도(2,000) 모두 만족. 6종 전부 통과해야 한다.",
      target: USDC_ADDRESS,
      value: 0n,
      callData: xfer(addressOf.counterparty, 400_000_000n),
      timestamp: ts(1),
      expectedRevert: null,
      provesEnforcer: null,
    },
    {
      id: "NC1",
      kind: "negative",
      intent: "일일 한도 초과 — 2,500 USDC (잔여 available 1,600)",
      target: USDC_ADDRESS,
      value: 0n,
      callData: xfer(addressOf.counterparty, 2_500_000_000n),
      timestamp: ts(2),
      expectedRevert: "ERC20PeriodTransferEnforcer:transfer-amount-exceeded",
      provesEnforcer: "ERC20PeriodTransferEnforcer",
    },
    {
      id: "NC2",
      kind: "negative",
      intent: "허용목록 밖 대상 호출",
      target: addressOf.disallowedTarget,
      value: 0n,
      callData: xfer(addressOf.counterparty, 100_000_000n),
      timestamp: ts(3),
      expectedRevert: "AllowedTargetsEnforcer:target-address-not-allowed",
      provesEnforcer: "AllowedTargetsEnforcer",
    },
    {
      id: "NC3",
      kind: "negative",
      intent:
        "회차 한도만 초과 — 800 USDC. 일일 한도는 지키므로 beforeHook을 전부 통과하고 " +
        "실행 후 afterHook의 상태 차분 검사에서 막힌다.",
      target: USDC_ADDRESS,
      value: 0n,
      callData: xfer(addressOf.counterparty, 800_000_000n),
      timestamp: ts(4),
      expectedRevert: "ERC20BalanceChangeEnforcer:exceeded-balance-decrease",
      provesEnforcer: "ERC20BalanceChangeEnforcer",
    },
    {
      id: "NC4",
      kind: "negative",
      intent: "허용목록 밖 메서드 — approve(address,uint256)",
      target: USDC_ADDRESS,
      value: 0n,
      callData: encodeFunctionData({
        abi: [ERC20_APPROVE],
        functionName: "approve",
        args: [addressOf.counterparty, 100_000_000n],
      }),
      timestamp: ts(5),
      expectedRevert: "AllowedMethodsEnforcer:method-not-allowed",
      provesEnforcer: "AllowedMethodsEnforcer",
    },
    {
      id: "NC5",
      kind: "negative",
      intent: "네이티브 값 상한 초과 — value 1 wei (상한 0)",
      target: USDC_ADDRESS,
      value: 1n,
      callData: xfer(addressOf.counterparty, 100_000_000n),
      timestamp: ts(6),
      expectedRevert: "ValueLteEnforcer:value-too-high",
      provesEnforcer: "ValueLteEnforcer",
    },
    {
      id: "NC6",
      kind: "negative",
      intent: "위임 유효기간 경과 — before 임계(1788660491) 이후. 마지막에 둔다(시각을 되돌릴 수 없다).",
      target: USDC_ADDRESS,
      value: 0n,
      callData: xfer(addressOf.counterparty, 100_000_000n),
      timestamp: CAVEAT_PARAMS.timestampBefore + STEP_TIMESTAMP_OFFSET,
      expectedRevert: "TimestampEnforcer:expired-delegation",
      provesEnforcer: "TimestampEnforcer",
    },
  ];

  const delegateWallet = walletFor(accounts.delegate);
  const results: StepResult[] = [];

  for (const step of steps) {
    // 결정론: 벽시계 유입 금지. 블록 타임스탬프를 명시 설정한다.
    await publicClient.request({
      method: "evm_setNextBlockTimestamp" as any,
      params: [toHex(step.timestamp)] as any,
    });

    const usdcBefore = await readUsdcBalance(delegator);
    const ethBefore = await publicClient.getBalance({ address: delegator });

    const data = encodeFunctionData({
      abi: REDEEM_ABI,
      functionName: "redeemDelegations",
      args: [
        [permissionContext],
        [MODE_CODE_SIMPLE_SINGLE],
        [encodeSingleExecution(step.target, step.value, step.callData)],
      ],
    });

    // gas를 명시해 viem의 가스 추정을 건너뛴다. 추정 단계에서 먼저 실패해버리면
    // revert가 온체인에 기록되지 않아 G2의 증거가 되지 않는다.
    const txHash = await delegateWallet.sendTransaction({ to: dm, data, gas: 3_000_000n });
    const receipt = await publicClient.waitForTransactionReceipt({ hash: txHash });

    const usdcAfter = await readUsdcBalance(delegator);
    const ethAfter = await publicClient.getBalance({ address: delegator });

    let observedRevert: string | null = null;
    let revertSource: string | null = null;
    if (receipt.status === "reverted") {
      const r = await extractRevertReason(txHash, receipt.blockNumber, addressOf.delegate, dm, data);
      observedRevert = r.reason;
      revertSource = r.source;
    }

    // ERC20PeriodTransferEnforcer가 실제로 실행됐다는 온체인 증거 (성공 회차에서 이벤트가 나온다)
    const periodEnforcer = manifest.enforcers["ERC20PeriodTransferEnforcer"].toLowerCase();
    const sawPeriodEnforcerEvent = receipt.logs.some((l) => l.address.toLowerCase() === periodEnforcer);

    const matchesExpectation =
      step.expectedRevert === null
        ? receipt.status === "success"
        : receipt.status === "reverted" && observedRevert === step.expectedRevert;

    results.push({
      id: step.id,
      kind: step.kind,
      intent: step.intent,
      provesEnforcer: step.provesEnforcer,
      target: step.target,
      value: step.value.toString(),
      callData: step.callData,
      blockTimestamp: step.timestamp.toString(),
      txHash,
      blockNumber: receipt.blockNumber.toString(),
      receiptStatus: receipt.status,
      gasUsed: receipt.gasUsed.toString(),
      expectedRevert: step.expectedRevert,
      observedRevert,
      revertSource,
      matchesExpectation,
      balancesBefore: { usdc: usdcBefore.toString(), eth: ethBefore.toString() },
      balancesAfter: { usdc: usdcAfter.toString(), eth: ethAfter.toString() },
      logCount: receipt.logs.length,
      sawPeriodEnforcerEvent,
    });

    const mark = matchesExpectation ? "OK" : "MISMATCH";
    console.log(
      `[g2] ${step.id} ${mark} status=${receipt.status} block=${receipt.blockNumber} ` +
        `revert=${observedRevert ?? "-"}`,
    );
  }

  // ── 정답지 대조. 하나라도 어긋나면 fail closed. ──────────────────────
  const bad = results.filter((r) => !r.matchesExpectation);
  const encodingErrors = results.filter((r) => r.observedRevert?.includes("invalid-terms-length"));

  // 해시 대상 / 비해시 영역을 분리한다.
  const hashed = {
    fork: {
      chainId: FORK_CHAIN_ID,
      blockNumber: FORK_BLOCK_NUMBER.toString(),
      blockHash: FORK_BLOCK_HASH,
      delegationFrameworkCommit: PINNED_COMMIT,
    },
    baseline: {
      delegationManager: dm,
      delegator,
      delegate: addressOf.delegate,
      counterparty: addressOf.counterparty,
      caveats: caveats.map((c, i) => ({
        index: i,
        enforcer: c.enforcer,
        name: [
          "AllowedTargetsEnforcer",
          "AllowedMethodsEnforcer",
          "ValueLteEnforcer",
          "TimestampEnforcer",
          "ERC20PeriodTransferEnforcer",
          "ERC20BalanceChangeEnforcer",
        ][i],
        terms: c.terms,
        termsBytes: (c.terms.length - 2) / 2,
      })),
      parameters: {
        dailyLimitUsdc: CAVEAT_PARAMS.erc20Period.periodAmount.toString(),
        periodDurationSeconds: CAVEAT_PARAMS.erc20Period.periodDuration.toString(),
        perRedemptionMaxDecreaseUsdc: CAVEAT_PARAMS.erc20BalanceChange.amount.toString(),
        valueLteMax: CAVEAT_PARAMS.valueLteMax.toString(),
      },
    },
    delegation: {
      delegate: signed.delegate,
      delegator: signed.delegator,
      authority: signed.authority,
      salt: signed.salt.toString(),
      signature: signed.signature,
      // 정본 delegation.ts 헬퍼로 계산한 struct 해시. state-digest.ts가 이 값을 재계산해 대조한다.
      delegationHash: hashDelegationStruct({
        delegate: signed.delegate,
        delegator: signed.delegator,
        authority: signed.authority,
        caveats: signed.caveats.map((c) => ({ enforcer: c.enforcer, terms: c.terms })),
        salt: signed.salt,
      }),
    },
    steps: results,
  };

  const trace = {
    schemaVersion: 1,
    kind: "negative-control",
    note:
      "G2 — baseline이 살아 있음을 증명한다. 모든 revert는 노드에서 관측된 온체인 revert이며 " +
      "txHash / blockNumber / receiptStatus 로 확인할 수 있다.",
    hashed,
    // meta는 해시 대상이 아니다 (벽시계 시각·절대경로 등 비결정 값).
    meta: {
      generatedAtWallClock: new Date().toISOString(),
      note: "meta는 재현성 해시 대상에서 제외한다.",
    },
  };

  mkdirSync(dirname(TRACE_PATH), { recursive: true });
  writeFileSync(TRACE_PATH, `${JSON.stringify(trace, null, 2)}\n`, "utf8");
  console.log(`[g2] 트레이스 기록: ${TRACE_PATH}`);

  if (encodingErrors.length > 0) {
    throw new Error(
      `invalid-terms-length 계열 revert가 관측됐다. 이것은 baseline이 작동한 증거가 아니라 ` +
        `terms 인코딩이 틀렸다는 뜻이다:\n` +
        encodingErrors.map((r) => `  ${r.id}: ${r.observedRevert}`).join("\n"),
    );
  }
  if (bad.length > 0) {
    throw new Error(
      `정답지와 불일치한 회차가 있다:\n` +
        bad
          .map((r) => `  ${r.id}: 기대 ${r.expectedRevert ?? "success"}, 실측 ${r.observedRevert ?? r.receiptStatus}`)
          .join("\n"),
    );
  }
  console.log(`[g2] 전 회차 정답지 일치 (${results.length}건)`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
