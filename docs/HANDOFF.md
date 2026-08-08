# 세션 인계 — Phase 1 (G1/G2)

작성 2026-08-08. 이전 세션 종료 시점 상태. **다음 세션은 이 문서부터 읽는다.**

## 0. 역할 (사용자 지정, 유지할 것)

- **Opus = PM 겸 검증자.** 설계 결정과 검증은 Opus가 직접 한다.
- **개발 = Sonnet 서브에이전트.** 서브에이전트가 또 서브에이전트를 띄우지 못하게 지시에 명시.
- 중요한 보고·판정·블로커는 **한글로**.

먼저 읽을 것: `README.md`, `docs/baseline-config.md`, `docs/phase1-acceptance.md`.
스펙과 합격 기준은 전부 그 안에 있다. 사용자는 따로 설명하지 않는다.

현재 범위는 **G1·G2 뿐이다.** G3/G4는 사용자가 G1·G2를 검증한 뒤에 붙인다.
G3/G4를 미리 만들지 말 것. 단, G3/G4를 불가능하게 만드는 설계도 하지 말 것.

---

## 1. 확정된 고정 파라미터 (변경 금지)

`docs/baseline-config.md` §4에 이미 기록 완료. 논문에 박히는 값이다.

| 항목 | 값 |
| --- | --- |
| delegation-framework 커밋 | `197463b4aba3409adef1df544dabafc3636ee82d` |
| 포크 체인 | Ethereum mainnet (chainId 1) |
| 포크 블록 | `25700000` |
| 포크 블록 해시 | `0x528d3ac8a0fbb982d354cbef4f842140ed0ae75cbcdf41dbd08324e298a72abf` |
| 포크 블록 타임스탬프 | `1786068491` (2026-08-07T02:08:11Z) |
| Chainlink ETH/USD | `0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419`, decimals 8, 해당 블록 값 `189811115161` ($1898.11) |
| USDC | `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48`, decimals 6 |

**커밋 핀 근거** — `main` HEAD를 쓴다. 태그 `v1.3.0`은 구체 enforcer가 32종뿐이라
`baseline-config.md` §1의 37종과 불일치한다. 이 커밋은 정확히 37종.

> **정정 (2026-08-08)** — 이전 세션이 쓴 "38종 / 33종"은 `src/enforcers/`의 **파일 개수**였다.
> 그 중 `CaveatEnforcer`는 `abstract contract` 베이스 클래스로 배포 대상이 아니다.
> 배포 가능한 구체 enforcer는 핀 커밋 **37종**, `v1.3.0` **32종**이다. 실제 배포로 검증했다.

**블록 선정 근거** — 선정 시점 최신이 25702227이라 2227블록(약 7.4시간) 뒤 = 확정 구간.
해당 블록에서 아카이브 조회·오라클·USDC 실제 확인 완료.

**테스트넷을 쓰지 않는 이유** — Sepolia Chainlink는 비현실적 가격의 테스트 피드이고
풀 잔고가 임의값이라 G3의 오라클 환산 가치 근거가 무너진다. 사용자가 테스트넷을
제안했으나 위 근거로 메인넷 포크를 채택했고, 로컬 anvil 포크라 실제 비용은 0이다.

### 활성 caveat 6종 (37종은 전부 배포, 붙이는 건 이 6종)

| enforcer | 대응하는 Agent Wallet 통제 |
| --- | --- |
| `AllowedTargetsEnforcer` | 프로토콜 허용목록 |
| `AllowedMethodsEnforcer` | 메서드 허용목록 |
| `ERC20PeriodTransferEnforcer` (period 86400) | 일일 지출 한도 + 일일 리셋 |
| `ValueLteEnforcer` | 네이티브 값 상한 |
| `ERC20BalanceChangeEnforcer` | 상태 차분 검사 |
| `TimestampEnforcer` | 위임 유효 기간 |

`ERC20BalanceChangeEnforcer`는 **의도적으로 포함**했다. `baseline-config.md` §2대로,
빼고 "baseline은 정적 속성만 본다"고 쓰면 MetaMask 심사자가 자사 코드로 즉시 반박한다.
baseline을 더 강하게 만든 상태로 뚫어야 논문이 산다.

---

## 2. 완료된 것

