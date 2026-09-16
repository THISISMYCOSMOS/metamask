# RQ3 작성용 AI 정본 — 실행 직전 결과 상태 검증과 테스트

이 문서는 `metamask` 저장소의 RQ3 글을 다른 AI가 작성할 때 사용하는 상세 컨텍스트다.
프로젝트 전체 소개문이 아니며, RQ1의 기존 보호 체계 분석이나 RQ2의 LLM 컴파일러 성능 평가를
대신하지 않는다. RQ3의 구현, 실험, 증거, 주장 범위만 다룬다.

## 0. AI 판독 지침

다음 규칙을 지켜야 한다.

1. RQ3를 두 실험 경로로 분리한다.
   - RQ3-A: 고정 메인넷 포크에서 만든 누적 손실 후보와 대응 정상 후보의 오프라인 결정론 평가
   - RQ3-B: Ethereum Sepolia에서 `assetBalanceFloor`를 적용한 MetaMask Agent Wallet direct 실행
2. RQ3-A와 RQ3-B를 하나의 통합 온체인 실험처럼 합치지 않는다.
3. 로컬 포크의 signed delegation 성공과 Sepolia direct 전송 성공을 합쳐서
   `Agent Wallet signed delegation end-to-end 성공`이라고 쓰지 않는다.
4. `accepted`는 정책 판정을 통과해 브로드캐스트 자격을 얻었다는 뜻이다. 거래 성공은 별도의
   `broadcastAttempted`, transaction hash, 영수증, 이벤트, 사후 상태로 판정한다.
5. `assetBalanceFloor`는 애플리케이션 게이트가 집행했다. MetaMask Agent Wallet 또는
   Delegation Framework의 네이티브 정책이라고 쓰지 않는다.
6. `마지막 보호장치`는 이 연구가 통합한 실행 경로 안에서 브로드캐스트 직전에 동작한다는 뜻이다.
   지갑 밖의 모든 전송 경로를 전역적으로 통제한다는 뜻이 아니다.
7. 사용자의 심리적·숨은 의도를 알아냈다고 쓰지 않는다. 사용자가 검토하고 승인한 정책을
   의도의 조작적 표현으로 사용했다고 쓴다.
8. 과거 주 성공 사례였던 `40 → 39 USDC, 하한 20 USDC` 거래는 보조 역사 자료다.
   현재 주 성공 증거는 `1.0 → 0.9 USDC, 하한 0.5 USDC` 거래다.
9. `기존 보호를 모두 통과했다`, `전 계층을 통과했다`고 쓰지 않는다. 정확한 표현은
   `고정한 Delegation Framework 커밋에서 실험에 구성한 여섯 Caveat를 통과했다`이다.
10. 이 문서의 상태·수치가 다른 과거 문서와 충돌하면 현재 코드, 재실행 가능한 아티팩트,
    `docs/research-writing-source-of-truth-ko.md`를 우선한다.

## 1. 기준 스냅샷과 증거 등급

- 작성 기준일: 2026-09-04
- 저장소: `metamask`
- 기준 브랜치: `feat`
- 현재 문서 HEAD: `353e7e3154c556d9854a886772fe6541e84f5fba`
- 조건부 서명 위임 검증 코드: `9f72005`
- 깨끗한 G3 재현 대상 코드: `d06e942`
- Delegation Framework 고정 커밋:
  `197463b4aba3409adef1df544dabafc3636ee82d`
- 고정 메인넷 포크 블록: `25700000`

증거는 다음 등급을 구분한다.

| 등급 | 의미 |
| --- | --- |
| 구현됨 | 현재 코드에 해당 경로가 존재한다. |
| 자동화 테스트됨 | 테스트가 지정된 입력에서 기대한 판정·전송 호출 횟수·스키마를 확인했다. |
| 로컬 포크에서 실행됨 | 고정 블록의 Anvil 메인넷 포크에서 실제 컨트랙트 호출과 영수증을 관측했다. |
| 공개 테스트넷에서 실행됨 | 공개 Sepolia 거래와 영수증을 확인할 수 있다. |
| 번들에 보존됨 | 당시 관측한 오프체인 값과 실행 결과가 해시 결합 evidence bundle에 있다. |
| 추론·구성됨 | 직접 실행 결과가 아니라 고정 사실을 이용해 만든 반사실적 대조군이다. |
| 미검증 | 코드 또는 계획은 있으나 해당 환경의 end-to-end 실행 증거가 없다. |

`테스트됨`, `온체인 검증됨`, `증명됨`을 서로 바꿔 쓰지 않는다.

### 1.1 과거 `8c8591a` 문서에서 반드시 바꿀 내용

