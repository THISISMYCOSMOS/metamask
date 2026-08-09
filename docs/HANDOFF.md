# 세션 인계 — Phase 1 완료, Phase 3 평가기 세로 슬라이스 진행

## 2026-08-09 현재 상태 — 아래의 과거 G3 대기 설명보다 우선

- Phase 1 G1·G2·G3가 완료되어 `feat`에 커밋·푸시됐다. Phase 1 정본 커밋은
  `39ce117aef25d4b19f803ac14dee76a4b65ca07c`이다.
- G3 결정론 해시는
  `0x7070733f52215bd255c69fe863efa33e780f72aa7c162c6ff3f9f9574549dcf7`이다.
- 다음 방향은 추가 공격 트레이스보다 먼저 결정론적 평가기 세로 슬라이스를 완성하는 것이다.
  기존 G3가 이미 골든 네거티브 입력을 제공하기 때문이다.
- `portfolioValueFloor`와 `cumulativeLossCap`의 strict/versioned 정책 모델, 정수 전용
  평가기, fail-closed CLI, 경계·변조·정상 통제 스트레스 테스트가 추가됐다.
- 상세 합격 기준과 아직 주장하지 않는 범위는 `docs/phase3-acceptance.md`를 따른다.
- `specs/phase1-demo-invariants.json`은 평가기 검증용 fixture이며 실제 사용자 정책이 아니다.
- 여전히 남은 핵심 작업은 generic pre-execution trace 계약, 나머지 불변식 3종, 자연어 합성,
  사용자 승인 경로, 실제 실행 전 연결과 성능 측정이다.

아래 본문은 Phase 1 당시의 결정 근거와 함정을 보존한 역사 기록이다. “다음은 G3” 및
“G3 승인 대기” 문구는 현재 작업 지시로 사용하지 않는다.

갱신 2026-08-08. **다음 세션은 이 문서부터 읽는다.**

## 0. 지금 상태 한 줄

**Phase 1의 G1(결정론)·G2(baseline 생존)가 통과했고 커밋됐다 (`ca22cc3`, 브랜치 `feat`, 푸시 안 함).**
다음 관문은 **사용자의 G1·G2 검증**이다. 그 승인 전에는 G3을 만들지 않는다.

```bash
bash chain/scripts/reproduce.sh   # 배포 + G2 트레이스 + 상태 다이제스트, 한 명령
```

두 번 돌려 마지막 줄 `STATE_DIGEST`가 같으면 G1이 재현된 것이다.

## 1. 역할 (사용자 지정, 유지할 것)

- **Opus = PM 겸 검증자.** 설계 결정과 판정은 Opus가 직접 한다.
- **개발 = Sonnet 서브에이전트.** 서브에이전트가 또 서브에이전트를 띄우지 못하게 지시에 명시.
- 중요한 보고·판정·블로커는 **한글로**.

> **단, 이번 세션에서 서브에이전트가 두 번 죽었다** (API 스톨 1회, 600초 워치독 1회).
> 남긴 코드에 실제 버그가 있었다(`ROOT_AUTHORITY`가 31바이트, 미정의 함수 호출).
> 두 번 죽은 시점에서 PM이 나머지 구현을 떠안았다. 다음에도 같은 패턴이면
> **긴 작업은 처음부터 PM이 하거나, 서브에이전트에게 "파일 하나 완성할 때마다 즉시 디스크에
> 쓰라"고 지시**해야 진척이 남는다.

먼저 읽을 것: `README.md`, `docs/phase1-acceptance.md`, `docs/baseline-config.md`,
`docs/phase1-parameters.md`, `docs/caveat-encoding.md`, `chain/README.md`.
스펙과 합격 기준은 전부 그 안에 있다. 사용자는 따로 설명하지 않는다.

---

## 2. 고정 파라미터 (변경 금지, 논문에 박힘)

`docs/baseline-config.md` §4가 정본. 실측값으로 전부 채워졌다.

