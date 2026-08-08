# Phase 1 baseline 파라미터 (PM 확정, 변경 금지)

확정 2026-08-08. `baseline-config.md` §4의 "한도 파라미터" 칸을 채우는 근거 문서.

**실측 전에 먼저 박는다.** 결과가 안 나온다고 한도를 옮기는 것이 심사에서 가장 먼저
의심받는 행위이므로, 값과 근거를 구현 착수 전에 고정한다.
`phase1-acceptance.md` §검증 시 확인할 것 2번("통과시키려고 한도를 비정상적으로 크게
잡지 않았는가")의 답이 이 문서다.

## 시작 포트폴리오

| 자산 | 수량 | 포크 블록 오라클 환산 |
| --- | --- | --- |
| USDC | `10_000_000_000` (10,000 USDC, 6 decimals) | $10,000.00 |
| ETH | `10_000_000_000_000_000_000` (10 ETH) | $18,981.11 (@ $1898.11) |
| **합계** | | **$28,981.11** |

ETH 10개는 가스 지불과 G3의 자산 간 상계 논거(`netDeltaBound`)를 위해 둔다.
Phase 1 G1/G2 범위에서는 가스 지불에만 쓰인다.

## caveat 파라미터

| enforcer | 파라미터 | 값 | 근거 |
| --- | --- | --- | --- |
| `AllowedTargetsEnforcer` | targets | `[USDC]` | 에이전트에게 준 프로토콜 허용목록. 단일 결제 토큰. |
| `AllowedMethodsEnforcer` | selectors | `[0xa9059cbb]` (`transfer(address,uint256)`) | 승인(`approve`)조차 주지 않는다. 실제 Agent Wallet보다 **더 좁다.** |
| `ValueLteEnforcer` | maxValue | `0` | 네이티브 ETH 이동 권한 없음. baseline을 최대로 조인 설정. |
| `TimestampEnforcer` | after / before | `1786068491` / `1788660491` | 포크 블록 ts ~ +30일(2592000s). 위임 유효기간. |
| `ERC20PeriodTransferEnforcer` | token | USDC | |
| | periodAmount | `2_000_000_000` (2,000 USDC) | 일일 지출 한도. 시작 USDC의 **20%**. |
| | periodDuration | `86400` | Agent Wallet의 **일일 리셋**을 그대로 재현. |
| | startDate | `1786068491` | 포크 블록 ts. |
| `ERC20BalanceChangeEnforcer` | enforceDecrease | `0x01` | 감소 상한 검사. |
| | token / recipient | USDC / delegator 스마트계정 | 사용자 자산 감소를 본다. |
| | amount | `500_000_000` (500 USDC) | **회차당** 최대 감소. 일일 한도의 1/4. |

### 왜 이 값들이 "약하게 잡은 것"이 아닌가

1. **`ValueLteEnforcer = 0`** — 네이티브 값을 한 푼도 허용하지 않는다. 상한을 크게
   잡아 통과시킨 것의 정반대다.
2. **`AllowedMethods = [transfer]` 하나** — `approve`도 없다. 실제 Agent Wallet은
   DEX 스왑을 허용하므로 라우터·`approve`가 필요하다. 우리 baseline이 더 좁다.
3. **회차 한도(500) < 일일 한도(2,000) < 잔고(10,000)** — 세 층이 전부 유효하게 걸린다.
   회차 한도를 일일 한도와 같게 두면 회차 층이 무의미해지는데, 그렇게 하지 않았다.
4. **`ERC20BalanceChangeEnforcer` 포함** — `baseline-config.md` §2대로, 상태 차분을
   보는 enforcer를 **일부러** 넣었다. 빼는 것이 통과에 유리하지만 넣었다.

일일 한도 20%가 관대해 보일 수 있으나, 이것이 **작을수록** 누적 손실 논거는 강해진다
(더 촘촘한 한도 아래에서도 고갈됨을 보이는 것이므로). 즉 이 파라미터를 크게 잡는 것은
우리에게 유리하지 않다. 20%는 "에이전트에 하루치 재량을 준다"는 현실적 상한이다.

### 파생: G3에서 필요한 회차 수 (참고, Phase 1 범위 밖)

회차 500 USDC × 일 4회 = 일 2,000 USDC(일일 한도 정확히 소진).
10,000 USDC 전액 이동에 **5일 × 4회 = 20회**. 모든 회차가 회차·일일 한도를 동시에
만족하면서 잔고가 0이 된다. 이것이 `cumulativeLossCap`이 필요한 이유다.

**G3는 아직 만들지 않는다.** 이 계산은 위 파라미터가 G3를 불가능하게 만들지 않음을
확인하기 위한 것이다.

## 결정론 파라미터

| 항목 | 값 |
| --- | --- |
| 기준 타임스탬프 | `1786068491` (포크 블록 ts) |
| 회차 간 오프셋 | `21600` 초 (6시간) — 일당 4회차가 같은 period에 들어감 |
| 블록당 트랜잭션 | 1건, fifo |
| 배포자 | anvil 기본 키 #0 (`0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80`) |
| 스마트계정 소유자 | anvil 기본 키 #1 |
| delegate EOA | anvil 기본 키 #2 |
| 카운터파티(수취인) | anvil 기본 키 #3 |
| 허용목록 밖 대상 (G2용) | anvil 기본 키 #4의 주소 |
| CREATE2 salt | `"intent-as-spec-phase1"` (문자열, 업스트림 스크립트 `SALT` 규약) |

첫 회차 타임스탬프는 `1786068491 + 21600`이다. `TimestampEnforcer`의 비교가
**strict**(`block.timestamp > after`)이므로 기준 ts 그대로 쓰면 `early-delegation`이 난다.
