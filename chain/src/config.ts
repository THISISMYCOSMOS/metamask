// docs/phase1-parameters.md + docs/baseline-config.md §4 값을 그대로 옮긴 상수 모듈.
// 여기 있는 값은 임의로 바꾸지 않는다 (PM 확정).

// ── RPC ──────────────────────────────────────────────────────────────────
// .env의 RPC_URL은 절대 읽지 않는다. anvil은 하드코딩된 localhost만 쓴다.
export const ANVIL_RPC = process.env.ANVIL_RPC ?? "http://127.0.0.1:8545";

// ── 포크 결정론 파라미터 (docs/baseline-config.md §4) ───────────────────
export const FORK_CHAIN_ID = 1;
export const FORK_BLOCK_NUMBER = 25_700_000n;
export const FORK_BLOCK_HASH =
  "0x528d3ac8a0fbb982d354cbef4f842140ed0ae75cbcdf41dbd08324e298a72abf" as const;
export const FORK_BLOCK_TIMESTAMP = 1_786_068_491n;

// delegation-framework 핀 커밋
export const PINNED_COMMIT = "197463b4aba3409adef1df544dabafc3636ee82d";

// ── 오라클 / 토큰 주소 ───────────────────────────────────────────────────
export const CHAINLINK_ETH_USD_FEED =
  "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419" as const;
export const CHAINLINK_ETH_USD_DECIMALS = 8;

export const USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48" as const;
export const USDC_DECIMALS = 6;

// ── EntryPoint (v0.7, 메인넷에 이미 배포됨) ─────────────────────────────
export const ENTRYPOINT_ADDRESS =
  "0x0000000071727De22E5E9d8BAf0edAc6f37da032" as const;

// ── CREATE2 salt (업스트림 스크립트 SALT 규약, 문자열) ──────────────────
export const DEPLOY_SALT = "intent-as-spec-phase1";

// ── anvil 기본 테스트 계정 (인덱스만 정본, 개인키는 guard/account에서 파생) ─
// 배포자: key #0 / 스마트계정 소유자: key #1 / delegate EOA: key #2
// 카운터파티(수취인): key #3 / 허용목록 밖 대상(G2용): key #4
export const ACCOUNT_INDEX = {
  deployer: 0,
  owner: 1,
  delegate: 2,
  counterparty: 3,
  disallowedTarget: 4,
} as const;

// anvil 기본 테스트키 (공개적으로 알려진 anvil --mnemonic 기본 니모닉의 파생키).
// 사용자 환경/.env에서 절대 읽지 않는다. 이 노드는 anvil 기본 계정으로만 자금이 있다.
export const ANVIL_DEFAULT_PRIVATE_KEYS = [
  "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80", // #0 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
  "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d", // #1 0x70997970C51812dc3A010C7d01b50e0d17dc79C8
  "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a", // #2 0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC
  "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6", // #3 0x90F79bf6EB2c4f870365E785982E1f101E93b906
  "0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a", // #4 0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65
] as const;

// ── 시작 포트폴리오 (docs/phase1-parameters.md) ─────────────────────────
export const STARTING_USDC_BALANCE = 10_000_000_000n; // 10,000 USDC (6 decimals)
export const STARTING_ETH_BALANCE = 10_000_000_000_000_000_000n; // 10 ETH

// ── caveat 파라미터 (docs/phase1-parameters.md, 변경 금지) ──────────────
export const CAVEAT_PARAMS = {
  allowedTargets: [USDC_ADDRESS] as const,
  allowedMethodSelectors: ["0xa9059cbb"] as const, // transfer(address,uint256)
  valueLteMax: 0n,
  timestampAfter: 1_786_068_491n,
  timestampBefore: 1_788_660_491n,
  erc20Period: {
    token: USDC_ADDRESS,
    periodAmount: 2_000_000_000n, // 2,000 USDC 일일 한도
    periodDuration: 86_400n,
    startDate: 1_786_068_491n,
  },
  erc20BalanceChange: {
    enforceDecrease: true,
    token: USDC_ADDRESS,
    // recipient는 delegator 스마트계정(사용자 자산 감소를 본다) — deploy 이후 채워짐.
    amount: 500_000_000n, // 회차당 최대 감소 500 USDC
  },
} as const;