| 과거 문서의 상태·표현 | 현재 정정 |
| --- | --- |
| 기준 커밋 `8c8591a` | 현재 문서 HEAD는 `353e7e3`; 조건부 검증 코드는 `9f72005`, 깨끗한 G3 재현 코드는 `d06e942`다. |
| Sepolia 주 사례가 `40→39 USDC`, 하한 20 USDC | 이는 보조 역사 자료다. 주 사례는 `1.0→0.9 USDC`, 하한 0.5 USDC, tx `0xaf7566…f500`이다. |
| 라이브 결과가 사용자 보고이고 저장소에 hash가 없음 | 현재 approval, candidate, runtime, Agent Wallet request, public-chain verification이 evidence bundle에 커밋돼 있다. |
| 라이브 허용 사례 1건만 있음 | 같은 승인 정책에서 하한 위반 후보를 거부하고 새 Agent Wallet request와 tx hash가 없음을 기록한 대응 reject bundle이 있다. |
| signed delegation은 코드만 있고 실행되지 않음 | 고정 메인넷 포크에서 실제 EIP-712 서명과 `redeemDelegations` 20/20은 성공했다. 단, Agent Wallet 공개 원격 E2E는 여전히 미검증이다. |
| 5종 불변식 구상 | 현재 오프라인 구현 범위는 4종이고 라이브 경로는 `assetBalanceFloor` 1종이다. RQ3-A 결과에는 그중 `portfolioValueFloor`와 `cumulativeLossCap` 두 정책을 사용한다. |
| 60개 Gemini 정량 결과가 없음 | 현재 58개 strict output과 2개 fail-closed 결과가 있지만 RQ2 자료이므로 RQ3 본문에는 넣지 않는다. |
| `MetaMask 확인창`으로 일반화 | 주 라이브 증거는 Agent Wallet CLI, Guard Mode, 이메일 MFA 경로다. 브라우저 MetaMask 경로와 혼동하지 않는다. |
| 모든 현재 증거가 깨끗한 최종 커밋에서 생성됨 | G3는 별도 깨끗한 `d06e942`에서 재현했다. 네 evidence bundle은 생성 당시의 dirty provenance와 이전 커밋을 보존하므로 출판용 manifest 정리가 남아 있다. |

## 2. RQ3의 정확한 질문과 분할

RQ3의 상위 질문은 다음과 같다.

> 승인된 결과 상태 불변식을 예정 거래의 시뮬레이션된 사후 상태에 적용했을 때,
> 정책 위반 후보를 브로드캐스트 전에 거부하고 정책 범위 안의 후보만 실행 경계로 보낼 수 있는가?

현재 증거는 서로 다른 두 하위 질문에 답한다.

### RQ3-A — 오프라인 누적 손실 평가

> 고정 메인넷 포크에서 생성된 G3 누적 손실 후보를 승인된 포트폴리오 결과 정책이 거부하고,
> 같은 fork·oracle·초기 상태·정책을 공유하는 대응 정상 후보를 허용할 수 있는가?

이 경로는 포트폴리오 불변식 평가의 판별 가능성을 다룬다. 실제 지갑 브로드캐스트를 수행하지 않는다.

### RQ3-B — Sepolia 라이브 `assetBalanceFloor`

> 실제 애플리케이션 실행 경로에서 예정된 ERC-20 전송의 사후 잔액이 승인된 하한을 만족하면
> 전송을 허용하고, 하한을 위반하면 Agent Wallet CLI 호출 전에 거부할 수 있는가?

이 경로는 단일 자산 잔고 하한과 실제 Agent Wallet 전송 경계의 결합을 다룬다.
현재 라이브 정책은 `assetBalanceFloor` 한 종류다.

## 3. RQ3에 필요한 프로젝트 원리

### 3.1 LLM과 런타임 판정의 분리

LLM은 자연어를 제한된 정책 스키마로 변환한다. 거래 허용 여부는 LLM이 판정하지 않는다.
사용자가 정책을 검토·수정하고 정확한 제안 해시를 승인한 뒤, 결정론적 코드가 승인된 정책과
시뮬레이션 결과를 비교한다.

RQ3에서 중요한 입력은 `승인된 정책`이다. RQ2의 모델 정확도 전체를 다시 설명할 필요는 없다.
다만 다음 사실은 실행 신뢰 경계를 설명하기 위해 필요하다.

- LLM은 체인, 지갑, 토큰, 식별자를 선택하지 못한다.
- 정책을 수정하면 새 proposal hash가 만들어지고 기존 승인은 무효가 된다.
- 승인 문구는 `APPROVE <proposalSha256>`와 정확히 일치해야 한다.
- 이 해시는 승인 대상의 무결성과 결합성을 제공하지만 사용자 이해나 법적·암호학적 신원을
  자동으로 보장하지 않는다.
- 현재 UI는 정확한 승인 문구를 표시하고 입력란에 미리 채우므로, 사용자가 해시를 직접 기억해
  입력했다고 쓰지 않는다. `표시된 정확한 승인 문구를 제출했다`고 쓴다.

### 3.2 결과 정책, 후보, 실행 요청의 결합

정책 판정은 예측된 잔액 하나만 보지 않는다. 실제로 전송될 요청이 시뮬레이션한 요청과 같은지
필드 단위로 확인한다.

- 승인 envelope hash와 후보의 `approvalSha256`
- 정책 hash와 후보의 `policySha256`
- chain ID, wallet, token
- ERC-20 `transfer(address,uint256)` calldata
- sender, recipient, amount
- nonce와 gas limit
- 시뮬레이션 전후 sender·recipient 잔액
- 예정 거래의 canonical hash

이 결합이 없으면 작은 전송을 시뮬레이션한 뒤 더 큰 calldata로 바꾸는 치환을 막을 수 없다.

### 3.3 실행 전 시뮬레이션과 상태 복원

로컬 제어 실행 경로는 예정 거래를 Anvil snapshot 안에서 실제 실행하고 영수증과 잔액 변화를
확인한다. 그 뒤 `evm_revert`로 snapshot을 복원하고 nonce와 잔액이 원상 복구됐는지 검증한다.
시뮬레이터는 외부 sender를 호출하지 않는다.

