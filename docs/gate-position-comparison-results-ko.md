# 애플리케이션 게이트 위치 비교 결과

## 결론

로컬 Anvil 통제 실험에서 안정적인 정상 전송은 세 경로 모두 성공했다. 처음부터 잔고 하한을 위반하는 전송은 `pre-submit`과 `pre-sign` 모두 후보 서명 호출 전에 거절했다. 현재 위치의 게이트가 끝난 뒤 후보 서명 전에 제3자 `transferFrom`으로 잔고가 바뀐 경우에는 `no-application-gate`와 `pre-submit`이 하한 미만 상태까지 실행됐고, `pre-sign`은 캡처된 컨텍스트가 낡았음을 감지해 후보 서명 호출 없이 거절했다.

이 결과는 동일한 애플리케이션 게이트를 어느 위치에서 실행하는지에 따른 차이를 보여준다. `pre-sign`의 늦은 상태 변경 결과는 새 잔고로 후보를 다시 만들어 하한을 재계산한 결과가 아니라, 동일 후보의 `STALE_CAPTURED_CONTEXT` 거절이다.

## 고정 조건

- 기반 revision: `353e7e3154c556d9854a886772fe6541e84f5fba`. 비교 harness와 토큰은 아직 이 commit에 포함되지 않은 워킹트리 파일이므로 revision만으로 실험 소스를 식별하지 않는다.
- 실행 소스 SHA-256: harness `f6e66e9bfa86c18ae1aeed7f5b5214af1ab7fdbf206cd46e3e8374485a9b7dc6`, token `57c321e14dd8f0cb9663141f00cf338dd23add15a3d0da0931d5cf405673484a`, evaluator `ada89b34621c1cf18042d9090d3c4d285ec69bf6ee0fcd4cd8023f8000c3dd33`. JSON에는 실행 시점 `sourceDirtyProvenance`도 보존한다.
- 네트워크: 외부 연결 없는 로컬 Anvil, chain ID `31337`
- 상태 격리: 토큰 배포 직후 snapshot으로 각 셀 실행 전에 `evm_revert`하고 새 snapshot ID를 발급
- 토큰: 로컬 전용 `GatePositionToken`(`GPTT`, 6 decimals). 실제 Sepolia USDC가 아니다.
- 시작 잔고/하한: `1,000,000` / `500,000` base units, 즉 `1.0` / `0.5 GPTT`
- 후보 signer: Anvil 공개 테스트 계정 #1의 실제 `LocalAccount.signTransaction`; proposal/bundle에는 개인키가 없다.
- 공통 게이트: 두 보호 경로는 같은 함수를 한 번씩 호출하며 위치만 다르다. 함수는 후보 `eth_call` simulation 뒤 실제 balance/nonce를 읽고, 컨텍스트가 같을 때만 `evaluateDirectFloor`를 호출한다. 늦은 `pre-sign` 셀은 컨텍스트 불일치로 먼저 중단되므로 floor evaluator 호출 수가 `0`이다.
- 늦은 변경: 실험이 미리 승인한 allowance를 가진 spender가 직렬로 `200,000`을 `transferFrom`하고 성공 receipt를 기다린다. owner nonce는 `0`으로 유지되고 잔고만 `1,000,000 -> 800,000`으로 바뀐다. 임의 병렬 경쟁이나 MEV를 재현한 시나리오는 아니다.
- 각 시나리오 안에서 세 경로의 policy hash, candidate hash, execution hash, 초기 balance/nonce/allowance/block, 서명 대상 transaction fields가 모두 같음을 harness가 검증한다.

## 3x3 실행 결과

| 시나리오 | 경로 | 후보 금액 | 외부 차감 | 게이트 결과 | 후보 sign 호출 | 후보 receipt | 최종 owner 잔고 | 하한 미만 |
|---|---|---:|---:|---|---:|---|---:|---|
| 정상·안정 | `no-application-gate` | 100,000 | 0 | 없음 | 1 | success | 900,000 | 아니오 |
| 정상·안정 | `pre-submit` | 100,000 | 0 | 승인 | 1 | success | 900,000 | 아니오 |
| 정상·안정 | `pre-sign` | 100,000 | 0 | 승인 | 1 | success | 900,000 | 아니오 |
| 최초부터 하한 위반 | `no-application-gate` | 600,000 | 0 | 없음 | 1 | success | 400,000 | 예 |
| 최초부터 하한 위반 | `pre-submit` | 600,000 | 0 | `ASSET_BALANCE_FLOOR_VIOLATION` | 0 | 없음 | 1,000,000 | 아니오 |
| 최초부터 하한 위반 | `pre-sign` | 600,000 | 0 | `ASSET_BALANCE_FLOOR_VIOLATION` | 0 | 없음 | 1,000,000 | 아니오 |
| 게이트 뒤 상태 변경 | `no-application-gate` | 400,000 | 200,000 | 없음 | 1 | success | 400,000 | 예 |
| 게이트 뒤 상태 변경 | `pre-submit` | 400,000 | 200,000 | 변경 전 승인 | 1 | success | 400,000 | 예 |
| 게이트 뒤 상태 변경 | `pre-sign` | 400,000 | 200,000 | `STALE_CAPTURED_CONTEXT` | 0 | 없음 | 800,000 | 아니오 |

