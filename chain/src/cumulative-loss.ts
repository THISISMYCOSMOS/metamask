// G3 — 누적 손실 트레이스.
//
// 기존에 서명된 단 하나의 root delegation(caveat 6종, negative-control.ts와 완전히 동일한
// 순서·파라미터·바이트 인코딩)으로 정확히 500 USDC를 20회 연속 전송해 delegator의 USDC를
// 10,000 -> 0으로 고갈시킨다. 전 회차가 성공해야 하고(revert 0건), 포크 블록 오라클로
// 환산한 포트폴리오 가치가 감소했음을 정수 연산으로 증명한다.
//
// revert는 절대 나오면 안 된다 — G3의 정의가 "baseline을 전부 통과하면서 손실이 난다"이므로
// revert 1건이라도 나오면 fail closed로 실패한다. 실패 시에도 그때까지 모은 데이터로
// 트레이스를 남긴 뒤 throw한다(원인 진단용, verifier 스키마 충족을 보장하지는 않는다).
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import {
  encodeAbiParameters,
  encodeFunctionData,
  keccak256,
  pad,
  parseAbiItem,
  toBytes,
  toHex,
  type Address,
  type Hex,
} from "viem";

import {
  BASE_TIMESTAMP,
  CAVEAT_PARAMS,
  CHAINLINK_ETH_USD_DECIMALS,
  CHAINLINK_ETH_USD_FEED,
  CHAINLINK_USDC_USD_DECIMALS,
  CHAINLINK_USDC_USD_FEED,
  DELEGATION_SALT,
  EXPECTED_DELEGATION_HASH,
  EXPECTED_ORACLE,
  FORK_BLOCK_HASH,
  FORK_BLOCK_NUMBER,
  FORK_BLOCK_TIMESTAMP,
  FORK_CHAIN_ID,
  G3_EXPECTED_PERIOD_DISTRIBUTION,
  G3_STEP_AMOUNT_USDC,
  G3_STEP_COUNT,
  ORACLE_MAX_STALENESS_SECONDS,
  PINNED_COMMIT,
  STARTING_ETH_BALANCE,
  STARTING_USDC_BALANCE,
  STEP_TIMESTAMP_OFFSET,
  USDC_ADDRESS,
  USDC_DECIMALS,
  USD_VALUE_SCALE_DECIMALS,
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

const TRACE_PATH = join(REPO_ROOT, "traces", "cumulative-loss.json");
const FAILED_TRACE_PATH = join(REPO_ROOT, "traces", "cumulative-loss.failed.json");

const ERC20_TRANSFER = parseAbiItem("function transfer(address to, uint256 amount) returns (bool)");

// DelegationManager.redeemDelegations(bytes[],bytes32[],bytes[]) — negative-control.ts와 동일 시그니처.
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

// abi.decode(_permissionContexts[i], (Delegation[])) — negative-control.ts와 동일.
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

// ERC20PeriodTransferEnforcer.periodicAllowances — state-digest.ts에 있는 것과 동일 규약.
// state-digest.ts는 편집 금지 파일이라 이 파일 안에 상수로 다시 둔다(브리프 §3-1-6).
const PERIODIC_ALLOWANCES_ABI = [
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
] as const;

// Chainlink AggregatorV3Interface 최소 ABI.
const AGGREGATOR_ABI = [
  { type: "function", name: "decimals", inputs: [], outputs: [{ type: "uint8" }], stateMutability: "view" },
  {
    type: "function",
    name: "latestRoundData",
    inputs: [],
    outputs: [
      { name: "roundId", type: "uint80" },
      { name: "answer", type: "int256" },
      { name: "startedAt", type: "uint256" },
      { name: "updatedAt", type: "uint256" },
      { name: "answeredInRound", type: "uint80" },
    ],
    stateMutability: "view",
  },
] as const;

const TRANSFER_EVENT_TOPIC = keccak256(toBytes("Transfer(address,address,uint256)"));
const PERIOD_TRANSFER_EVENT_TOPIC = keccak256(
  toBytes(
    "TransferredInPeriod(address,address,bytes32,address,uint256,uint256,uint256,uint256,uint256)",
  ),
);

const CAVEAT_NAMES = [
  "AllowedTargetsEnforcer",
  "AllowedMethodsEnforcer",
  "ValueLteEnforcer",
  "TimestampEnforcer",
  "ERC20PeriodTransferEnforcer",
  "ERC20BalanceChangeEnforcer",
] as const;

/** 키를 재귀적으로 정렬해 JSON 직렬화를 정본화한다. state-digest.ts의 canonical()과 같은 규약. */
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

/**
 * 자산 하나의 가치를 1e-18 USD 정수로 환산한다.
 * value1e18 = amount * answer * 10^18 / (10^tokenDecimals * 10^oracleDecimals)
 * 나눗셈 전에 나머지가 0인지 검사한다 — 무성 절삭으로 손실 수치를 왜곡시키지 않기 위해서다.
 */
function assetValue1e18(amount: bigint, tokenDecimals: bigint, answer: bigint, oracleDecimals: bigint): bigint {
  const numerator = amount * answer * 10n ** USD_VALUE_SCALE_DECIMALS;
  const denominator = 10n ** tokenDecimals * 10n ** oracleDecimals;
  if (numerator % denominator !== 0n) {
    throw new Error(
      `[g3] 포트폴리오 가치 환산 나머지가 0이 아니다: amount=${amount} answer=${answer} ` +
        `tokenDecimals=${tokenDecimals} oracleDecimals=${oracleDecimals}`,
    );
  }
  return numerator / denominator;
}

interface OracleCheck {
  name: string;
  passed: boolean;
}

interface OracleFields {
  feed: Address;
  roundId: string;
  answer: string;
  startedAt: string;
  updatedAt: string;
  answeredInRound: string;
  decimals: number;
  ageSeconds: string;
  checks: OracleCheck[];
}

/**
 * 오라클을 포크 블록 스냅샷(blockNumber: FORK_BLOCK_NUMBER)으로 명시 조회하고 fail-closed 검증한다.
 * 검사: decimals==8, answer>0, updatedAt<=포크ts, answeredInRound>=roundId, 신선도<=86400s,
 * 핀 값(answer/updatedAt) 정확 일치. 하나라도 어긋나면 throw.
 */
async function readAndValidateOracle(
  feed: Address,
  expected: { answer: bigint; updatedAt: bigint; decimals: number },
): Promise<{ fields: OracleFields; answer: bigint }> {
  const decimals = (await publicClient.readContract({
    address: feed,
    abi: AGGREGATOR_ABI,
    functionName: "decimals",
    blockNumber: FORK_BLOCK_NUMBER,
  })) as number;

  const [roundId, answer, startedAt, updatedAt, answeredInRound] = (await publicClient.readContract({
    address: feed,
    abi: AGGREGATOR_ABI,
    functionName: "latestRoundData",
    blockNumber: FORK_BLOCK_NUMBER,
  })) as readonly [bigint, bigint, bigint, bigint, bigint];

  const ageSeconds = FORK_BLOCK_TIMESTAMP - updatedAt;

  const checks: OracleCheck[] = [
    { name: "decimalsIs8", passed: decimals === 8 },
    { name: "answerPositive", passed: answer > 0n },
    { name: "updatedAtNotAfterForkBlock", passed: updatedAt <= FORK_BLOCK_TIMESTAMP },
    { name: "answeredInRoundGteRoundId", passed: answeredInRound >= roundId },
    // 신선도 상한 86400초는 G3 구현 시 고정했다. 피드 age 관측 전에 선택했다는 역사적
    // 근거는 없으므로 사전선정으로 주장하지 않는다 — docs/baseline-config.md 정정 노트 참조.
    { name: "freshnessWithinCeiling", passed: ageSeconds <= ORACLE_MAX_STALENESS_SECONDS },
    { name: "pinnedAnswerMatches", passed: answer === expected.answer },
    { name: "pinnedUpdatedAtMatches", passed: updatedAt === expected.updatedAt },
  ];

  const failed = checks.filter((c) => !c.passed);
  if (failed.length > 0) {
    throw new Error(`[g3] 오라클 검증 실패 (${feed}): ${failed.map((c) => c.name).join(", ")}`);
  }

  return {
    answer,
    fields: {
      feed,
      roundId: roundId.toString(),
      answer: answer.toString(),
      startedAt: startedAt.toString(),
      updatedAt: updatedAt.toString(),
      answeredInRound: answeredInRound.toString(),
      decimals,
      ageSeconds: ageSeconds.toString(),
      checks,
    },
  };
}

interface CaveatRecord {
  index: number;
  name: string;
  enforcer: Address;
  terms: Hex;
  termsBytes: number;
}

interface CaveatPassedRecord extends CaveatRecord {
  evidence: string;
}

interface StepResult {
  stepIndex: number;
  n: number;
  timestamp: string;
  txHash: Hex;
  blockNumber: string;
  receiptStatus: string;
  gasUsed: string;
  transferAmount: string;
  usdcBefore: string;
  usdcAfter: string;
  ethBefore: string;
  ethAfter: string;
  transferEvent: { logIndex: number; address: Address; topics: Hex[]; data: Hex };
  periodEnforcerEvent: { logIndex: number; address: Address; topics: Hex[]; data: Hex };
  periodicAllowanceAfter: {
    periodAmount: string;
    periodDuration: string;
    startDate: string;
    lastTransferPeriod: string;
    transferredInCurrentPeriod: string;
  };
  periodIndex: number;
  caveatsPassed: CaveatPassedRecord[];
}

interface StateSnapshot {
  stepIndex: number;
  before: { usdc: string; ethWei: string };
  after: { usdc: string; ethWei: string };
}

async function main(): Promise<void> {
  await assertLocalAnvilFork(publicClient);
  console.log("[g3] fork guard 통과");

  const manifest = readManifest();
  const dm = manifest.delegationManager;
  const delegator = manifest.delegator;

  // ── caveat 6종. negative-control.ts와 완전히 동일한 순서·파라미터·바이트. ──────
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

  // caveats 배열 하나를 서명·인코딩·트레이스 기록에 전부 동일 참조로 재사용한다 — 회차가
  // 참조한 caveat 목록이 실제 서명된 delegation의 caveat 배열과 같은 객체임을 코드로 보장한다
  // (브리프 §3-1-7: enforcer를 안 붙이고 통과한 것처럼 기록하는 것이 심사 1순위 공격 지점).
  const caveatRecords: CaveatRecord[] = caveats.map((c, i) => ({
    index: i,
    name: CAVEAT_NAMES[i],
    enforcer: c.enforcer,
    terms: c.terms,
    termsBytes: (c.terms.length - 2) / 2,
  }));

  const delegation = buildRootDelegation(addressOf.delegate, delegator, caveats, DELEGATION_SALT);
  const signed = await signDelegation(accounts.owner, dm, delegation);
  const onchain = toOnchainDelegation(signed);
  const permissionContext = encodeAbiParameters([DELEGATION_ARRAY_ABI_TYPE as any], [[onchain] as any]);
  const delegationHash = hashDelegationStruct({
    delegate: signed.delegate,
    delegator: signed.delegator,
    authority: signed.authority,
    caveats: signed.caveats.map((c) => ({ enforcer: c.enforcer, terms: c.terms })),
    salt: signed.salt,
  });
  if (delegationHash.toLowerCase() !== EXPECTED_DELEGATION_HASH.toLowerCase()) {
    throw new Error(
      `[g3] delegation 해시 불일치: 기대 ${EXPECTED_DELEGATION_HASH}, 실측 ${delegationHash}`,
    );
  }
  console.log(`[g3] delegation 서명 완료 (caveat 6종), delegationHash=${delegationHash}`);

  const forkInfo = {
    chainId: FORK_CHAIN_ID,
    blockNumber: FORK_BLOCK_NUMBER.toString(),
    blockHash: FORK_BLOCK_HASH,
    blockTimestamp: FORK_BLOCK_TIMESTAMP.toString(),
    delegationFrameworkCommit: PINNED_COMMIT,
  };
  const baselineInfo = {
    delegationManager: dm,
    delegator,
    delegate: addressOf.delegate,
    counterparty: addressOf.counterparty,
    caveats: caveatRecords,
    parameters: {
      dailyLimitUsdc: CAVEAT_PARAMS.erc20Period.periodAmount.toString(),
      periodDurationSeconds: CAVEAT_PARAMS.erc20Period.periodDuration.toString(),
      perRedemptionMaxDecreaseUsdc: CAVEAT_PARAMS.erc20BalanceChange.amount.toString(),
      valueLteMax: CAVEAT_PARAMS.valueLteMax.toString(),
      stepCount: G3_STEP_COUNT,
      stepAmountUsdc: G3_STEP_AMOUNT_USDC.toString(),
    },
  };
  const delegationInfo = {
    delegate: signed.delegate,
    delegator: signed.delegator,
    authority: signed.authority,
    salt: signed.salt.toString(),
    signature: signed.signature,
    delegationHash,
  };

  const steps: StepResult[] = [];
  const states: StateSnapshot[] = [];
  let oracleInfo: unknown = null;
  let resultInfo: unknown = null;

  function writeTrace(targetPath: string, copyPath?: string): void {
    const trace = {
      schemaVersion: 1,
      kind: "cumulative-loss",
      note:
        "G3 — 단일 서명 root delegation으로 20회 연속 500 USDC 전송, 전 회차 성공(revert 0건), " +
        "caveat 6종 전부 통과, 오라클 환산 포트폴리오 가치 감소를 정수 연산으로 증명한다.",
      hashed: {
        fork: forkInfo,
        baseline: baselineInfo,
        delegation: delegationInfo,
        steps,
        states,
        oracle: oracleInfo,
        result: resultInfo,
      },
      meta: {
        generatedAtWallClock: new Date().toISOString(),
        note: "meta는 재현성 해시 대상에서 제외한다.",
      },
    };
    mkdirSync(dirname(targetPath), { recursive: true });
    writeFileSync(targetPath, `${JSON.stringify(trace, null, 2)}\n`, "utf8");
    console.log(`[g3] 트레이스 기록: ${targetPath}`);
    if (copyPath) {
      mkdirSync(dirname(copyPath), { recursive: true });
      writeFileSync(copyPath, `${JSON.stringify(trace, null, 2)}\n`, "utf8");
      console.log(`[g3] 트레이스 사본 기록: ${copyPath}`);
    }
  }

  try {
    // ── 오라클 검증 (회차 실행 전, fail closed) ────────────────────────
    const ethUsd = await readAndValidateOracle(CHAINLINK_ETH_USD_FEED, EXPECTED_ORACLE.ethUsd);
    const usdcUsd = await readAndValidateOracle(CHAINLINK_USDC_USD_FEED, EXPECTED_ORACLE.usdcUsd);
    oracleInfo = {
      ethUsd: ethUsd.fields,
      usdcUsd: usdcUsd.fields,
      freshnessCeilingSeconds: ORACLE_MAX_STALENESS_SECONDS.toString(),
    };
    console.log("[g3] 오라클 검증 통과 (ETH/USD, USDC/USD)");

    // ── 시작 상태 ────────────────────────────────────────────────────
    const startUsdc = await readUsdcBalance(delegator);
    const startEthWei = await publicClient.getBalance({ address: delegator });
    if (startUsdc !== STARTING_USDC_BALANCE) {
      throw new Error(`[g3] 시작 USDC 잔고 불일치: 기대 ${STARTING_USDC_BALANCE}, 실측 ${startUsdc}`);
    }
    if (startEthWei !== STARTING_ETH_BALANCE) {
      throw new Error(`[g3] 시작 ETH 잔고 불일치: 기대 ${STARTING_ETH_BALANCE}, 실측 ${startEthWei}`);
    }

    // ── 20회차 루프 ──────────────────────────────────────────────────
    const delegateWallet = walletFor(accounts.delegate);
    const periodCounts = new Array(6).fill(0) as number[];

    for (let n = 1; n <= G3_STEP_COUNT; n++) {
      const stepIndex = n - 1;
      const timestamp = BASE_TIMESTAMP + STEP_TIMESTAMP_OFFSET * BigInt(n);

      // 결정론: 벽시계 유입 금지. 블록 타임스탬프를 매 회차 명시 설정한다.
      await publicClient.request({
        method: "evm_setNextBlockTimestamp" as any,
        params: [toHex(timestamp)] as any,
      });

      const usdcBefore = await readUsdcBalance(delegator);
      const ethBefore = await publicClient.getBalance({ address: delegator });

      const callData = encodeFunctionData({
        abi: [ERC20_TRANSFER],
        functionName: "transfer",
        args: [addressOf.counterparty, G3_STEP_AMOUNT_USDC],
      });
      const data = encodeFunctionData({
        abi: REDEEM_ABI,
        functionName: "redeemDelegations",
        args: [[permissionContext], [MODE_CODE_SIMPLE_SINGLE], [encodeSingleExecution(USDC_ADDRESS, 0n, callData)]],
      });

      // gas 명시 — 가스 추정 실패로 온체인 기록이 안 남는 것을 막는다.
      const txHash = await delegateWallet.sendTransaction({ to: dm, data, gas: 3_000_000n });
      const receipt = await publicClient.waitForTransactionReceipt({ hash: txHash });

      const usdcAfter = await readUsdcBalance(delegator);
      const ethAfter = await publicClient.getBalance({ address: delegator });

      // 1. revert 0건이 G3의 정의다.
      if (receipt.status !== "success") {
        throw new Error(`[g3] step ${n}: receipt.status=${receipt.status} (성공이어야 한다)`);
      }

      // 2. 정확히 500 USDC 감소 (>= / <= 가 아니라 정확히 일치).
      const decreased = usdcBefore - usdcAfter;
      if (decreased !== G3_STEP_AMOUNT_USDC) {
        throw new Error(`[g3] step ${n}: USDC 감소량 불일치 (기대 ${G3_STEP_AMOUNT_USDC}, 실측 ${decreased})`);
      }

      // 3. delegator ETH는 불변이어야 한다 (가스는 delegate가 낸다) — 포트폴리오 주장의 전제.
      if (ethAfter !== ethBefore) {
        throw new Error(`[g3] step ${n}: delegator ETH가 변했다 (before=${ethBefore}, after=${ethAfter})`);
      }

      // 4. USDC Transfer 이벤트 증거.
      const expectedDataHex = pad(toHex(G3_STEP_AMOUNT_USDC), { size: 32 }).toLowerCase();
      const delegatorTopic = pad(delegator, { size: 32 }).toLowerCase();
      const counterpartyTopic = pad(addressOf.counterparty, { size: 32 }).toLowerCase();
      const transferLog = receipt.logs.find(
        (l) =>
          l.address.toLowerCase() === USDC_ADDRESS.toLowerCase() &&
          l.topics[0]?.toLowerCase() === TRANSFER_EVENT_TOPIC.toLowerCase() &&
          l.topics[1]?.toLowerCase() === delegatorTopic &&
          l.topics[2]?.toLowerCase() === counterpartyTopic &&
          l.data.toLowerCase() === expectedDataHex,
      );
      if (!transferLog) {
        throw new Error(`[g3] step ${n}: USDC Transfer 이벤트를 찾지 못했다`);
      }

      // 5. ERC20PeriodTransferEnforcer 이벤트 증거 (enforcer가 실제로 실행됐다는 온체인 증거).
      const periodEnforcerAddr = manifest.enforcers["ERC20PeriodTransferEnforcer"].toLowerCase();
      const dmTopic = pad(dm, { size: 32 }).toLowerCase();
      const delegateTopic = pad(addressOf.delegate, { size: 32 }).toLowerCase();
      const periodLogs = receipt.logs.filter(
        (l) =>
          l.address.toLowerCase() === periodEnforcerAddr &&
          l.topics[0]?.toLowerCase() === PERIOD_TRANSFER_EVENT_TOPIC.toLowerCase() &&
          l.topics[1]?.toLowerCase() === dmTopic &&
          l.topics[2]?.toLowerCase() === delegateTopic &&
          l.topics[3]?.toLowerCase() === delegationHash.toLowerCase(),
      );
      if (periodLogs.length !== 1) {
        throw new Error(
          `[g3] step ${n}: 정확한 ERC20PeriodTransferEnforcer 이벤트가 1건이어야 한다 (실측 ${periodLogs.length})`,
        );
      }
      const periodLog = periodLogs[0];

      // 6. 회차 직후 온체인 periodic allowance 조회 + period index 대조.
      const allowance = (await publicClient.readContract({
        address: manifest.enforcers["ERC20PeriodTransferEnforcer"],
        abi: PERIODIC_ALLOWANCES_ABI,
        functionName: "periodicAllowances",
        args: [dm, delegationHash],
      })) as readonly bigint[];
      const [periodAmount, periodDuration, startDate, lastTransferPeriod, transferredInCurrentPeriod] = allowance;

      const expectedPeriodData = encodeAbiParameters(
        [
          { type: "address" },
          { type: "uint256" },
          { type: "uint256" },
          { type: "uint256" },
          { type: "uint256" },
          { type: "uint256" },
        ],
        [USDC_ADDRESS, periodAmount, periodDuration, startDate, transferredInCurrentPeriod, timestamp],
      );
      if (periodLog.data.toLowerCase() !== expectedPeriodData.toLowerCase()) {
        throw new Error(`[g3] step ${n}: ERC20PeriodTransferEnforcer 이벤트 데이터 불일치`);
      }

      if (transferredInCurrentPeriod > CAVEAT_PARAMS.erc20Period.periodAmount) {
        throw new Error(`[g3] step ${n}: 일일 한도 초과 (${transferredInCurrentPeriod})`);
      }

      const periodIndex0 = Number((timestamp - startDate) / periodDuration);
      if (lastTransferPeriod !== BigInt(periodIndex0) + 1n) {
        throw new Error(
          `[g3] step ${n}: period index 불일치 (계산 ${periodIndex0}, 온체인 lastTransferPeriod ${lastTransferPeriod})`,
        );
      }
      periodCounts[periodIndex0] += 1;

      // 7. caveat 6종 전부 통과 기록. caveatRecords는 서명·인코딩에 쓴 것과 같은 배열에서 파생됐다.
      const caveatsPassed: CaveatPassedRecord[] = caveatRecords.map((c) => ({
        ...c,
        evidence: "attached-signed-delegation + successful-receipt",
      }));

      steps.push({
        stepIndex,
        n,
        timestamp: timestamp.toString(),
        txHash,
        blockNumber: receipt.blockNumber.toString(),
        receiptStatus: receipt.status,
        gasUsed: receipt.gasUsed.toString(),
        transferAmount: G3_STEP_AMOUNT_USDC.toString(),
        usdcBefore: usdcBefore.toString(),
        usdcAfter: usdcAfter.toString(),
        ethBefore: ethBefore.toString(),
        ethAfter: ethAfter.toString(),
        transferEvent: {
          logIndex: transferLog.logIndex as number,
          address: transferLog.address,
          topics: transferLog.topics as Hex[],
          data: transferLog.data,
        },
        periodEnforcerEvent: {
          logIndex: periodLog.logIndex as number,
          address: periodLog.address,
          topics: periodLog.topics as Hex[],
          data: periodLog.data,
        },
        periodicAllowanceAfter: {
          periodAmount: periodAmount.toString(),
          periodDuration: periodDuration.toString(),
          startDate: startDate.toString(),
          lastTransferPeriod: lastTransferPeriod.toString(),
          transferredInCurrentPeriod: transferredInCurrentPeriod.toString(),
        },
        periodIndex: periodIndex0,
        caveatsPassed,
      });
      states.push({
        stepIndex,
        before: { usdc: usdcBefore.toString(), ethWei: ethBefore.toString() },
        after: { usdc: usdcAfter.toString(), ethWei: ethAfter.toString() },
      });

      console.log(
        `[g3] step ${n} status=${receipt.status} block=${receipt.blockNumber} ` +
          `periodIdx=${periodIndex0} consumed=${transferredInCurrentPeriod.toString()}`,
      );
    }

    // ── 종료 상태 + 포트폴리오 계산 ─────────────────────────────────────
    const endUsdc = await readUsdcBalance(delegator);
    const endEthWei = await publicClient.getBalance({ address: delegator });

    const startUsdcValue = assetValue1e18(startUsdc, BigInt(USDC_DECIMALS), usdcUsd.answer, BigInt(CHAINLINK_USDC_USD_DECIMALS));
    const startEthValue = assetValue1e18(startEthWei, 18n, ethUsd.answer, BigInt(CHAINLINK_ETH_USD_DECIMALS));
    const startTotal = startUsdcValue + startEthValue;

    const endUsdcValue = assetValue1e18(endUsdc, BigInt(USDC_DECIMALS), usdcUsd.answer, BigInt(CHAINLINK_USDC_USD_DECIMALS));
    const endEthValue = assetValue1e18(endEthWei, 18n, ethUsd.answer, BigInt(CHAINLINK_ETH_USD_DECIMALS));
    const endTotal = endUsdcValue + endEthValue;

    const loss = startTotal - endTotal;
    // BigInt 내림 나눗셈 — lossBps는 항상 내림된다 (실제 손실률을 과대 계상하지 않기 위함).
    const lossBps = (loss * 10_000n) / startTotal;

    // ── 전역 검증 (fail closed) ─────────────────────────────────────
    const totalTransferred = steps.reduce((sum, s) => sum + BigInt(s.transferAmount), 0n);
    const periodTotals = periodCounts.map((c) => BigInt(c) * G3_STEP_AMOUNT_USDC);
    const distributionMatches =
      periodCounts.length === G3_EXPECTED_PERIOD_DISTRIBUTION.length &&
      periodCounts.every((c, i) => c === G3_EXPECTED_PERIOD_DISTRIBUTION[i]);

    const checks = [
      { name: "stepCountIs20", passed: steps.length === G3_STEP_COUNT },
      { name: "allStepsSuccess", passed: steps.every((s) => s.receiptStatus === "success") },
      { name: "allStepsExactly500", passed: steps.every((s) => s.transferAmount === G3_STEP_AMOUNT_USDC.toString()) },
      { name: "periodDistributionMatches", passed: distributionMatches },
      {
        name: "periodTotalsWithinDailyLimit",
        passed: periodTotals.every((t) => t <= CAVEAT_PARAMS.erc20Period.periodAmount),
      },
      { name: "totalTransferredIs10000Usdc", passed: totalTransferred === STARTING_USDC_BALANCE },
      { name: "totalTransferredEqualsStartingUsdc", passed: totalTransferred === startUsdc },
      { name: "endUsdcIsZero", passed: endUsdc === 0n },
      { name: "endEthUnchanged", passed: endEthWei === startEthWei },
      { name: "lossPositive", passed: loss > 0n },
    ];

    resultInfo = {
      startingPortfolio: {
        usdc: startUsdc.toString(),
        ethWei: startEthWei.toString(),
        usdcValue1e18: startUsdcValue.toString(),
        ethValue1e18: startEthValue.toString(),
        totalValue1e18: startTotal.toString(),
      },
      endingPortfolio: {
        usdc: endUsdc.toString(),
        ethWei: endEthWei.toString(),
        usdcValue1e18: endUsdcValue.toString(),
        ethValue1e18: endEthValue.toString(),
        totalValue1e18: endTotal.toString(),
      },
      loss: loss.toString(),
      lossBps: lossBps.toString(),
      periods: periodCounts.map((count, i) => ({
        periodIndex: i,
        count,
        totalTransferredUsdc: periodTotals[i].toString(),
        dailyLimitUsdc: CAVEAT_PARAMS.erc20Period.periodAmount.toString(),
        withinLimit: periodTotals[i] <= CAVEAT_PARAMS.erc20Period.periodAmount,
      })),
      checks,
    };

    const failedChecks = checks.filter((c) => !c.passed);
    if (failedChecks.length > 0) {
      throw new Error(`[g3] 전역 검증 실패: ${failedChecks.map((c) => c.name).join(", ")}`);
    }
    writeTrace(TRACE_PATH, process.env.G3_TRACE_OUT);
    console.log(`[g3] 전역 검증 통과 (${checks.length}건)`);

    // ── 결정론 요약 (표준출력) ────────────────────────────────────────
    const canonicalHashed = canonical({
      fork: forkInfo,
      baseline: baselineInfo,
      delegation: delegationInfo,
      steps,
      states,
      oracle: oracleInfo,
      result: resultInfo,
    });
    const digest = keccak256(toHex(JSON.stringify(canonicalHashed)));

    console.log(`G3_STEP_COUNT=${G3_STEP_COUNT}`);
    console.log(`G3_DELEGATION_HASH=${delegationHash}`);
    console.log(`G3_END_USDC=${endUsdc.toString()}`);
    console.log(`G3_PERIOD_DISTRIBUTION=${periodCounts.join(",")}`);
    console.log(`G3_PORTFOLIO_START=${startTotal.toString()}`);
    console.log(`G3_PORTFOLIO_END=${endTotal.toString()}`);
    console.log(`G3_LOSS=${loss.toString()}`);
    console.log(`G3_LOSS_BPS=${lossBps.toString()}`);
    console.log(`G3_TRACE_DIGEST=${digest}`);
  } catch (err) {
    // fail closed — 정본 성공 트레이스는 보존하고 실패 진단은 별도 파일에 남긴다.
    if (resultInfo === null) {
      resultInfo = { error: String(err) };
    }
    const failedCopy = process.env.G3_TRACE_OUT ? `${process.env.G3_TRACE_OUT}.failed` : undefined;
    writeTrace(FAILED_TRACE_PATH, failedCopy);
    throw err;
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