Sepolia direct 경로는 공개 RPC의 `eth_call`, `balanceOf`, nonce, gas estimate를 이용해 후보를
구성한다. 이 결과는 전송 직전의 컨텍스트 재검증과 함께 사용한다.

### 3.4 결정론적 판정

결정론적 평가기는 의도를 다시 추론하거나 불일치를 수리하지 않는다. 모든 불일치를 reason code로
누적하고, reason code가 하나라도 있으면 거부한다. 라이브 direct 경로에서 핵심 정책 조건은 다음과 같다.

```text
simulatedAfterAssetBalance >= approvedAssetBalanceFloor
```

하한보다 작으면 `ASSET_BALANCE_FLOOR_VIOLATION`이다. 등호는 통과한다.

오프라인 포트폴리오 경로에서는 현재 RQ3 실험에 다음 두 불변식을 사용한다.

- `portfolioValueFloor`: 평가 대상 포트폴리오 가치가 승인된 하한 이상이어야 한다.
- `cumulativeLossCap`: 지정 rolling window 안의 관측 최대 누적 손실이 승인된 상한 이하여야 한다.

두 평가 모두 정수 연산을 사용하며, 정책과 후보의 fork·block·hash·history coverage가 맞지 않으면
fail-closed 처리한다.

### 3.5 컨텍스트 재검증과 전송 경계

판정과 전송 사이에 상태가 바뀌는 TOCTOU 문제를 줄이기 위해 전송 직전에 chain ID, nonce,
잔액, 블록 컨텍스트를 다시 읽는다. Python 로컬 경로와 delegated 경로는 캡처한 컨텍스트의 정확한
일치를 요구한다. Sepolia direct 경로는 블록 전진 자체는 허용하지만 현재 블록 번호가 과거로 가지
않아야 하고 pending nonce와 token balance, chain, wallet, token이 그대로여야 한다. 각 경로의
조건을 만족하지 않으면 전송하지 않는다. 통과한 결정은 sender 호출 전에 소비 처리된다.

단, Python `ExecutionGate`의 일회성 보장은 한 프로세스 안에서만 유효하다. 프로세스 재시작,
다른 프로세스, 게이트를 우회한 RPC·지갑 호출은 통제하지 못한다. 따라서 전역 exactly-once 또는
wallet-native enforcement라고 쓰지 않는다.

### 3.6 사후 검증

전송 허용과 거래 성공을 분리한다. 라이브 성공은 다음을 모두 확인해야 한다.

- transaction hash 존재
- 영수증 status 성공
- 영수증의 from, to, input/calldata, value, gas, nonce가 승인 실행과 일치
- 정확한 ERC-20 `Transfer` 이벤트
- 영수증 블록의 사후 토큰 잔액이 승인 하한 이상

## 4. RQ3-A — G3 누적 손실 후보 거부

### 4.1 G3 입력이 만들어진 방식

G3는 고정 블록 `25700000`의 Ethereum mainnet Anvil fork에서 실행한 구성된 반례다.
테스트키 소유자가 EIP-712 root delegation에 서명했고, 하나의 signed delegation을 20회
`redeemDelegations`에 사용했다.

서명된 Caveat는 다음 여섯 개다.

1. `AllowedTargetsEnforcer`
2. `AllowedMethodsEnforcer`
3. `ValueLteEnforcer`
4. `TimestampEnforcer`
5. `ERC20PeriodTransferEnforcer`
6. `ERC20BalanceChangeEnforcer`

각 회차는 500 USDC를 전송한다. 20회 모두 성공해 USDC 잔액은 10,000에서 0이 됐다.
기간별 실행 횟수는 `3,4,4,4,4,1`이고 각 기간의 총 전송량은
`1500,2000,2000,2000,2000,500 USDC`다. 고정한 일일 한도 안이다.

G3 재현에서 확인된 주요 값은 다음과 같다.

| 항목 | 값 |
| --- | --- |
| 서명 형식 | EIP-712 `Delegation`, 65-byte ECDSA |
| delegation hash | `0x9c79a1b3758c54c83757c4d724957df8333966500183b78986b7abcf7bbe7ebb` |
| redemption 결과 | 20/20 성공 |
| 최종 USDC | `0` base units |
| 포트폴리오 시작값 | `28,978.7867161 USD` |
| 포트폴리오 종료값 | `18,981.1115161 USD` |
| 총 가치 감소 | `9,997.6752 USD`, `3449 bps` |
| semantic trace digest | `0x7070733f52215bd255c69fe863efa33e780f72aa7c162c6ff3f9f9574549dcf7` |

이 실험은 여섯 Caveat가 무효이거나 고장 났다는 뜻이 아니다. 각 Caveat는 설정된 조건을
집행했다. 다만 이 고정 구성에는 여러 거래 뒤의 포트폴리오 가치 하한과 별도 rolling cumulative
loss cap이 없으므로, 개별 허용 거래들의 시퀀스가 별도 결과 정책을 위반할 수 있음을 보여준다.

### 4.2 G3 후보의 결정론적 거부

`traces/cumulative-loss.json` 전체는 strict G3 공격 증거다. RQ3 평가기는 그 트레이스에서
최종 후보와 필요한 24시간 이력을 `traces/mvp-candidate-reject.json`으로 정규화한다.