- **서브모듈 벤더링 완료·검증됨.** `chain/lib/delegation-framework`가 핀 커밋에 정확히
  있고, 중첩 서브모듈 9개(FCL/SCL/openzeppelin/account-abstraction 등) 전부 채워짐.
  `git submodule status --recursive`로 확인함.
- **`.gitignore` 수정 완료.** `lib/` 무시를 풀어 서브모듈 핀이 git에 기록되게 함.
  `out/`, `cache/`, `broadcast/`는 계속 무시. 이유는 주석으로 파일에 남김.
- **`.env` 생성·검증 완료.** `RPC_URL`(Alchemy 메인넷), `FORK_BLOCK=25700000`.
  `.gitignore:2`가 이미 `.env`를 무시함(`git check-ignore -v`로 확인). 추가 조치 불필요.
- **RPC 검증 완료.** chainId 1, **아카이브 접근 정상**(블록 20000000 스토리지 조회 성공),
  오라클·USDC 해당 블록 조회 성공.
- **`docs/baseline-config.md` §4 기록 완료** (enforcer 주소·한도 파라미터 2칸만 남음).
- **Step 1 빌드 증명 완료.** `forge build` exit 0, 아티팩트 258개 생성.
  `DelegationManager`·`HybridDeleGator` 및 활성 6종 전부 아티팩트 확인.
  `src/enforcers/*.sol` = 38개 파일이지만 그 중 `CaveatEnforcer`가 abstract 베이스이므로
  **배포 가능한 구체 enforcer는 37종**이다(실제 배포로 확인).
  **캐시가 데워져 있으므로 재빌드는 빠르다.**

### 배포 시 반드시 걸러야 할 것 (실측으로 발견)

`out/`에는 `*Enforcer.sol` 아티팩트가 41개 있다. 배포 대상 37종이 아니다. 초과 4개의 출처는:

| 아티팩트 | 출처 | 처리 |
| --- | --- | --- |
| `ICaveatEnforcer.sol` | `src/interfaces/` | 인터페이스, 배포 대상 아님 |
| `CaveatEnforcer.sol` | `src/enforcers/` | **abstract 베이스**, 배포 대상 아님 |
| `MockCaveatEnforcer.sol` | `test/utils/` | **테스트 목, 절대 배포 금지** |
| `MockFailureCaveatEnforcer.sol` | `test/utils/` | **테스트 목, 절대 배포 금지** |

`out/`을 훑어서 "모든 enforcer 배포"를 구현하면 목이 섞여 들어간다.
특히 `MockFailureCaveatEnforcer`가 baseline에 들어가면 결과 전체가 오염된다.
**배포 대상은 업스트림 `script/DeployCaveatEnforcers.s.sol`의 import 목록을 정본으로 쓴다.**
그 스크립트가 배포하는 개수는 **37종**이고, `src/enforcers/` 파일 목록과의 차집합은
`CaveatEnforcer`(abstract) 하나뿐임을 실측 확인했다. 배포 후 매니페스트 항목 수가
**37**인지 검증할 것. (38로 검증하면 통과하지 못한다.)

## 3. Step 2~6 완료 (2026-08-08)

**Phase 1의 G1·G2가 전부 통과했다.** 재현: `bash chain/scripts/reproduce.sh`

- **Step 2 완료** — 구체 enforcer 37종 + `DelegationManager` + delegator 스마트계정
  (`ERC1967Proxy` + `HybridDeleGator`) 배포. `chain/deployments/manifest.json`.
  `deploy.ts`가 CREATE2 주소를 기대값과 대조해 어긋나면 throw한다.
- **Step 3 완료** — viem 하니스. `terms` 인코더 6종은 핀 커밋 소스에서 추출한
  `docs/caveat-encoding.md`를 따르며, 각 인코더가 바이트 길이를 assert한다.
- **Step 4 완료** — G1. 독립 2회 실행에서 스냅샷이 바이트 단위로 일치.
  `STATE_DIGEST=0xe29efd531e1a734a1fd94a6bfd53338d0a296f0185d0e072cb9c9c1f3a26ef48`
- **Step 5 완료** — G2. `traces/negative-control.json`. 합격 기준 2건을 넘어
  **활성 caveat 6종 전부**를 개별 revert로 증명. 통과 회차(PC0) 포함 7회차.
