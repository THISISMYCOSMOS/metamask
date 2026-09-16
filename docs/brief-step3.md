# 서브에이전트 브리프 — Step 2 스크립트화 + Step 3 하니스 + Step 5 G2

PM(Opus)이 작성. 개발은 당신이 한다. **당신은 또 다른 서브에이전트를 띄우지 않는다.**

## 0. 먼저 읽어라 (전부, 순서대로)

1. `docs/phase1-acceptance.md` — 합격 기준. 완화 금지.
2. `docs/phase1-parameters.md` — PM이 확정한 파라미터. **변경 금지.**
3. `docs/caveat-encoding.md` — 6종 `terms` 인코딩 정본. **추측 금지, 이 문서가 정답지.**
4. `docs/baseline-config.md` §4 — 고정 파라미터.

`docs/HANDOFF.md`는 **읽지도 쓰지도 말 것.** PM이 관리한다.

## 1. 이미 끝난 것 (다시 하지 마라)

- `forge build` 완료. `chain/lib/delegation-framework/out/` 아티팩트 258개. **캐시가 데워져 있다.**
- **anvil이 이미 떠 있다**: `http://127.0.0.1:8545`, 메인넷 포크 @ 25700000, chainId 1, `--order fifo`.
  **이 노드를 죽이지 마라.** 개발·디버깅은 여기에 붙어서 한다.
- PM이 수동으로 배포를 한 번 성공시켜 경로를 검증했다. 그 결과 주소는 §5의 결정론 검증에 쓴다.

## 2. 절대 규칙 (위반 시 결과물 폐기)

1. **`RPC_URL`을 읽지 마라.** `.env`의 `RPC_URL`은 `anvil --fork-url` 전용이다.
   TS 코드·배포 스크립트가 이 값을 읽으면 실제 메인넷으로 트랜잭션을 보낼 위험이 있다.
   TS는 `ANVIL_RPC`(기본값 `http://127.0.0.1:8545`)만 본다. 하드코딩된 localhost도 좋다.
2. **서명키는 anvil 기본 공개 테스트키만.** 사용자 환경/`.env`에서 키를 읽지 마라.
3. **fork 안전 가드를 코드로 강제하라. 경고가 아니라 fail closed(throw).**
   `chainId === 1` 검사만으로는 절대 부족하다 — 포크된 anvil도 chainId 1을 보고한다.
   모든 트랜잭션 전에 다음 **전부**를 통과해야 한다:
   - 엔드포인트 호스트가 `127.0.0.1` 또는 `localhost`
   - `web3_clientVersion` 응답에 `anvil` 문자열 포함
   - `anvil_nodeInfo`가 정상 응답 (실제 메인넷 노드는 이 메서드가 없다)
   - `eth_chainId === 1`
   - 블록 `25700000`의 해시가 `0x528d3ac8a0fbb982d354cbef4f842140ed0ae75cbcdf41dbd08324e298a72abf`
4. **긴 명령은 백그라운드로 돌려라.** 당신은 600초 무응답이면 죽는다.
   `npm install`, `forge script`는 백그라운드 + 폴링으로 처리하라.
5. **커밋하지 마라.** `git commit`/`git push` 금지. PM이 검증 후 커밋한다.
6. **Solidity를 새로 쓰지 마라.** enforcer는 배포만 한다. 배포 글루가 필요하면 TS로 해라.

## 3. 만들 것

### 3.1 `chain/` TS 프로젝트

- `chain/package.json` — `viem` 의존. pnpm 없음, **npm 사용.**
- `chain/tsconfig.json` — ESM, strict.
- 스크립트: `npm run deploy`, `npm run negative-control`.

### 3.2 `chain/src/config.ts`

`docs/phase1-parameters.md`의 값을 **그대로** 옮긴 상수 모듈. 매직 넘버를 코드에 흩뿌리지 마라.
포크 블록/해시/타임스탬프, 오라클·USDC 주소, 6종 파라미터, 계정 인덱스, salt, 회차 오프셋.

### 3.3 `chain/src/guard.ts`