승인된 평가 정책은 다음 두 기준을 포함한다.

| 불변식 | 승인 기준 |
| --- | --- |
| `portfolioValueFloor` | `26,979.2516761 USD` 이상 |
| `cumulativeLossCap` | 86,400초 rolling window 손실 `1,999.53504 USD` 이하 |

재검증 결과는 다음과 같다.

| 평가 | 관측값 | 기준 | 결과 |
| --- | --- | --- | --- |
| 포트폴리오 가치 하한 | 최소 `18,981.1115161 USD` | `26,979.2516761 USD` 이상 | 실패 |
| 24시간 누적 손실 상한 | 최대 `2,499.4188 USD` | `1,999.53504 USD` 이하 | 실패 |

최종 `accepted`는 `false`다. 오프라인 평가이므로 브로드캐스트는 시도하지 않았고 transaction
hash도 없다. 이는 지갑 또는 컨트랙트의 네이티브 차단 증거가 아니라 결정론적 후보 평가 결과다.

### 4.3 해석 범위

이 결과로 말할 수 있는 것은 다음과 같다.

> 고정된 G3 누적 손실 후보는 별도로 승인한 포트폴리오 가치 하한과 rolling 누적 손실 상한을
> 모두 위반했고, 결정론적 평가기는 이를 거부했다.

이 결과만으로 말할 수 없는 것은 다음과 같다.

- 실제 Agent Wallet이 해당 포트폴리오 정책을 집행했다.
- 모든 누적 손실 공격을 차단한다.
- 실제 공격 발생률이나 미탐률을 추정했다.
- 20회 거래가 20개의 독립 표본이다. 이것은 하나의 구성된 시퀀스다.

## 5. RQ3-A 대조군 — 대응 정상 후보 허용

거부 사례만 제시하면 모든 후보를 거부하는 평가기와 구별할 수 없다. 그래서 같은 fork, oracle,
초기 상태, 승인 정책을 공유하되 전송 횟수와 수량을 정책 범위 안으로 낮춘 대응 정상 후보를 만들었다.

대조군은 300 USDC씩 5회 전송하는 반사실적 시퀀스다. G3에서 실제로 실행된 거래가 아니며,
G3의 고정 사실을 사용해 구성·재계산한 control이다.

| 평가 | 관측값 | 기준 | 결과 |
| --- | --- | --- | --- |
| 최종/최소 포트폴리오 가치 | `27,479.1354361 USD` | `26,979.2516761 USD` 이상 | 통과 |
| 24시간 최대 누적 손실 | `1,499.65128 USD` | `1,999.53504 USD` 이하 | 통과 |

최종 `accepted`는 `true`다. 그러나 이 사례는 오프라인 구성 대조군이므로 브로드캐스트하지 않았고,
거래 성공 사례라고 쓰지 않는다.

RQ3-A의 균형 잡힌 결론은 다음과 같다.

> 동일한 승인 정책 아래에서 결정론적 평가기는 구성된 G3 누적 손실 후보를 거부하고 대응 정상
> 후보를 허용했다. 이는 제한된 고정 입력에서 평가기가 상시 거부기가 아니라 정책 경계에 따라
> 두 후보를 구분했음을 보여준다.

## 6. RQ3-B — Sepolia direct `assetBalanceFloor` 허용

### 6.1 실행 경로

이 사례는 Delegation Framework `redeemDelegations`가 아니라 MetaMask Agent Wallet 주소에서
Circle Sepolia USDC 컨트랙트로 보낸 direct ERC-20 `transfer`다.

실행 순서는 다음과 같다.

```text
한국어 자연어 입력
→ 실제 Gemini의 제한된 `assetBalanceFloor` 제안
→ 사용자 검토와 정확한 proposal hash 승인
→ 공개 RPC 시뮬레이션과 후보 생성
→ 결정론적 floor 판정
→ nonce·잔액 컨텍스트 재검증
→ Agent Wallet CLI에 exact transaction 전달
→ Guard Mode의 이메일 MFA
→ 브로드캐스트
→ 영수증·exact transaction·Transfer 이벤트·사후 잔액 검증
```

### 6.2 입력과 결과

| 항목 | 값 |
| --- | --- |
| 네트워크 | Ethereum Sepolia |
| chain ID | `11155111` |
| 토큰 | Circle Sepolia USDC, decimals 6 |
| 토큰 주소 | `0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238` |
| 송신 Agent Wallet | `0xb11539d7b6423c4523e1fba35953154b6b393df9` |
| 수신자 | `0x9f85d965258624053734d8caea00dc3f452f3c27` |
| 자연어 입력 | `이 Sepolia Agent Wallet에서 USDC를 0.5개 이상 남겨줘` |
| 승인 하한 | `500000` base units = `0.5 USDC` |
| 전송 전 잔액 | `1000000` base units = `1.0 USDC` |
| 전송 수량 | `100000` base units = `0.1 USDC` |
| 시뮬레이션 사후 잔액 | `900000` base units = `0.9 USDC` |
| 정책 판정 | 허용, `eligibleForBroadcast=true` |
| candidate hash | `0x2ef30f41600d6ac40dd4be5c44aecfe29315951eac0149c80ab719161f8786e9` |
| transaction hash | `0xaf7566c59d0b10c3983f2478088ac31df165b1acaf1b6084acacd96d08d4f500` |
| 포함 블록 | `11628030` |
| 영수증 | success |
| 실행 후 잔액 | `900000` base units = `0.9 USDC` |

