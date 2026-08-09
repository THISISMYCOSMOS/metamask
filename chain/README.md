# chain/ — 포크 환경과 위임 실행 경로

Phase 1 범위는 **G1(결정론)과 G2(baseline 생존 증명)** 이다. G3(위반 트레이스)는
사용자가 G1·G2를 검증한 뒤에 붙인다.

## 재현

```bash
bash chain/scripts/reproduce.sh
```

이 한 명령이 anvil 포크 기동 → 배포 → G2 트레이스 생성 → 상태 다이제스트 출력까지 한다.
`.env`의 `RPC_URL`(아카이브 노드)이 필요하다.

두 번 돌려서 마지막 줄 `STATE_DIGEST`가 같으면 G1이 성립한다.

두 번을 자동으로 비교하려면:

```bash
bash chain/scripts/verify-determinism.sh
```

`reproduce.sh`를 서로 다른 포트(기본 8545/8546, `DETERMINISM_PORT_1`/`_2`로 재지정)로
두 번 돌려 `SNAPSHOT_OUT` 스냅샷과 표준출력의 `FINAL_BLOCK` / `DELEGATION_HASH` /
`PERIOD_CONSUMED` / `STATE_DIGEST`를 비교하고, `traces/determinism.json`에 두 실행의
원본 출력과 스냅샷 SHA-256을 기록한다. 스냅샷이 바이트 단위로 다르거나 네 필드 중
하나라도 어긋나면 실패(exit != 0)한다. 벽시계 시각·임시경로·비밀값은 기록하지 않는다.

| 항목 | 값 |
| --- | --- |
| 최종 블록 | `25700048` |
| `STATE_DIGEST` | `0xe29efd531e1a734a1fd94a6bfd53338d0a296f0185d0e072cb9c9c1f3a26ef48` |
| delegation 해시 | `0x9c79a1b3758c54c83757c4d724957df8333966500183b78986b7abcf7bbe7ebb` |

## 왜 블록 헤더의 `stateRoot`를 쓰지 않는가

**anvil은 포크 모드에서 머클 상태 루트를 계산하지 않는다.** 로컬에서 채굴된 블록의
헤더 `stateRoot`가 전부 `0x00…00`이다. 실측으로 확인했다. 따라서 정본 상태 해시로 쓸 수 없다.

대신 `src/state-digest.ts`가 다음 둘을 정본 JSON으로 직렬화해 keccak256한다.

1. **노드가 실제로 계산한 트라이 루트** — 로컬 채굴 블록 전체의 `transactionsRoot`,
   `receiptsRoot`, `hash`, `timestamp`, `gasUsed`. 손으로 고른 값이 아니라 실행 결과
   전체를 담는 머클 루트다.
2. **명시적으로 열거한 계정 상태** — 주소순 정렬된 nonce/balance/codeHash, 관련 주체의
   USDC 잔고, 그리고 `ERC20PeriodTransferEnforcer`에 누적된 기간 사용량.

(2)의 마지막 항목이 중요하다. 일일 한도에 얼마가 소진됐는지가 누적 손실 논거의 핵심
상태이므로 재실행 시 이 값까지 같아야 결정론이 성립한다. 실측 `400000000`(=400 USDC,
PC0 한 건분)이며, 네거티브 컨트롤들이 상태를 남기지 않고 되돌아갔음을 함께 증명한다.

## 결정론을 지키는 장치

- **블록 타임스탬프에 벽시계가 새어 들어오지 않게 한다.** `deploy.ts`가 시작할 때
  `anvil_setBlockTimestampInterval(1)` + `evm_setNextBlockTimestamp`로 배포 블록 시각을
  못박고, `negative-control.ts`는 회차마다 시각을 명시 설정한다.
  **이걸 빼면 G1이 깨진다** — 배포 블록 시각이 실제 경과 시간에 따라 1초씩 달라지고
  부모 해시 연쇄로 이후 모든 블록 해시가 어긋난다. 실제로 한 번 깨뜨려서 확인했다.