| 항목 | 값 |
| --- | --- |
| delegation-framework 커밋 | `197463b4aba3409adef1df544dabafc3636ee82d` |
| 포크 | Ethereum mainnet (chainId 1), 블록 `25700000` |
| 포크 블록 해시 | `0x528d3ac8a0fbb982d354cbef4f842140ed0ae75cbcdf41dbd08324e298a72abf` |
| 포크 블록 타임스탬프 | `1786068491` |
| Chainlink ETH/USD | `0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419`, 해당 블록 `189811115161` ($1898.11) |
| USDC | `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48`, decimals 6 |
| CREATE2 salt | `"intent-as-spec-phase1"` |
| `DelegationManager` | `0xeA6F34E56c9bEa6d9114A30b52e040af2b594373` |
| delegator 스마트계정 | `0x09e68b4a2335a2aaa1944bc3938d285b883f11e1` |
| 일일 한도 / 회차 한도 | 2,000 USDC / 500 USDC (근거는 `docs/phase1-parameters.md`) |

**구체 enforcer는 37종이다.** `src/enforcers/`의 `.sol` 파일은 38개지만 `CaveatEnforcer`는
`abstract contract` 베이스로 배포 대상이 아니다. 태그 `v1.3.0`은 32종.
논문에 38을 쓰면 심사자가 업스트림 배포 스크립트를 세어 즉시 반박한다.

### 활성 caveat 6종 — 배열 순서에 의미가 있다

`DelegationManager`의 `beforeHook` 루프가 **오름차순**으로 평가하므로
**이 순서가 위반 시 관측되는 revert 문자열을 결정한다.** 바꾸지 말 것.

| index | enforcer | 대응하는 Agent Wallet 통제 |
| --- | --- | --- |
| 0 | `AllowedTargetsEnforcer` | 프로토콜 허용목록 |
| 1 | `AllowedMethodsEnforcer` | 메서드 허용목록 |
| 2 | `ValueLteEnforcer` | 네이티브 값 상한 |
| 3 | `TimestampEnforcer` | 위임 유효 기간 |
| 4 | `ERC20PeriodTransferEnforcer` (period 86400) | 일일 지출 한도 + 일일 리셋 |
| 5 | `ERC20BalanceChangeEnforcer` | 상태 차분 검사 |

`AllowedTargets`가 `ERC20PeriodTransfer`보다 뒤에 있으면 허용목록 밖 호출이
`target-address-not-allowed`가 아니라 `ERC20PeriodTransferEnforcer:invalid-contract`로
터진다. 그러면 의도와 다른 이유의 revert를 네거티브 컨트롤이라고 잘못 라벨하게 된다.

`ERC20BalanceChangeEnforcer`는 **의도적으로 포함**했다. 빼고 "baseline은 정적 속성만
본다"고 쓰면 MetaMask 심사자가 자사 코드로 즉시 반박한다. baseline을 더 강하게 만든
상태로 뚫어야 논문이 산다.

---

## 3. 완료된 것 (Phase 1 전체)