공개 체인에서 현재 독립적으로 다시 확인한 범위는 성공 영수증, from/to, ERC-20 transfer calldata,
수신자, 100,000 base units, Transfer 이벤트다. 사후 잔액 900,000 base units는 실행 당시 확인돼
해시 결합 bundle에 보존됐지만, 현재 사용한 공개 RPC는 해당 블록의 historical state 조회를
제공하지 않아 이번 재조회에서 다시 도출하지 못했다. 따라서 `현재 공개 RPC로 사후 잔액까지
독립 재도출했다`고 쓰지 않는다.

이 사례의 정확한 결론은 다음과 같다.

> 애플리케이션 수준 `assetBalanceFloor` 게이트가 하한을 유지하는 direct ERC-20 후보를 허용했고,
> 동일한 exact transaction이 실제 MetaMask Agent Wallet Guard Mode와 이메일 MFA를 거쳐
> Sepolia에 브로드캐스트된 뒤 성공 영수증과 Transfer 이벤트가 확인됐다.

## 7. RQ3-B 대조군 — 하한 위반 후보의 무전송 거부

성공 사례와 같은 승인 정책 및 Agent Wallet을 사용했다. 성공 거래 뒤 잔액 0.9 USDC에서
0.5 USDC 전송을 계획하면 예상 사후 잔액은 0.4 USDC가 되어 승인 하한 0.5 USDC보다 작다.

| 항목 | 값 |
| --- | --- |
| 전송 전 잔액 | `900000` base units = `0.9 USDC` |
| 전송 수량 | `500000` base units = `0.5 USDC` |
| 예상 사후 잔액 | `400000` base units = `0.4 USDC` |
| 승인 하한 | `500000` base units = `0.5 USDC` |
| 판정 | 거부 |
| reason code | `ASSET_BALANCE_FLOOR_VIOLATION` |
| `--broadcast` 호출 뒤 새 Agent Wallet request ID | 없음 |
| transaction hash | `null` |
| `broadcastAttempted` | `false` |

`--broadcast` 옵션을 붙인 실행도 결정론적 게이트에서 CLI sender 호출 전에 닫혔다. 실행 전후의
Agent Wallet request ID 집합이 같았고 새 거래 hash가 생성되지 않았다.

이 결과의 정확한 결론은 다음과 같다.

> 같은 승인 정책에서 하한을 위반하는 direct ERC-20 후보는 Agent Wallet 요청 생성 전에
> `ASSET_BALANCE_FLOOR_VIOLATION`으로 거부됐으며, 무전송 상태가 request 목록과 null transaction
> hash로 기록됐다.

이것은 application-level no-send 증거다. Agent Wallet 또는 컨트랙트가 네이티브 floor 정책으로
거부했다는 증거가 아니다.

## 8. signed delegation과 RQ3의 조건부 경계

고정 메인넷 포크에서는 실제 EIP-712 signed root delegation과 6개 caveat를 포함한
`redeemDelegations` 20/20 실행이 성공했다. 또한 제품용 `delegated-floor-gate`는 커밋된 G3의
65-byte 서명, 여섯 caveat, delegation manager, delegator, delegate, outer calldata와 inner
ERC-20 transfer를 재구성해 결합 검증하는 자동화 테스트를 통과했다.

하지만 다음을 한 번의 공개 원격 실행에서 연결하지 않았다.

```text
승인된 결과 정책
→ 실제 Agent Wallet에 맞는 signed delegation
→ outer redeemDelegations transaction
→ Agent Wallet Guard Mode/MFA
→ 공개 체인 영수증
→ 사후 floor 검증
```

현재 저장소의 typed-data helper는 chain ID 1에 고정돼 있어 그대로 Sepolia 서명에 쓸 수 없다.
활성 Agent Wallet이 필요한 DeleGator 실행 토폴로지를 제공하는지도 아직 증명되지 않았다.
원격 block context를 생성과 전송 사이에 보존하는 운용 절차도 필요하다.

따라서 다음 두 문장을 함께 유지한다.

- 로컬 고정 포크에서 실제 signed delegation redemption 메커니즘을 재현했다.
- MetaMask Agent Wallet의 원격 signed-delegation end-to-end는 미검증이다.

## 9. RQ3 증거 지도

### 9.1 반드시 읽을 Markdown

다른 AI에게 GitHub Markdown을 제공할 때는 아래 순서를 사용한다.

1. `docs/rq3-ai-writing-context-ko.md`
   - RQ3 전용 작성 규칙, 두 실험 경로, 수치, 주장 경계를 담은 이 문서다.
2. `docs/research-writing-source-of-truth-ko.md`
   - 전체 연구의 최신 정본이다. RQ3 작성 시 §0, §2~4, §7~12, §15를 우선한다.
3. `docs/conditional-signed-delegation-test-results-ko.md`
   - 로컬 signed delegation, Sepolia direct 실행, 원격 signed-delegation 미검증 경계를 상세히
     설명한다.
4. `research/evidence/README.md`
   - 네 evidence bundle의 의미와 `accepted`, broadcast, receipt를 분리해서 읽는 규칙이다.
5. `README.md`
   - `현재 제어 실행 경로`, `MetaMask Agent Wallet 실행 어댑터`, `설계 원칙`만 우선 읽는다.