- 블록당 트랜잭션 1건, `--order fifo`.
- 배포키 고정(anvil key #0), CREATE2 salt 고정 → 주소 결정론.
  `deploy.ts`가 배포 결과를 PM 실측 주소와 대조해 어긋나면 throw한다.
- 트레이스 JSON은 해시 대상(`hashed`)과 비해시 영역(`meta`)을 분리한다.
  벽시계 시각·절대경로는 `meta`에만 들어간다.

## 배포 전 프레임워크 핀/워크트리 검사 (fail closed)

`src/deploy.ts`의 `assertFrameworkPinnedAndClean()`이 어떤 배포 트랜잭션보다 먼저,
로컬 anvil 연결보다도 먼저 실행된다. `git`(`execFileSync`)으로
`chain/lib/delegation-framework`의 HEAD가 `PINNED_COMMIT`과 정확히 같은지,
그리고 `broadcast/` · `out/` · `cache/` 밖에 워크트리 변경이 없는지 검사하고,
하나라도 어긋나면 조치 방법을 담은 에러와 함께 throw한다. `broadcast/`는 업스트림이
git으로 추적해 파이프라인 실행마다 갱신되므로 항상 허용한다.
가드 통과 직후 `forge build --force`로 핀 소스에서 `out/`과 `cache/`를 다시 만든 뒤
배포하므로, 기존 생성물의 바이트코드를 그대로 신뢰하지 않는다.

## delegation 해시 — 정본 헬퍼 하나로 통일

`src/delegation.ts`의 `hashDelegationStruct()`가 EIP-712 `hashStruct(Delegation)`을
계산하는 유일한 정본 구현이다. `negative-control.ts`가 서명한 delegation의 해시를
이 함수로 계산해 `traces/negative-control.json`의 `hashed.delegation.delegationHash`에
기록하고, `state-digest.ts`가 같은 함수로 재계산해 기록값과 대조한다. 어긋나면 G1이
fail closed로 실패한다 — 두 스크립트가 각자 해시를 다시 구현하다 조용히 어긋나는
경우를 막기 위함이다.

## 안전장치 (fail closed)

`src/guard.ts`가 모든 진입점 첫 줄에서 다음 5개를 검사하고, 하나라도 실패하면 throw한다.

1. RPC 호스트가 `127.0.0.1`/`localhost`
2. `web3_clientVersion`에 `anvil` 포함
3. `anvil_nodeInfo` 정상 응답 (실제 메인넷 노드에는 없는 메서드)
4. `eth_chainId === 1`
5. 블록 `25700000`의 해시가 고정 해시와 일치

**`chainId === 1` 검사만으로는 부족하다.** 포크된 anvil도 chainId 1을 보고하므로,
이 머신에 실제 메인넷 엔드포인트가 있는 상황에서는 chainId만으로 둘을 구분할 수 없다.

`.env`의 `RPC_URL`은 **포크 소스 전용**이다. `anvil --fork-url`에만 들어가고
TS 코드는 이 값을 읽지 않는다. 서명키는 anvil 기본 공개 테스트키만 쓴다.
USDC는 실제로 구하지 않고 포크 잔고 슬롯을 치트코드로 덮어쓴다
(슬롯 번호를 믿지 않고 쓴 뒤 `balanceOf`로 검증한다).

**Phase 1은 전부 로컬 시뮬레이션이므로 온체인 실제 비용은 0원이다.**

## 벤더링된 서브모듈을 청소하지 말 것

**업스트림 `delegation-framework`는 `broadcast/`를 git으로 추적한다** (배포 이력 634개 파일).
루트 `.gitignore`의 `broadcast/` 패턴은 서브모듈에 적용되지 않는다 — gitignore는 이미
추적 중인 파일에 영향을 주지 않고, 서브모듈은 별도 저장소다.

`chain/lib/delegation-framework`에서 `rm -rf broadcast`를 하면 업스트림의 커밋된 이력을
지우게 된다. 실제로 한 번 지웠다가 복구했다.

```bash
# 실수로 지웠거나 run-latest.json이 수정됐을 때
git -C chain/lib/delegation-framework checkout -- broadcast
```

파이프라인을 돌리면 `broadcast/.../1/run-latest.json`(추적 파일)이 매번 덮어써지고
타임스탬프가 붙은 새 파일이 미추적으로 쌓인다. **정상이다.** 서브모듈 핀 자체는
바뀌지 않으므로 재현성에 영향이 없다. 신경 쓰이면 위 명령으로 되돌린다.

## 배포 대상은 구체 enforcer 37종

`src/enforcers/`의 `.sol` 파일은 38개지만 `CaveatEnforcer`는 `abstract contract`
베이스 클래스로 배포 대상이 아니다. 업스트림 `script/DeployCaveatEnforcers.s.sol`이
배포하는 개수도 정확히 37종이다.

`out/`을 훑어서 배포 목록을 만들면 `MockCaveatEnforcer`·`MockFailureCaveatEnforcer`
같은 테스트 목이 섞인다. **업스트림 배포 스크립트의 콘솔 출력을 정본으로 쓴다.**
`deploy.ts`는 개수가 37이 아니면 throw한다.

## G2 — baseline이 살아 있다

`traces/negative-control.json`. 합격 기준은 2건이지만 **활성 caveat 6종 전부를 개별로**
증명한다. 같은 코드 경로라 비용이 없고, "baseline을 약하게 잡았다"는 반박을 정면으로 막는다.

| 회차 | 의도 | 관측된 revert |
| --- | --- | --- |
| PC0 | 400 USDC — 회차·일일 한도 모두 만족 | (성공) |
| NC1 | 일일 한도 초과 2,500 USDC | `ERC20PeriodTransferEnforcer:transfer-amount-exceeded` |
| NC2 | 허용목록 밖 대상 | `AllowedTargetsEnforcer:target-address-not-allowed` |
| NC3 | 회차 한도만 초과 800 USDC | `ERC20BalanceChangeEnforcer:exceeded-balance-decrease` |
| NC4 | 허용목록 밖 메서드 `approve` | `AllowedMethodsEnforcer:method-not-allowed` |
| NC5 | 네이티브 값 1 wei (상한 0) | `ValueLteEnforcer:value-too-high` |
| NC6 | 위임 유효기간 경과 | `TimestampEnforcer:expired-delegation` |

### PC0가 필수인 이유

통과 회차가 하나도 없으면 NC들의 revert가 "baseline이 막았다"인지 "하니스가 고장났다"인지
구분할 수 없다. PC0는 성공 영수증에 USDC `Transfer`와 `ERC20PeriodTransferEnforcer`의
`TransferredInPeriod` 이벤트를 함께 남긴다 — 후자가 **enforcer가 실제로 실행됐다는 온체인 증거**다.

### revert는 노드에서 관측한 것이다

TS에서 위반을 예측해 던진 예외가 아니다. 각 회차가 `txHash`·`blockNumber`·
`receiptStatus`·`gasUsed`를 남기며, revert 문자열은 `debug_traceTransaction`으로 노드에서 뽑는다.
viem의 가스 추정을 건너뛰려고 `gas`를 명시 지정한다 — 추정 단계에서 먼저 실패하면
revert가 온체인에 기록되지 않아 증거가 되지 않는다.

가스 소모량이 이를 교차 검증한다. NC2(113,793) < NC1(144,336) < NC3(226,731)로,
caveat 배열에서 앞 index에서 막힌 회차일수록 가스가 적고, NC3은 실행까지 마친 뒤
`afterHook`에서 막혀 가장 크다. 예측 예외였다면 이런 차이가 나올 수 없다.

### caveat 배열 순서가 revert 문자열을 결정한다

`DelegationManager`의 `beforeHook` 루프는 caveat 배열을 **오름차순**으로 돈다
(`afterHook`/`afterAllHook`은 역순). 따라서 `AllowedTargetsEnforcer`가
`ERC20PeriodTransferEnforcer`보다 **앞 index**에 있어야 한다. 뒤에 두면 허용목록 밖
호출이 `target-address-not-allowed`가 아니라 `ERC20PeriodTransferEnforcer:invalid-contract`로
터져서, 의도와 다른 이유의 revert를 네거티브 컨트롤이라고 잘못 라벨하게 된다.

고정 순서와 각 `terms` 인코딩은 `docs/caveat-encoding.md`에 있다.

> `…:invalid-terms-length` 계열이 나오면 baseline이 작동한 증거가 **아니다.**
> `terms` 인코딩이 틀렸다는 뜻이다. `negative-control.ts`는 이 문자열이 관측되면 throw한다.

## G3 — 누적 손실 트레이스

`traces/cumulative-loss.json`. G2와 완전히 같은 caveat 6종(순서·파라미터·바이트 인코딩
동일)으로 **단 하나의 root delegation**을 서명해서 재사용하고, 그 delegation으로 500 USDC
전송을 **20회 연속** 성공시켜 delegator의 USDC를 10,000 → 0으로 고갈시킨다.

### 무엇을 증명하는가

- 20회차 **전부 성공**(revert 0건) — G3의 정의가 "baseline을 전부 통과하면서 손실이 난다"
  이므로 revert가 1건이라도 나오면 실패로 처리한다(fail closed).
- 서명된 caveat 6종이 매 회차 **실제로 평가돼 통과**했다 — enforcer를 안 붙이고 통과한
  것처럼 기록하는 것이 심사 1순위 공격 지점이므로, 서명·인코딩에 쓴 것과 같은 caveat 배열
  객체에서 파생한 기록만 남긴다.
- 포크 블록 오라클(ETH/USD, USDC/USD)로 환산한 포트폴리오 가치가 감소했음을 **정수 연산**
  으로 보인다(부동소수 미사용).

### 회차별 온체인 증거

각 회차가 다음을 노드에서 관측해 기록한다.

1. `receipt.status === "success"`
2. delegator USDC가 **정확히** 500 USDC 감소 (`usdcBefore - usdcAfter === 500_000_000n`)
3. delegator ETH는 **불변** (`ethAfter === ethBefore`) — delegate가 가스를 내므로 delegator의
   네이티브 잔고는 회차와 무관하게 그대로다. 이것이 "ETH 10개는 그대로, USDC만 고갈된다"는
   포트폴리오 주장의 온체인 전제다.
4. USDC `Transfer(delegator, counterparty, 500e6)` 이벤트 로그
5. `ERC20PeriodTransferEnforcer`가 낸 이벤트 로그 1건 이상 — enforcer가 실제로 실행됐다는 증거
6. 회차 직후 온체인 `periodicAllowances(delegationManager, delegationHash)` 조회 —
   `transferredInCurrentPeriod <= 2,000,000,000`(일일 한도) 검사

### period 분포 — 3, 4, 4, 4, 4, 1 (정직하게 기록)

회차 n(1..20)의 블록 타임스탬프는 `1786068491 + 21600n`이다. `TimestampEnforcer`가 strict
`>` 비교라 n=0은 `early-delegation`이 나므로 n=1부터 시작한다. period index(0-based) =
`floor(21600n / 86400)`이며, 그 결과 6개의 일일 한도 창에 **3, 4, 4, 4, 4, 1**회씩
분포한다(오프셋이 기준 시각에서 시작하지 않아 첫 창과 마지막 창이 온전히 4회씩 차지
않는다). 각 창의 합계(1500 / 2000 / 2000 / 2000 / 2000 / 500 USDC)는 전부 일일 한도
2,000 USDC 이하다. 이 분포를 4,4,4,4,4로 맞추려고 `BASE_TIMESTAMP` / `STEP_TIMESTAMP_OFFSET`
/ `erc20Period.startDate` / 회차 수를 옮기지 않았다 — 자세한 근거는
`docs/phase1-parameters.md` "G3 회차 파라미터" 참조.

### 오라클 검증과 정수 산술

오라클은 회차 실행 **전에** `blockNumber: 25700000`(포크 블록)으로 명시 조회하고 다음을
fail-closed 검증한다: `decimals()===8`, `answer>0`, `updatedAt<=포크ts`,
`answeredInRound>=roundId`, 신선도(`포크ts - updatedAt <= 86400초`), 그리고 `answer`/
`updatedAt`이 `docs/baseline-config.md` "G3 오라클 핀" 표의 값과 정확히 일치. 신선도 상한
86400초는 G3 구현 시 코드와 검증기에 고정했지만, 피드 age 관측 전에 선택했다는 기록은
없으므로 사전선정으로 주장하지 않는다.

포트폴리오 가치는 1e-18 USD 단위 정수로 계산한다.

```
value1e18 = amount * answer * 10^18 / (10^tokenDecimals * 10^8)
```

나눗셈 전에 나머지가 0인지 검사하고, 0이 아니면 throw한다(무성 절삭으로 손실 수치를
왜곡시키지 않기 위함). `loss = startTotal - endTotal`, `lossBps = loss * 10000 / startTotal`
(BigInt 내림 나눗셈 — 항상 내림된다). 판정 기준은 `loss > 0 && endUsdc === 0`이 전부다 —
사후에 만든 임계값(`lossThreshold` 등)은 쓰지 않는다.

USDC/USD가 1.0이 아니므로(포크 블록 실측 `0.99976752`) 오라클 환산 시작 가치는
`docs/phase1-parameters.md`의 "$10,000.00 / 합계 $28,981.11" 표와 다르다. 그 표는 명목
페그 근사다 — 오라클 환산 정정 노트도 같은 문서에 있다.

### 재현

```bash
bash chain/scripts/reproduce-g3.sh        # G3 단독 — 새 anvil(기본 포트 8547)에 배포+G3+검증
bash chain/scripts/reproduce-phase1.sh    # G2 + G3, 각자 독립된 fresh fork/포트
bash chain/scripts/verify-g3-determinism.sh   # reproduce-g3.sh를 포트 분리해 2회 실행 후 비교
```

`reproduce-g3.sh`는 자기 anvil을 새로 띄우고 `negative-control.ts`를 실행하지 않는다 —
G2(포트 8545/8546)의 PC0 상태(400 USDC 이체, period 소진량 등)가 G3(포트 8547)로 새어
들어가지 않는다. 두 트레이스는 완전히 독립된 fresh fork에서 만들어진다.

`reproduce-g3.sh`는 `verifier/validate_trace.py`(Pydantic 스키마 + Python 교차 검증)로
`traces/cumulative-loss.json`을 검증하는 단계까지 포함한다. `uv`가 없으면 명확한 에러와
함께 실패한다(조용히 건너뛰지 않는다).
실패 진단은 `traces/cumulative-loss.failed.json`에 따로 기록해 마지막 성공 정본을
덮어쓰지 않는다.

npm script alias는 없다 — `chain/package.json`은 이 작업의 편집 금지 파일이라 위 명령을
Git Bash에서 직접 실행한다.

## 구성

| 파일 | 역할 |
| --- | --- |
| `src/config.ts` | 고정 파라미터 (`docs/phase1-parameters.md`와 1:1) |
| `src/guard.ts` | fail-closed 포크 안전 가드 |
| `src/accounts.ts` | anvil 기본 테스트 계정 |
| `src/delegation.ts` | `terms` 인코더 6종, EIP-712 서명, 실행 인코딩 |
| `src/deploy.ts` | 배포 파이프라인 → `chain/deployments/manifest.json` |
| `src/negative-control.ts` | G2 → `traces/negative-control.json` |
| `src/state-digest.ts` | G1 정본 상태 다이제스트 (delegation 해시 재계산·대조 포함) |
| `src/determinism-report.ts` | G1 2회 실행 비교 → `traces/determinism.json` |
| `src/cumulative-loss.ts` | G3 → `traces/cumulative-loss.json` |
| `src/g3-determinism-report.ts` | G3 2회 실행 비교 → `traces/g3-determinism.json` |
| `scripts/reproduce.sh` | 배포+G2+상태 다이제스트를 한 명령으로 |
| `scripts/verify-determinism.sh` | `reproduce.sh`를 포트 분리해 2회 실행 후 비교 |
| `scripts/reproduce-g3.sh` | 새 fork에 배포+G3+`verifier/` 검증을 한 명령으로 |
| `scripts/verify-g3-determinism.sh` | `reproduce-g3.sh`를 포트 분리해 2회 실행 후 비교 |
| `scripts/reproduce-phase1.sh` | G2+G3를 각자 독립된 fresh fork/포트에서 순차 재현 |
| `../verifier/models.py` | G3 트레이스 Pydantic 정본 모델 (TS 출력과 1:1 규약) |
| `../verifier/validate_trace.py` | G3 트레이스 스키마 검증 + Python 독립 교차 검증 CLI |