- **Step 6 완료** — `chain/README.md`, `docs/baseline-config.md` §4 실측값으로 채움.

### 이전 세션 전제 중 실측으로 틀린 것 2개

1. **"anvil이 실제 state root를 계산하므로 stateRoot를 정본으로 쓴다"** — 틀렸다.
   anvil은 포크 모드에서 머클 상태 루트를 계산하지 않는다. 로컬 채굴 블록의 헤더
   `stateRoot`가 전부 `0x00…00`이다. 대체 다이제스트를 `chain/src/state-digest.ts`에
   구현했다(노드가 실제 계산한 `transactionsRoot`/`receiptsRoot` + 명시 열거 계정 상태).
2. **"38종"** — 파일 개수였다. 구체 enforcer는 37종. §1 위 정정 참조.

### G1을 실제로 한 번 깨뜨린 원인 (재발 주의)

`evm_setNextBlockTimestamp`를 네거티브 컨트롤 회차에만 걸고 **배포 블록 ~41개를
방치**했더니 그 블록들의 타임스탬프가 벽시계에서 들어와 실행마다 1초씩 달라졌고,
부모 해시 연쇄로 이후 모든 블록 해시가 어긋났다. `deploy.ts` 시작 시
`anvil_setBlockTimestampInterval(1)` + `evm_setNextBlockTimestamp`로 고정해서 해결했다.
**"트랜잭션 시각만 고정하면 된다"가 아니라 로컬에서 채굴되는 모든 블록이 대상이다.**

## 3-1. 다음 세션이 할 일

- **사용자의 G1·G2 검증을 먼저 받는다.** 그 뒤에 G3를 붙인다. 미리 만들지 않는다.
- G3 설계 시 참고: 회차 500 USDC × 일 4회 = 일 2,000(일일 한도 정확히 소진),
  10,000 USDC 고갈에 5일 × 4회 = **20회차**. 모든 회차가 회차·일일 한도를 동시에
  만족하면서 잔고가 0이 된다. 계산 근거는 `docs/phase1-parameters.md`.
- G3의 포트폴리오 가치 감소는 **오라클 환산**이어야 한다. 임의 계산이면 심사에서 무너진다.
  USDC만 움직이면 ETH/USD 피드가 개입할 여지가 없으므로, 자산 구성을 다시 볼 것.

---

## 4. 반드시 지킬 함정 (이미 대가를 치른 것들)

### 서브에이전트 600초 워치독
서브에이전트는 **600초 무응답이면 죽는다.** 이전 세션이 이걸로 한 번 날아갔다.
`forge build`, 서브모듈 재귀 클론 같은 긴 명령은 서브에이전트가 **백그라운드로**
돌리게 지시하거나, PM(Opus)이 직접 떠안아서 캐시를 데운 뒤 넘겨야 한다.

### chainId만으로 포크와 진짜 메인넷을 구분할 수 없다
**포크된 anvil도 chainId를 1로 보고한다.** 이 머신에는 이제 진짜 메인넷 엔드포인트가
있으므로 이건 실제 위험이다. 안전장치는 반드시 **anvil 노드 신원(`web3_clientVersion` /
`anvil_nodeInfo`) + localhost 엔드포인트**를 같이 검사해야 한다. chainId 검사만으로는
안 된다. 실패 시 경고가 아니라 **fail closed**.

- `RPC_URL`은 **포크 소스 전용**이다. `anvil --fork-url`에만 들어가고 그 외 어디에도
  안 쓴다. 어떤 스크립트도 `RPC_URL`로 트랜잭션을 보내면 안 된다. 문서가 아니라 코드로 강제.
- 서명키는 anvil 기본 공개 테스트키만 쓴다. 사용자 환경/`.env`에서 키를 읽지 않는다.
- USDC는 실제로 구하지 말고 로컬 포크 잔고 슬롯을 치트코드(`anvil_setStorageAt`/`deal`)로 덮어쓴다.
- **사용자가 실제 자금 지출을 우려했다.** Phase 1은 전부 로컬 시뮬레이션이라 온체인
  실제 비용 0원이 맞다. 이 전제를 깨는 설계를 하지 말 것.