6. `chain/README.md`
   - G3의 생성·재현·결정론·기간 분포가 더 필요할 때 `G2`, `G3`, `재현` 절을 읽는다.

### 9.2 현재 글의 1차 자료로 쓰지 않을 Markdown

아래 파일은 틀렸다는 뜻이 아니라 과거 개발 단계 또는 다른 연구 질문을 위한 문서다.
RQ3 최신 상태를 설명하는 1차 정본으로 사용하지 않는다.

| 파일 | 이유 |
| --- | --- |
| `docs/HANDOFF.md` | 과거 Phase 1~3 인계 기록이다. 현재 제품 상태와 남은 작업의 정본이 아니다. |
| `docs/mvp-scope.md` | 2026-08-10 오프라인 MVP 경계를 기록한다. 이후 live 경로가 추가됐다. |
| `docs/phase3-acceptance.md` | 초기 결정론 평가기 수용 기준이다. 설계 역사 확인에만 선택적으로 쓴다. |
| `docs/phase1-acceptance.md` | G1~G4의 사전 기준이다. G3 실험 설계를 설명할 때만 보조로 쓴다. |
| `docs/phase1-parameters.md` | 고정 파라미터와 정정 기록이다. 세부 수치 감사가 필요할 때만 쓴다. |
| `docs/baseline-config.md` | 주로 RQ1 baseline 조사 문서다. RQ3 본문에 통째로 넣지 않는다. |
| `docs/caveat-encoding.md` | caveat byte encoding 상세다. 논문 방법론에 인코딩이 필요할 때만 쓴다. |
| `docs/brief-step3.md` | 과거 구현 작업 지시서다. 연구 결과 근거가 아니다. |
| `backend/README.md` | RQ2 컴파일러 계약이 중심이다. RQ3에는 승인 입력 설명 이상 필요하지 않다. |
| `research/README.md` | RQ2 benchmark와 evidence 생성 명령이 중심이다. RQ3에는 evidence 부분만 필요하다. |

### 9.3 Markdown만으로 부족한 1차 아티팩트

RQ3의 수치와 결론을 쓸 때는 다음 JSON을 함께 읽어야 한다.

| 아티팩트 | 역할 |
| --- | --- |
| `traces/cumulative-loss.json` | 실제 signed G3 20회 상태 변화와 caveat 실행 증거 |
| `traces/g3-determinism.json` | 과거 두 실행의 semantic digest 일치 보고서 |
| `traces/mvp-candidate-reject.json` | G3에서 정규화한 평가 후보 |
| `traces/mvp-candidate-accept.json` | 구성된 대응 정상 후보 |
| `traces/mvp-candidate-accept-source.json` | 정상 후보에서 재사용·재계산한 값의 provenance |
| `research/evidence/bundles/offline-g3-reject.bundle.json` | G3 거부의 승인·후보·결정 결합 |
| `research/evidence/bundles/offline-benign-accept.bundle.json` | 정상 후보 허용과 반사실적 성격 |
| `research/evidence/bundles/live-floor-accept.bundle.json` | Sepolia 허용·Agent Wallet·영수증 결합 |
| `research/evidence/bundles/live-floor-preflight-reject.bundle.json` | 하한 위반 거부와 무전송 감사 |

### 9.4 코드 책임 지도

| 책임 | 코드 |
| --- | --- |
| 라이브 floor 결정론 판정 | `core/evaluator.py` |
| 판정 후 drift 확인과 1회 sender 경계 | `core/gate.py` |
| 시뮬레이션→판정→전송 순서 | `core/execution_service.py` |
| Anvil snapshot 실행과 복원 | `core/rpc_simulator.py` |
| 오프라인 포트폴리오 후보 평가 | `verifier/evaluate_candidate.py`, `verifier/evaluate_invariants.py` |
| signed G3 생성 | `chain/src/cumulative-loss.ts` |
| delegated calldata 결합 | `chain/src/delegated-floor-gate.ts` |
| Agent Wallet 원격 런타임 | `chain/src/agent-wallet-runtime.ts` |
| Agent Wallet direct floor 경로 | `chain/src/agent-wallet-direct-floor.ts` |
| evidence bundle strict schema | `verifier/evidence_bundle_models.py` |

## 10. 현재 주장 원장

| 주장 | 상태 | 정확한 범위 |
| --- | --- | --- |
| G3 signed delegation 20회가 실행됐다 | 로컬 포크 실행됨 | 고정 커밋·블록·여섯 caveat 구성에서 20/20 성공 |
| G3 후보를 결과 정책이 거부했다 | 자동화 재검증됨 | 오프라인 포트폴리오 평가, 두 불변식 모두 실패 |
| 정상 후보를 결과 정책이 허용했다 | 자동화 재검증됨 | 구성된 반사실 대조군, 미브로드캐스트 |
| Sepolia floor 유지 후보가 성공했다 | 공개 테스트넷 실행됨 | direct ERC-20, application-level gate, Agent Wallet MFA/전송 |
| Sepolia floor 위반 후보가 거부됐다 | 번들·무전송 감사됨 | CLI sender 전 거부, 새 request ID 없음, tx hash 없음 |
| delegated 제품 gate가 G3 구조와 결합된다 | 자동화 테스트됨 | 실제 G3 fixture 재인코딩·필드 결합, 원격 전송 아님 |
| Agent Wallet signed delegation E2E | 미검증 | 공개 체인 한 실행으로 승인부터 영수증까지 연결하지 않음 |
| wallet-native `assetBalanceFloor` | 구현 안 됨 | 현재는 애플리케이션 수준 companion gate |