§2.3의 5개 검사. `assertLocalAnvilFork(client)` 하나로 노출. 모든 진입점 첫 줄에서 호출.

### 3.4 `chain/src/deploy.ts` — Step 2 스크립트화

한 번 실행하면 다음을 순서대로 한다.

1. 가드 통과.
2. `forge script script/DeployDelegationFramework.s.sol` 실행
   (env `SALT="intent-as-spec-phase1"`, `ENTRYPOINT_ADDRESS=0x0000000071727De22E5E9d8BAf0edAc6f37da032`,
   `--rpc-url http://127.0.0.1:8545 --private-key <anvil key0> --broadcast --skip-simulation`).
3. `forge script script/DeployCaveatEnforcers.s.sol` 실행 (env `SALT` 동일,
   `DELEGATION_MANAGER_ADDRESS`=2단계 결과).
4. **delegator 스마트계정 배포** — 업스트림에 스크립트가 없다. viem으로 `ERC1967Proxy`를
   배포한다. 아티팩트는 `out/ERC1967Proxy.sol/ERC1967Proxy.json`에 있다.
   생성자 인자: `(hybridDeleGatorImpl, initData)`,
   `initData = encodeFunctionData(initialize(address,string[],uint256[],uint256[]), [owner, [], [], []])`.
   `owner` = anvil key #1의 주소. **정본은 `test/utils/BaseTest.t.sol`의 `deployDeleGator_Hybrid`다.**
5. 매니페스트를 `chain/deployments/manifest.json`에 쓴다. 최소 내용:
   - `pinnedCommit`: `197463b4aba3409adef1df544dabafc3636ee82d`
   - `fork`: chainId, blockNumber, blockHash
   - `salt`, `deployer`
   - `delegationManager`, `hybridDeleGatorImpl`, `multiSigDeleGatorImpl`, `delegator`(프록시)
   - `enforcers`: { 이름 → 주소 } — **정확히 37개.**
6. **검증**: 매니페스트의 enforcer 개수가 37이 아니면 throw.
   (`src/enforcers/`의 파일은 38개지만 `CaveatEnforcer`는 abstract 베이스로 배포 대상이 아니다.
   `out/`을 훑어서 목록을 만들면 `MockCaveatEnforcer`·`MockFailureCaveatEnforcer` 같은
   테스트 목이 섞인다 — 절대 그렇게 하지 마라. 업스트림 배포 스크립트의 콘솔 출력을 파싱하거나
   `broadcast/.../run-latest.json`을 정본으로 써라.)

### 3.5 `chain/src/delegation.ts`

- 6종 `terms` 인코더. `docs/caveat-encoding.md`를 그대로 구현. **packed bytes다, ABI 인코딩이 아니다.**
  각 인코더는 결과 바이트 길이를 assert하라 (20N / 4N / 32 / 32 / 73 / 116).
- **caveat 배열 순서는 `docs/caveat-encoding.md`의 고정 표를 반드시 지켜라.**
  `AllowedTargets`(0) → `AllowedMethods`(1) → `ValueLte`(2) → `Timestamp`(3) →
  `ERC20PeriodTransfer`(4) → `ERC20BalanceChange`(5).
  순서를 바꾸면 G2의 revert 문자열이 바뀌어서 네거티브 컨트롤을 잘못 라벨하게 된다.
- delegation 해시 + EIP-712 서명. domain은 `{name:"DelegationManager", version:"1", chainId:1,
  verifyingContract: delegationManager}`. **version은 `"1"`이다. `"1.3.0"`이 아니다.**