- 서브모듈 벤더링, `.gitignore`, `.env`, RPC/아카이브 검증 — 전부 완료.
- **Step 1** 빌드 — `forge build` exit 0, 아티팩트 258개. 캐시가 데워져 있어 재빌드는 빠르다.
- **Step 2** 배포 — 구체 enforcer 37종 + `DelegationManager` + delegator 스마트계정
  (`ERC1967Proxy` + `HybridDeleGator`, 소유자 = anvil key #1). `chain/deployments/manifest.json`.
  `deploy.ts`가 CREATE2 주소를 기대값과 대조해 어긋나면 throw한다.
- **Step 3** viem 하니스 — `terms` 인코더 6종, EIP-712 서명, 실행 인코딩.
- **Step 4** G1 — 독립 2회 실행에서 스냅샷 **바이트 단위 일치**.
  `STATE_DIGEST=0xe29efd531e1a734a1fd94a6bfd53338d0a296f0185d0e072cb9c9c1f3a26ef48`
  (최종 블록 25700048)
- **Step 5** G2 — `traces/negative-control.json`. 합격 기준 2건을 넘어 **6종 전부** 증명.
- **Step 6** 문서 — `chain/README.md`, `baseline-config.md` §4 실측값.

### G2 실측 결과

| 회차 | 관측된 revert | 가스 |
| --- | --- | --- |
| PC0 (400 USDC) | **성공** | 398,506 |
| NC1 일일 한도 초과 2,500 | `ERC20PeriodTransferEnforcer:transfer-amount-exceeded` | 144,336 |
| NC2 허용목록 밖 대상 | `AllowedTargetsEnforcer:target-address-not-allowed` | 113,793 |
| NC3 회차 한도만 초과 800 | `ERC20BalanceChangeEnforcer:exceeded-balance-decrease` | 226,731 |
| NC4 허용목록 밖 메서드 | `AllowedMethodsEnforcer:method-not-allowed` | 119,209 |
| NC5 네이티브 값 1 wei | `ValueLteEnforcer:value-too-high` | 123,296 |
| NC6 유효기간 경과 | `TimestampEnforcer:expired-delegation` | 127,168 |

- 전 회차가 고유 `txHash`/`blockNumber`/`receiptStatus`를 갖는다. revert 문자열은
  `debug_traceTransaction`으로 노드에서 뽑았다. **TS 예측 예외가 아니다.**
- 가스 소모량이 교차 검증한다 — 앞 index에서 막힌 회차일수록 가스가 적고, NC3은 실행까지
  마친 뒤 `afterHook`에서 막혀 가장 크다. 예측 예외였다면 이런 차이가 나올 수 없다.
- **PC0가 필수인 이유**: 통과 회차가 없으면 NC들의 revert가 "baseline이 막았다"인지
  "하니스가 고장났다"인지 구분할 수 없다.
- NC3이 특히 값지다. `beforeHook`을 전부 통과한 뒤 **상태 차분 검사**에서 막힌다.

### 이전 세션 전제 중 실측으로 틀린 것 2개

1. **"anvil이 실제 state root를 계산하므로 `stateRoot`를 정본으로 쓴다"** — 틀렸다.
   anvil은 **포크 모드에서 머클 상태 루트를 계산하지 않는다.** 로컬 채굴 블록의 헤더
   `stateRoot`가 전부 `0x00…00`이다. 대체 다이제스트를 `chain/src/state-digest.ts`에
   구현했다(노드가 실제 계산한 `transactionsRoot`/`receiptsRoot` + 명시 열거 계정 상태
   + `ERC20PeriodTransferEnforcer`의 누적 기간 사용량).
2. **"enforcer 38종 / v1.3.0 33종"** — 파일 개수였다. 구체 enforcer는 37종 / 32종.

---

## 4. 다음 세션이 할 일

### 4-1. 먼저 사용자 검증을 받는다

`phase1-acceptance.md` §검증 시 확인할 것의 1~3번이 대상이다. 근거는 위 §3과
`chain/README.md`에 전부 있다. **승인 없이 G3으로 넘어가지 않는다.**

### 4-2. G3 착수 전에 반드시 풀어야 할 설계 문제 (미해결)

**G3의 "포트폴리오 가치 감소가 오라클 환산인가"를 현재 자산 구성으로는 만족할 수 없다.**

`phase1-acceptance.md` G3는 "시퀀스 종료 후 **오라클 환산** 포트폴리오 가치가 시작 대비
유의미하게 감소"를 요구하고, 검증 항목 3번이 "오라클 환산 결과인가, 임의 계산인가"다.
그런데 현재 활성 caveat 구성은 `AllowedMethods = [transfer]` + `AllowedTargets = [USDC]`라
**USDC 전송만 가능하다.** USDC만 움직이면 ETH/USD 피드가 개입할 여지가 없고, 가치 감소가
그냥 명목 수량이 되어 "임의 계산"으로 무너진다.

선택지(다음 세션이 사용자와 확정할 것):

- **(a) 허용목록을 넓힌다** — DEX 라우터를 `AllowedTargets`에, `approve`를 `AllowedMethods`에
  추가하고 USDC↔ETH 스왑을 반복해 슬리피지/수수료로 가치를 깎는다.
  오라클 환산이 자연스럽게 성립한다. 대신 baseline이 넓어지고 포크에 실제 풀 상태가 필요하다.
- **(b) ETH도 함께 움직인다** — 네이티브 전송을 허용해야 하므로 `ValueLteEnforcer = 0`을
  풀어야 한다. 지금 가장 강한 설정 하나를 약화시키는 셈이라 심사에서 불리하다.
- **(c) 시작 포트폴리오를 ETH 비중 높게 잡고 USDC 유출만으로 비중을 왜곡시킨다** —
  가치 환산에 오라클이 들어가긴 하나 "감소"의 주원인이 여전히 명목 USDC라 약하다.

**PM 소견: (a)가 논문에 가장 강하다.** 다만 baseline을 넓히는 것이므로 `phase1-parameters.md`에
근거를 남기고, 넓힌 뒤에도 G2를 다시 돌려 baseline이 여전히 살아 있음을 재증명해야 한다.
**결과가 안 나온다고 파라미터를 완화하는 것과 구분되게 기록할 것.**

### 4-3. G3 산술 (현재 파라미터 기준, 참고)

회차 500 USDC × 일 4회 = 일 2,000(일일 한도 정확히 소진).
10,000 USDC 고갈에 5일 × 4회 = **20회차**. 모든 회차가 회차·일일 한도를 동시에 만족하면서
잔고가 0이 된다. 이것이 `cumulativeLossCap` 불변식의 필요성 논거다.

기존 하니스에 회차 배열만 추가하면 되도록 `negative-control.ts`가 구성돼 있다.

---

## 5. 반드시 지킬 함정

### 결정론 (G1이 여기서 깨진다 — 실제로 한 번 깨졌다)

**`evm_setNextBlockTimestamp`를 트랜잭션 회차에만 걸고 배포 블록 ~41개를 방치했더니**
그 블록들의 타임스탬프가 벽시계에서 들어와 실행마다 1초씩 달라졌고, 부모 해시 연쇄로
이후 모든 블록 해시가 어긋났다. `deploy.ts` 시작 시 `anvil_setBlockTimestampInterval(1)` +
`evm_setNextBlockTimestamp`로 고정해 해결했다.
**"트랜잭션 시각만 고정"이 아니라 로컬에서 채굴되는 모든 블록이 대상이다.**

- 블록당 트랜잭션 1건, `--order fifo`. 배포키 고정, CREATE2 salt 고정.
- **트레이스 JSON의 해시 대상 영역에 벽시계 시각·소요시간·절대경로·실행 ID를 넣지 말 것.**
  현재 구조는 `hashed` / `meta`로 분리돼 있다. 이 분리를 유지하라.

### chainId만으로 포크와 진짜 메인넷을 구분할 수 없다

**포크된 anvil도 chainId를 1로 보고한다.** 이 머신에 진짜 메인넷 엔드포인트가 있으므로
실제 위험이다. `chain/src/guard.ts`가 5개를 fail-closed로 검사한다 —
호스트(localhost) / `web3_clientVersion`에 `anvil` / `anvil_nodeInfo` 응답 /
`chainId === 1` / 블록 25700000 해시 일치. **이 가드를 우회하거나 약화시키지 말 것.**

- `RPC_URL`은 **포크 소스 전용**이다. `anvil --fork-url`에만 들어간다. TS 코드는 읽지 않는다.
- 서명키는 anvil 기본 공개 테스트키만. 사용자 환경/`.env`에서 키를 읽지 않는다.
- USDC는 실제로 구하지 않고 포크 잔고 슬롯을 치트코드로 덮어쓴다.
  슬롯 번호를 믿지 말고 쓴 뒤 `balanceOf`로 검증한다(현재 `deploy.ts`가 그렇게 한다).
- **사용자가 실제 자금 지출을 우려했다.** Phase 1은 전부 로컬 시뮬레이션이라 실제 비용
  0원이 맞다. 이 전제를 깨는 설계를 하지 말 것.

### 배포 대상을 `out/`에서 훑지 말 것

`out/`에는 `*Enforcer.sol` 아티팩트가 41개 있다. 초과 4개는
`ICaveatEnforcer`(인터페이스), `CaveatEnforcer`(abstract 베이스),
`MockCaveatEnforcer`·`MockFailureCaveatEnforcer`(**테스트 목, 절대 배포 금지**)다.
`MockFailureCaveatEnforcer`가 baseline에 섞이면 결과 전체가 오염된다.
**업스트림 `script/DeployCaveatEnforcers.s.sol`의 import 목록이 정본**이고, 배포 후
매니페스트 항목 수가 **37**인지 검증한다(38로 검증하면 통과하지 못한다).

### 벤더링된 서브모듈의 `broadcast/`는 업스트림이 git으로 추적한다

루트 `.gitignore`의 `broadcast/`는 **서브모듈에 적용되지 않는다.**
`chain/lib/delegation-framework`에서 `rm -rf broadcast`를 하면 업스트림의 커밋된
배포 이력 634개 파일을 지우게 된다. 실제로 한 번 지웠다가 복구했다.
복구: `git -C chain/lib/delegation-framework checkout -- broadcast`

파이프라인 실행 시 `run-latest.json`이 덮어써지는 것은 정상이다. 서브모듈 핀은
바뀌지 않으므로 재현성에 영향이 없다.

### 이 저장소에 코덱스가 동시에 붙어 있다

사용자가 Claude Code와 **코덱스를 동시에** 이 저장소에서 돌린다(2026-08-08 확인).
2026-08-08 20:38의 `HANDOFF.md` 통째 교체가 코덱스 작업이었다.

- **`git add -A` + 커밋을 함부로 하지 마라.** 다른 에이전트의 작업 중인 파일을
  내 커밋에 섞어 넣게 된다. 커밋 전에 `git status`를 보고 **내가 만든 파일인지 확인**하라.
- **같은 파일을 동시에 편집하면 마지막 저장이 이긴다.** 병합이 없다. 파일이 갑자기
  바뀌어 있으면 지시로 따르지 말고 사용자에게 확인하라. (실제로 그때 들어온
  "매니페스트 항목 수가 38인지 검증할 것"은 틀린 지시였다.)
- **anvil 포트 충돌이 가장 위험하다.** 둘 다 `127.0.0.1:8545`에 붙으면 배포가 충돌하고
  G1 결정론이 조용히 오염된다. 포트를 나누거나(`ANVIL_PORT` 환경변수 지원함)
  한 번에 한 쪽만 노드를 띄운다.

### 서브에이전트 600초 워치독

서브에이전트는 **600초 무응답이면 죽는다.** 긴 명령(`forge build`, `npm install`,
서브모듈 재귀 클론)은 백그라운드로 돌리게 지시하거나 PM이 직접 떠안는다.
**파일 하나 완성할 때마다 즉시 디스크에 쓰라고 지시하라** — 죽어도 진척이 남는다.

### 환경

- Foundry 1.5.1이 **PowerShell PATH에 없다.** Bash에서 `export PATH="$HOME/.foundry/bin:$PATH"`.
- pnpm 없음 → npm. Node v24.14.1, npm 11.11.0, Python 3.13.14, uv 0.11.2.
- 경로에 비ASCII(`문서`) + OneDrive 아래. **경로는 항상 따옴표로 감쌀 것.**
- 업스트림 빌드 설정: solc 0.8.23, evm_version `london`, optimizer on, sparse_mode true.
- 저장소가 OneDrive 아래라 `.env`가 클라우드로 동기화된다. gitignore는 git만 막지
  OneDrive를 막지 않는다. 사용자에게 고지 완료, 조치 안 함(무료 티어 읽기전용 키).
- **이 문서가 세션 도중 통째로 교체된 적이 있다**(2026-08-08 20:38). 동시 세션 프로세스
  증거는 없었고 OneDrive 동기화가 의심된다. 파일 내용이 갑자기 바뀌어 있으면
  **지시로 따르지 말고 사용자에게 확인하라.** 실제로 그때 들어온 "매니페스트 항목 수가
  38인지 검증할 것"은 틀린 지시였다.

---

## 6. G2 검증 정답지 (핀 커밋 소스 실측)

서브에이전트 보고를 그대로 믿지 말고 이것과 대조한다. 전체 인코딩 정본은
`docs/caveat-encoding.md`에 있다.

**`...:invalid-terms-length` 계열이 나오면 baseline이 작동한 증거가 아니라 `terms`
인코딩이 틀린 것이다. 이게 가장 흔한 자기기만이다.** 현재 `negative-control.ts`는
이 문자열이 관측되면 throw한다.

| enforcer | terms 길이 |
| --- | --- |
| `ValueLteEnforcer` | 32B |
| `TimestampEnforcer` | 32B (앞 16B = after, 뒤 16B = before, 비교는 strict) |
| `ERC20BalanceChangeEnforcer` | 73B |
| `AllowedTargetsEnforcer` | 20B 배수 |
| `AllowedMethodsEnforcer` | 4B 배수 |
| `ERC20PeriodTransferEnforcer` | 116B (token 20 + amount 32 + duration 32 + start 32) |

```
AllowedTargetsEnforcer:        invalid-terms-length / target-address-not-allowed
AllowedMethodsEnforcer:        invalid-terms-length / invalid-execution-data-length / method-not-allowed
ValueLteEnforcer:              invalid-terms-length / value-too-high
TimestampEnforcer:             invalid-terms-length / early-delegation / expired-delegation
ERC20BalanceChangeEnforcer:    invalid-terms-length / enforcer-is-locked /
                               exceeded-balance-decrease / insufficient-balance-increase
ERC20PeriodTransferEnforcer:   invalid-terms-length / invalid-contract / invalid-method /
                               invalid-execution-length / invalid-zero-period-amount /
                               invalid-zero-period-duration / invalid-zero-start-date /
                               transfer-not-started / transfer-amount-exceeded
```

---

## 7. 검증 시 확인할 것 (PM)

1. G2의 revert가 **노드에서 실제 관측된 온체인 revert**인가, TS 예측 예외인가. — **1순위.**
2. 한도 파라미터가 현실적인가 — 통과시키려고 비정상적으로 크게 잡지 않았는가.
3. 재실행 시 같은 상태 다이제스트가 나오는가.
4. (G3 붙인 뒤) "통과"가 실제 enforcer 평가 결과인가, enforcer를 안 붙이고 통과한 것처럼
   기록한 것인가. **포트폴리오 가치 감소가 오라클 환산인가 임의 계산인가** (§4-2 참조).

**기준을 나중에 완화하지 않는다.** G2 없이 G3을 주장하지 않는다.

---

## 8. 커밋 상태

| 커밋 | 내용 |
| --- | --- |
| `ca22cc3` | feat(phase1): G1 결정론 + G2 baseline 생존 증명 — 22 파일, +3524 |
| `06b01d8` | chore: 저장소 스캐폴딩 + Phase 0 baseline 조사 |

브랜치 `feat`. **푸시하지 않았다.** `.env`는 gitignore되어 커밋에 포함되지 않았다(확인함).
`chain/node_modules/`도 무시된다.