## 11. 글에서 사용 가능한 표현과 금지 표현

### 사용 가능한 표현

- `제한된 고정 실험에서 결과 상태 정책이 G3 후보를 거부하고 대응 정상 후보를 허용했다.`
- `Sepolia direct 경로에서 하한 위반 후보는 Agent Wallet 요청 생성 전에 거부됐다.`
- `하한을 유지하는 후보는 Agent Wallet Guard Mode와 MFA를 거쳐 브로드캐스트됐고 성공 영수증과
  Transfer 이벤트가 확인됐다.`
- `실험에 구성한 여섯 Caveat를 통과했다.`
- `애플리케이션 실행 경로의 브로드캐스트 직전 게이트다.`
- `로컬 signed delegation과 Sepolia direct 실행은 서로 다른 증거다.`
- `제한된 proof-of-concept 범위에서 실행 가능성을 확인했다.`

### 사용하면 안 되는 표현

- `Agent Wallet의 모든 보호를 통과했다.`
- `기존 보호는 모두 정적 검사다.`
- `Agent Wallet이 assetBalanceFloor를 집행했다.`
- `포트폴리오 4종 불변식이 Sepolia 지갑 경로에서 동작했다.`
- `MetaMask Agent Wallet signed delegation E2E를 완료했다.`
- `한 번의 성공 거래로 차단 효과를 증명했다.`
- `사용자의 실제 의도를 자동으로 이해했다.`
- `모든 공격 거래를 차단한다.`
- `wallet-native 또는 contract-native 마지막 보호장치다.`
- `전역 exactly-once 전송을 보장한다.`
- `40→39 USDC 거래가 현재 주 성공 증거다.`

## 12. 현재 글을 쓰기에 충분한 부분

현재 자료는 다음 범위의 RQ3 proof-of-concept 글을 쓰기에 충분하다.

- 결과 정책을 실행 후보에 적용하는 원리
- 승인, 후보, 실행 요청, 시뮬레이션을 해시와 필드로 결합하는 방법
- 누적 손실 negative와 대응 benign control의 양방향 결정
- 하한 유지 live accept와 하한 위반 live reject의 한 쌍
- 거부 시 sender 호출 전 종료와 무전송 감사
- 허용 뒤 영수증·Transfer 이벤트·사후 잔액 확인
- application-level gate, local fork, public testnet, remote signed delegation의 증거 경계

따라서 제목과 결론을 `제한된 범위의 기술적 실행 가능성`에 맞추면 RQ3 본문 작성은 가능하다.

## 13. 아직 부족한 부분과 우선순위

### P0 — 최종 제출 전에 필요한 재현성 정리

1. 최종 논문용 커밋 또는 태그를 고정해야 한다.
2. 전체 RQ3 재현 명령, Python·Node·Foundry·Anvil·CLI 버전, 잠금 파일 hash, 서브모듈 커밋,
   고정 fork block과 RPC 요구사항을 하나의 publication manifest로 보존해야 한다.
3. 현재 네 evidence bundle은 생성 당시의 실제 provenance를 보존해
   `repositoryDirty=true`, `delegationFrameworkDirty=true`이고 일부는 현재 HEAD 이전 커밋을
   가리킨다. 증거 내용을 덮어쓰지 말고, 최종 태그에서 동일 의미를 재검증한 별도 출판 manifest
   또는 새 버전 bundle을 만들어 연결해야 한다.
4. 최종 제출 환경에서 G3 재현, G3 strict validator, G3 reject, benign accept, 네 evidence bundle
   검증, 관련 chain 테스트를 한 번에 재실행하고 로그를 보존해야 한다.

### P0 — 방법론 서술에서 보강할 내용

1. RQ3-A와 RQ3-B가 다른 정책·환경을 쓴 이유를 명시해야 한다.
   - A: 포트폴리오 수준 표현력과 rolling loss 판별
   - B: 현재 실제 지갑 경로에서 구현된 단일 자산 floor의 실행 가능성
2. benign 후보가 실제 온체인 정상 거래가 아니라 반사실적으로 구성된 대조군인 이유와 생성 규칙을
   방법론에 써야 한다.
3. G3의 20회가 통계 표본이 아니라 하나의 공격 시퀀스임을 써야 한다.
4. 라이브 accept/reject는 한 정책·한 지갑·한 토큰의 한 쌍이므로 외적 타당성이 제한됨을 써야 한다.
5. 사후 잔액은 실행 당시 bundle에는 있으나 현재 공개 RPC로 재도출하지 못한 증거 경계를 써야 한다.

### P1 — 주장을 확장할 때 필요한 실험

- 동일한 공개 원격 실행에서 signed delegation과 Agent Wallet 전송을 연결한 E2E
- 여러 정상·위반 후보, 경계값, nonce·잔액·block drift를 포함한 반복 실험
- 독립 실행자 또는 제3자의 clean-room 재현
- RPC 실패, receipt timeout, ambiguous sender error, chain reorganization에 대한 fault injection
- 악성·비표준 ERC-20, fee-on-transfer token, reentrancy, 복수 이벤트 같은 현재 제외 입력 분석
- latency, RPC 호출 수, 실패율, 사용자 MFA·확인 비용 측정
- 사용자 승인 문구와 정책 이해도에 대한 별도 UX 연구