### 결정론 위험 (G1이 여기서 깨진다)
- 블록 타임스탬프를 고정할 것. 기준점은 포크 블록 타임스탬프 `1786068491`,
  거기에 고정 오프셋을 더해 `evm_setNextBlockTimestamp`로 명시 설정. 벽시계 유입 금지.
- 블록당 트랜잭션 1건, fifo 정렬.
- 배포키 고정, CREATE2 salt 고정.
- **트레이스 JSON의 해시 대상 영역에 벽시계 시각·소요시간·절대경로·실행 ID가
  들어가면 안 된다.** 기록하려면 해시에서 제외되는 별도 섹션으로 분리.

### 환경
- Foundry 1.5.1이 **PowerShell PATH에 없다.** `C:\Users\parks\.foundry\bin`에 있으므로
  Bash에서 `export PATH="$HOME/.foundry/bin:$PATH"`.
- pnpm 없음 → npm 사용. Node v24.14.1, npm 11.11.0, Python 3.13.14, uv 0.11.2.
- 경로에 비ASCII(`문서`) 포함 + OneDrive 아래. 경로는 항상 따옴표로 감쌀 것.
- 업스트림 빌드 설정: solc 0.8.23, evm_version `london`, optimizer on, sparse_mode true.
- 저장소가 OneDrive 아래라 `.env`가 클라우드로 동기화된다. gitignore는 git만 막지
  OneDrive를 막지 않는다. 사용자에게 고지 완료, 현재는 조치 안 함(무료 티어 읽기전용 키).

---

## 5. G2 검증 정답지 (PM이 핀 커밋 소스에서 직접 추출)

서브에이전트 보고를 그대로 믿지 말고 이것과 대조한다.

- 한도 초과 → `ERC20PeriodTransferEnforcer:transfer-amount-exceeded`
- 허용목록 밖 → `AllowedTargetsEnforcer:target-address-not-allowed`

**다른 문자열이 나오면 의도와 다른 이유로 revert된 것을 네거티브 컨트롤이라고 잘못
라벨한 것이다.** 특히 `...:invalid-terms-length` 계열이 나오면 baseline이 작동한
증거가 아니라 caveat terms 인코딩이 틀린 것이다. 이게 가장 흔한 자기기만이다.

### terms 길이 (인코딩 검증용)

| enforcer | terms 길이 |
| --- | --- |
| `ValueLteEnforcer` | 32B |
| `TimestampEnforcer` | 32B |
| `ERC20BalanceChangeEnforcer` | 73B |
| `AllowedTargetsEnforcer` | 20B 배수 |
| `AllowedMethodsEnforcer` | 4B 배수 |
| `ERC20PeriodTransferEnforcer` | 116B (token 20 + periodAmount 32 + periodDuration 32 + startDate 32) |

### 6종 전체 revert 문자열 (핀 커밋 실측)

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

## 6. 검증 시 확인할 것 (Opus, `phase1-acceptance.md` §검증 시 확인할 것)

1. G2의 revert가 **노드에서 실제 관측된 온체인 revert**인가, 아니면 TS에서 위반을
   예측해서 던진 예외인가. 이게 1순위 확인 대상이다.
2. 한도 파라미터가 현실적인가 — 통과시키려고 비정상적으로 크게 잡지 않았는가.
3. 재실행 시 같은 `stateRoot`가 나오는가.
4. (G3 붙인 뒤) "통과"가 실제 enforcer 평가 결과인가, enforcer를 안 붙이고 통과한
   것처럼 기록한 것인가. 포트폴리오 가치 감소가 오라클 환산인가 임의 계산인가.

**기준을 나중에 완화하지 않는다.** `phase1-acceptance.md` 첫 줄에 못박혀 있다.
G2 없이 G3을 주장하지 않는다.

---

## 7. 커밋 상태

아무것도 커밋하지 않았다. 전부 작업 트리에만 있다. 브랜치 `feat`.

```
 M .gitignore                      # lib/ 무시 해제
 A  .gitmodules                    # delegation-framework 서브모듈
 A  chain/lib/delegation-framework # 핀 커밋 197463b4
 M docs/baseline-config.md         # §4 고정 파라미터 기록
 ?? .env                           # gitignore됨, 커밋 안 됨
 ?? docs/HANDOFF.md                # 이 문서
```

마지막 커밋은 스캐폴딩 `06b01d8` 하나뿐이다.