`success`는 실제 raw transaction을 로컬 노드에 보낸 뒤 받은 성공 receipt다. 하한 미만 여부는 별도 사후 잔고 판정이다. 따라서 control의 성공 receipt는 보호 성공을 뜻하지 않는다. 늦은 변경 시 세 경로의 외부 차감 transaction hash는 모두 `0x15e5c4678518c39484bc9ff2486e2cc685b18f040de476354df86b63c1c05863`이며 receipt가 성공했다. `pre-submit`에서는 전체 게이트가 끝난 다음 이 receipt가 확정되고 후보가 서명됐다. `pre-sign`에서는 외부 차감 receipt 뒤 simulation과 컨텍스트 검사가 실행됐고 후보 signer 호출은 0회였다.

성공 셀은 signed raw transaction, 그 keccak256 hash, 브로드캐스트 hash, receipt, 노드에서 다시 읽은 chainId/from/to/input/value/gas/nonce/type/maxFeePerGas/maxPriorityFeePerGas, ABI로 디코딩한 `Transfer` 로그, 사후 토큰 잔고를 JSON에 보존한다. harness는 이 transaction 필드들이 서명 대상과 같고, `Transfer` 로그의 contract/from/to/value/transaction hash가 예정된 이동과 정확히 일치하며 로그가 하나뿐임을 단언한다. 외부 차감도 같은 방식으로 receipt와 `Transfer(owner, externalRecipient, 200000)`를 결합한다. 거절 셀은 signed raw transaction, hash, receipt가 모두 `null`이고 후보 signer 호출 수가 0이다.

## 해시와 온체인 이벤트 증거

동일 snapshot에서 동일 서명 입력을 사용했기 때문에 같은 동작은 세 경로에서 같은 transaction hash를 재현했다.

| 동작 | 확인 경로 | transaction hash | 디코딩한 `Transfer` |
|---|---|---|---|
| 정상 후보 100,000 | 세 경로 전부 | `0xddc2c23249ff533d6137bd8312557bf9928ac27bb9bac4364565f013d66e5674` | owner → recipient, `100000` |
| 하한 위반 후보 600,000 | `no-application-gate`만 | `0xd6dec4457087fae143e035fab993887ec2cc22e9d05fb65597f7613915b15047` | owner → recipient, `600000` |
| 늦은 외부 차감 200,000 | 세 경로 전부 | `0x15e5c4678518c39484bc9ff2486e2cc685b18f040de476354df86b63c1c05863` | owner → externalRecipient, `200000` |
| 늦은 변경 뒤 후보 400,000 | `no-application-gate`, `pre-submit` | `0xedd4497112479d2f441e640e9a205f56115f4ef83eccd00e5bb82b50195c0e85` | owner → recipient, `400000` |

나머지 세 거절 셀에는 후보 transaction hash, receipt, `Transfer` 로그가 없다. 전체 block hash, log index, gas used와 주소는 JSON 원본에 있다.

## 현재 위치에서의 해석 한계

이 통제 실험의 상태 변경 지점은 `pre-submit` 평가 뒤이자 `pre-sign` 평가 전으로 고정했다. 따라서 현재의 이른 `pre-submit` 위치가 이 변경을 놓치고, 더 늦은 재검사가 추가 효과를 낸다는 것은 입증한다. 그러나 로컬 `pre-sign` 평가가 끝난 뒤 실제 원격 지갑의 서명·제출·채굴 사이에 다시 상태가 바뀌는 경우까지 원자적으로 막는다는 증거는 아니다. 그런 보장은 지갑 내부 집행이나 온체인 조건과 별도 검증이 필요하다.

## 재현

저장소의 `chain` 디렉터리에서 다음을 한 번 실행한다.

```powershell
npm run research:gate-position
```

결과 원본은 [`traces/gate-position-comparison.json`](../traces/gate-position-comparison.json)이다. 실행 harness는 [`chain/src/gate-position-comparison.ts`](../chain/src/gate-position-comparison.ts), 로컬 토큰은 [`chain/src/GatePositionToken.sol`](../chain/src/GatePositionToken.sol)이다. 이번 확정 artifact의 SHA-256은 harness `f6e66e9bfa86c18ae1aeed7f5b5214af1ab7fdbf206cd46e3e8374485a9b7dc6`, token `57c321e14dd8f0cb9663141f00cf338dd23add15a3d0da0931d5cf405673484a`, evaluator `ada89b34621c1cf18042d9090d3c4d285ec69bf6ee0fcd4cd8023f8000c3dd33`이다. 실행 당시 기존 dirty 상태였던 `chain/lib/delegation-framework`와 `docs/rq3-ai-writing-context-ko.md`도 artifact provenance에 기록했다.

## 해석 범위

`no-application-gate`는 애플리케이션 하한 검사와 컨텍스트 재검사가 없는 로컬 서명 control이다. 기존 6개 Delegation Framework caveat, MetaMask Agent Wallet Guard, 원격 server signer를 실행한 baseline이 아니다. 이 실험은 애플리케이션 잔고 하한 게이트의 효용과 늦은 상태 변경에 대한 위치 차이, 특히 서명 직전 컨텍스트 재검사의 효과를 검증한다. 모든 Agent Wallet native 보호를 합친 체계에 비해 추가 이득이 있다고 단독으로 증명하지 않으며, 원격 Agent Wallet 통합이나 Sepolia 실행 증거도 아니다. 외부 `transferFrom` 자체를 게이트가 막았다는 의미도 아니다.
