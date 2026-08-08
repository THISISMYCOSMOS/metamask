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

## 구성

| 파일 | 역할 |
| --- | --- |
| `src/config.ts` | 고정 파라미터 (`docs/phase1-parameters.md`와 1:1) |
| `src/guard.ts` | fail-closed 포크 안전 가드 |
| `src/accounts.ts` | anvil 기본 테스트 계정 |
| `src/delegation.ts` | `terms` 인코더 6종, EIP-712 서명, 실행 인코딩 |
| `src/deploy.ts` | 배포 파이프라인 → `chain/deployments/manifest.json` |
| `src/negative-control.ts` | G2 → `traces/negative-control.json` |
| `src/state-digest.ts` | G1 정본 상태 다이제스트 |
| `scripts/reproduce.sh` | 위 전부를 한 명령으로 |