- `authority = ROOT_AUTHORITY = 0xff…ff`(32바이트).
- delegator가 컨트랙트이므로 ERC-1271로 검증된다. `HybridDeleGator`는 **서명 65바이트면
  ECDSA로 `owner()`와 비교**한다. 따라서 소유자 EOA(anvil key #1)의 평범한 secp256k1 서명이면 된다.
  P256/WebAuthn 경로는 쓰지 마라.
- `Caveat.args`는 6종 모두 `0x`.
- 실행 인코딩: `ModeCode`는 single call type + default exec type.
  `executionCallData = packed(target(20B), value(32B), callData)` — 업스트림
  `@erc7579/lib/ExecutionLib.sol`의 `decodeSingle`이 정본이다. **직접 읽고 맞춰라.**

### 3.6 자금 세팅

- delegator 프록시에 USDC `10_000_000_000` (10,000 USDC). **실제로 구하지 마라.**
  `anvil_setStorageAt`으로 USDC 잔고 슬롯을 덮어쓴다. USDC 구현의 `balances` 매핑은
  슬롯 9로 알려져 있으나 **믿지 말고 검증하라**: 쓴 다음 `balanceOf`를 `eth_call`로 읽어
  기대값과 다르면 throw. (대안: 큰 보유자를 `anvil_impersonateAccount`해서 전송.)
- delegator ETH 잔고를 `anvil_setBalance`로 정확히 10 ETH로 고정.
- 가스는 delegate EOA(anvil key #2)가 낸다.

### 3.7 `chain/src/negative-control.ts` — Step 5 (G2)

**단 하나의 delegation**(위 6종 caveat, 파라미터 고정)을 서명해서 재사용한다.
네거티브 컨트롤마다 다른 delegation을 쓰면 "baseline을 그때만 켰다"는 반박에 노출된다.

각 회차 전에 `evm_setNextBlockTimestamp`를 명시 설정한다. 기준 `1786068491`,
회차 간 `+21600`초. **벽시계 시각을 절대 쓰지 마라.**

| 회차 | 내용 | 기대 결과 |
| --- | --- | --- |
| **PC0** | USDC `transfer(counterparty, 400_000_000)` (400 USDC) | **성공.** 6종 전부 통과 |
| **NC1** | USDC `transfer(counterparty, 2_500_000_000)` (2,500 USDC) | revert `ERC20PeriodTransferEnforcer:transfer-amount-exceeded` |
| **NC2** | target을 **허용목록 밖 주소**(anvil key #4)로, calldata는 `transfer(counterparty, 100_000_000)` | revert `AllowedTargetsEnforcer:target-address-not-allowed` |
| **NC3** | USDC `transfer(counterparty, 800_000_000)` (800 USDC) | revert `ERC20BalanceChangeEnforcer:exceeded-balance-decrease` |

**PC0는 필수다.** 통과 회차가 하나도 없으면 NC들의 revert가 "baseline이 막았다"가 아니라
"하니스가 고장났다"일 수 있고, 그러면 G2가 성립하지 않는다.

NC1이 2,500인 이유: PC0가 일일 한도 2,000 중 400을 소진했으므로 available은 1,600이다.
2,500은 어느 쪽으로 계산해도 초과다.

NC3은 회차 한도(500)만 넘고 일일 한도(1,200 ≤ 2,000)는 지킨다. 따라서 `beforeHook`을 전부
통과하고 **실행 후 `afterHook`의 상태 차분 검사에서** 막힌다. 이것이 baseline의 상태 차분
계층까지 살아 있다는 증거다. (G2 합격 기준은 NC1·NC2 2건이지만 NC3은 공짜로 얻는 강한 증거다.)

#### revert는 반드시 **노드에서 관측**해야 한다 — 이게 1순위 채점 항목

TS에서 위반을 미리 계산해 예외를 던지는 것은 **완전한 실패**다. 요구사항:

1. 트랜잭션을 실제로 전송한다. viem이 가스 추정 단계에서 먼저 실패해버리므로
   **`gas`를 명시적으로 넘겨 추정을 건너뛰어라** (예: 3_000_000).
2. `eth_getTransactionReceipt`로 영수증을 받는다. `status`가 reverted(0x0)여야 한다.
3. 트레이스에 **`txHash`, `blockNumber`, `receipt.status`, `gasUsed`를 기록**한다.
   이것이 "온체인에서 실제로 실행되고 되돌려졌다"는 증거다.
4. revert 문자열은 별도로 추출한다. `debug_traceTransaction`(callTracer)의 `revertReason`,
   또는 같은 블록에서 `eth_call`로 재현. **문자열을 손으로 만들어 넣지 마라.**
5. 얻은 문자열을 `docs/caveat-encoding.md`의 정답지와 **코드로 대조**하고 불일치 시 throw.

**`...:invalid-terms-length` 계열이 나오면 baseline이 작동한 증거가 아니다.**
그것은 당신의 `terms` 인코딩이 틀렸다는 뜻이다. 그 경우 "네거티브 컨트롤 성공"이라고
보고하면 안 된다. 인코딩을 고쳐라. 이것이 이 프로젝트에서 가장 흔한 자기기만이다.

### 3.8 `traces/negative-control.json`

해시 대상 영역과 비-해시 영역을 **분리**하라.

- 해시 대상: `fork`(chainId/blockNumber/blockHash/commit), `baseline`(enforcer 주소 + 파라미터),
  `delegation`(해시, caveat 목록), `steps[]`(회차별 target/value/calldata/기대·실측 revert/
  txHash/blockNumber/status/gasUsed/사전사후 USDC·ETH 잔액).
- 비-해시 영역(`meta`): 벽시계 시각, 소요시간, 절대경로, 실행 ID. **해시 대상에 넣지 마라.**

## 4. 하지 말 것

- **G3(누적 손실 트레이스)를 만들지 마라.** 범위 밖이다. 사용자가 G1·G2를 검증한 뒤에 붙인다.
- `traces/cumulative-loss.json`을 만들지 마라.
- `docs/baseline-config.md` §4의 확정 값을 바꾸지 마라.
- 결과가 안 나온다고 파라미터를 조정하지 마라. 막히면 **막혔다고 보고하라.**

## 5. 완료 조건 (전부 충족해야 보고 가능)

1. `npm run deploy`가 매니페스트를 생성하고 enforcer 37개를 담는다.
2. 매니페스트 주소가 PM 실측값과 **정확히 일치**한다 (CREATE2 결정론 증명):
   - `DelegationManager` = `0xeA6F34E56c9bEa6d9114A30b52e040af2b594373`
   - `HybridDeleGatorImpl` = `0xd321B8751D0dE55F9D8e25117216FFF1f1923805`
   - `MultiSigDeleGatorImpl` = `0x9094408F8A7430b482Ed48da73F76AD00CF03e39`
   - `AllowedTargetsEnforcer` = `0xef2f79e2A6Cda4f31bd213b0d1877a9B93F70038`
   - `AllowedMethodsEnforcer` = `0x27aF251F5cd8AE094925aEF1722655Ea822Edbe1`
   - `ValueLteEnforcer` = `0x0B5C5BEA5Df2fA9879Fac0AA3690aE2caD9eC498`
   - `TimestampEnforcer` = `0x8AF1d7a43158697106953f7F2EfADa603984269A`
   - `ERC20PeriodTransferEnforcer` = `0x5c0FD678387dD9a4f6D7ae4a4a2798439a0AEBb0`
   - `ERC20BalanceChangeEnforcer` = `0x2Ab40067D719bc5938AA1875CB409A9DBF50022c`

   이미 배포된 노드에 다시 배포하면 CREATE2가 같은 주소를 재사용하려다 실패할 수 있다.
   그 경우 **PM에게 새 anvil이 필요하다고 보고**하라. 임의로 salt를 바꾸지 마라.
3. `npm run negative-control`이 PC0 성공 + NC1/NC2/NC3 revert를 **온체인 영수증과 함께** 남긴다.
4. 4개 회차 전부에 `txHash`와 `blockNumber`가 있다.
5. `traces/negative-control.json`이 스키마대로 생성된다.

## 6. 보고 형식 (한글)

- 각 회차의 **실측 revert 문자열 원문**을 그대로 붙여라. 요약하지 마라.
- 각 회차의 `txHash` / `blockNumber` / `receipt.status`.
- PC0가 실제로 성공했는지, 통과한 enforcer 6종을 어떻게 확인했는지.
- 막힌 것이 있으면 숨기지 말고 **막혔다고** 써라. 우회해서 "성공"으로 만들지 마라.
- 파라미터를 하나라도 바꿨다면 무엇을 왜 바꿨는지 명시하라 (원칙적으로 바꾸면 안 된다).