이 항목들은 좁은 proof-of-concept RQ3를 쓰기 위한 선행조건은 아니다. 일반적 안전성, 운영 준비성,
사용자 효과까지 주장하려면 필요하다.

## 14. RQ3 본문 권장 구조

다른 AI가 실제 글을 쓸 때는 다음 순서를 따른다.

1. RQ3 질문과 두 하위 실험의 분리
2. 보호장치의 위치와 신뢰 경계
3. 승인 정책·후보·시뮬레이션·실행 요청 결합 방식
4. RQ3-A 실험 설계
5. G3 누적 손실 후보 거부 결과
6. 대응 정상 후보 허용 결과
7. RQ3-B 실험 설계
8. Sepolia floor 유지 후보의 허용·브로드캐스트·영수증 결과
9. 동일 정책의 floor 위반 후보 거부·무전송 결과
10. local signed delegation과 remote direct 실행의 분리
11. 내적·구성·외적 타당성 및 재현성 한계
12. 제한된 RQ3 결론

RQ1의 기존 보호 체계 전체 분석, Agent Wallet 사고 사례, 37개 enforcer 목록, Transaction Protection,
RQ2의 접근 비교와 60개 benchmark 수치는 이 구조에 넣지 않는다. RQ3 입력과 경계를 설명하는 데
필요한 한두 문장 이상 확장하지 않는다.

## 15. AI에게 그대로 전달할 작성 명령

아래 명령과 §9.1의 Markdown, §9.3의 JSON을 함께 제공한다.

```text
당신은 연구 논문의 RQ3 절만 작성한다. 프로젝트 소개, RQ1 전체 분석, RQ2 성능 평가는 쓰지 마라.

먼저 docs/rq3-ai-writing-context-ko.md를 규칙 문서로 읽고,
docs/research-writing-source-of-truth-ko.md,
docs/conditional-signed-delegation-test-results-ko.md,
research/evidence/README.md,
README.md의 현재 실행 경로와 Agent Wallet 어댑터 절,
chain/README.md의 G3와 재현 절을 교차검증하라.

RQ3를 반드시 다음 두 경로로 분리하라.
1) 고정 메인넷 포크의 G3 누적 손실 후보 reject와 반사실 benign 후보 accept
2) Sepolia Agent Wallet direct assetBalanceFloor accept와 같은 정책의 preflight reject

각 결과에서 정책 입력, 시뮬레이션/후보, 결정, 브로드캐스트 여부, 영수증·이벤트·사후 상태,
증거 등급, 한계를 순서대로 써라. accepted와 transaction success를 구분하라.

로컬 signed delegation 성공과 Sepolia direct 성공을 합쳐 원격 signed-delegation E2E로 쓰지 마라.
assetBalanceFloor는 application-level gate이며 wallet-native enforcement가 아니다.
현재 주 성공 거래는 1.0 USDC에서 0.1 USDC를 보내 0.9 USDC가 남은 거래이고 하한은 0.5 USDC다.
과거 40→39 USDC 거래를 주 증거로 사용하지 마라.

결론은 제한된 proof-of-concept의 기술적 실행 가능성으로 한정하라. 모든 공격 차단,
일반적 안전성, 사용자 의도 자동 이해, 전역 마지막 단계, 원격 signed delegation 완료를 주장하지 마라.
```

## 16. RQ3 결론 정본

> 제한된 고정 실험 범위에서 승인된 결과 상태 정책은 Delegation Framework의 실험용 여섯 Caveat를
> 통과한 G3 누적 손실 후보를 거부하고 대응 정상 후보를 허용했다. 별도의 Sepolia direct 경로에서는
> application-level `assetBalanceFloor`가 하한 위반 후보를 Agent Wallet 요청 생성 전에 거부했으며,
> 하한을 유지하는 후보는 실제 MetaMask Agent Wallet Guard Mode와 MFA를 거쳐 브로드캐스트된 뒤
> 성공 영수증과 Transfer 이벤트가 확인됐다. 이 결과는 결과 상태 기반 사전 검증의 제한된 기술적
> 실행 가능성을 보여주지만, wallet-native 집행, 일반적 안전성 또는 Agent Wallet signed delegation의
> 공개 원격 end-to-end 완료를 증명하지 않는다.

## 17. 외부 1차 자료

- 고정 Delegation Manager 명세:
  <https://github.com/MetaMask/delegation-framework/blob/197463b4aba3409adef1df544dabafc3636ee82d/documents/DelegationManager.md>
- 고정 Caveat Enforcers 명세:
  <https://github.com/MetaMask/delegation-framework/blob/197463b4aba3409adef1df544dabafc3636ee82d/documents/CaveatEnforcers.md>
- Circle 테스트넷 USDC 주소:
  <https://developers.circle.com/stablecoins/usdc-contract-addresses>
- Sepolia 주 성공 거래:
  <https://sepolia.etherscan.io/tx/0xaf7566c59d0b10c3983f2478088ac31df165b1acaf1b6084acacd96d08d4f500>

공식 프레임워크 동작을 설명할 때는 `main` 브랜치의 최신 문서만 인용하지 말고 실험에 사용한
고정 커밋 permalink를 사용한다.