// ── 결정론 파라미터 ───────────────────────────────────────────────────
export const BASE_TIMESTAMP = 1_786_068_491n; // 포크 블록 ts
export const STEP_TIMESTAMP_OFFSET = 21_600n; // 6시간, 회차 간 오프셋
// 첫 회차 타임스탬프 = BASE_TIMESTAMP + STEP_TIMESTAMP_OFFSET (TimestampEnforcer strict >)

// ── DelegationManager EIP-712 도메인 ────────────────────────────────────
export const DELEGATION_DOMAIN_NAME = "DelegationManager";
export const DELEGATION_DOMAIN_VERSION = "1"; // "1.3.0" 아님. DOMAIN_VERSION 상수.

// ROOT_AUTHORITY = 0xff...ff (32바이트 전부 0xff)
export const ROOT_AUTHORITY =
  "0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff" as const;

// delegation의 salt (redeem용, EIP-712 salt 필드). 결정론 재현을 위해 고정.
export const DELEGATION_SALT = 0n;

// ── 매니페스트 검증 ──────────────────────────────────────────────────
export const EXPECTED_ENFORCER_COUNT = 37;

// PM 실측 결정론 검증용 기대 주소 (브리프 §5.2)
export const EXPECTED_ADDRESSES = {
  DelegationManager: "0xeA6F34E56c9bEa6d9114A30b52e040af2b594373",
  HybridDeleGatorImpl: "0xd321B8751D0dE55F9D8e25117216FFF1f1923805",
  MultiSigDeleGatorImpl: "0x9094408F8A7430b482Ed48da73F76AD00CF03e39",
  AllowedTargetsEnforcer: "0xef2f79e2A6Cda4f31bd213b0d1877a9B93F70038",
  AllowedMethodsEnforcer: "0x27aF251F5cd8AE094925aEF1722655Ea822Edbe1",
  ValueLteEnforcer: "0x0B5C5BEA5Df2fA9879Fac0AA3690aE2caD9eC498",
  TimestampEnforcer: "0x8AF1d7a43158697106953f7F2EfADa603984269A",
  ERC20PeriodTransferEnforcer: "0x5c0FD678387dD9a4f6D7ae4a4a2798439a0AEBb0",
  ERC20BalanceChangeEnforcer: "0x2Ab40067D719bc5938AA1875CB409A9DBF50022c",
} as const;

// ── G3 오라클 (포크 블록 스냅샷, PM 핀) ─────────────────────────────
// USDC/USD 어그리게이터. 포크 블록에서 실측한 값을 기대값으로 박는다.
export const CHAINLINK_USDC_USD_FEED = "0xc9E1a09622afdB659913fefE800fEaE5DBbFe9d7" as const;
export const CHAINLINK_USDC_USD_DECIMALS = 8;

export const EXPECTED_ORACLE = {
  ethUsd: { answer: 189_811_115_161n, updatedAt: 1_786_066_847n, decimals: 8 },
  usdcUsd: { answer: 99_976_752n, updatedAt: 1_786_003_223n, decimals: 8 },
} as const;

// 오라클 신선도 상한 — G3 구현 시 코드/검증기에 고정했다.
// 피드 age 관측 전에 선택했다는 역사적 근거는 없으므로 사전선정으로 주장하지 않는다.
export const ORACLE_MAX_STALENESS_SECONDS = 86_400n;

// ── G3 회차 파라미터 ────────────────────────────────────────────────
export const G3_STEP_COUNT = 20;
export const G3_STEP_AMOUNT_USDC = 500_000_000n; // 회차당 정확히 500 USDC
export const EXPECTED_DELEGATION_HASH =
  "0x9c79a1b3758c54c83757c4d724957df8333966500183b78986b7abcf7bbe7ebb" as const;
// 실측 period 분포. offset 21600s + strict TimestampEnforcer의 귀결이다.
export const G3_EXPECTED_PERIOD_DISTRIBUTION = [3, 4, 4, 4, 4, 1] as const;

// 포트폴리오 가치 단위: 1e-18 USD (정수 전용, 부동소수 금지)
export const USD_VALUE_SCALE_DECIMALS = 18n;
