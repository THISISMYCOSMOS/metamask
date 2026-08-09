# Baseline 구성 (Phase 0)

조사일 2026-08-07. 이 문서는 **재구성한 baseline이 실제와 같음을 증명하는 근거**다.
심사자의 첫 공격 지점이 "baseline을 약하게 잡고 통과했다고 주장하는 것 아니냐"이므로,
여기 적힌 버전·주소·파라미터는 트레이스와 함께 고정한다.

## 1. 조사로 확인된 사실

### MetaMask Agent Wallet (2026-08-06 출시)

| 항목 | 내용 |
| --- | --- |
| 사용자 통제 | 일일(daily) 지출 한도, 프로토콜 허용목록, 위험 성향 설정 |
| 운영 모드 | **Guard Mode**(기본, 정책 밖 거래는 사용자 승인) / **Beast Mode**(승인 중단 축소, 플래그된 거래만 2FA) |
| 실행 전 검사 | ① 시뮬레이션 ② Blockaid 위협 스캔 ③ MEV 보호 |
| 사후 보상 | **Transaction Protection — 월 최대 $10,000.** 시뮬레이션·위협 스캔을 통과했는데도 손실이 난 거래를 보상 |
| 지원 체인 | HyperLiquid 및 지원 EVM 체인 (Robinhood, Monad 포함) |
| 지원 에이전트 | Claude Code, Codex, OpenClaw, Hermes, OpenCode, Cursor |

출처: <https://metamask.io/news/introducing-metamask-agent-wallet>

> **논문 활용** — Transaction Protection의 존재 자체가 본 연구의 전제를 뒷받침한다.
> "전 계층을 통과했음에도 손실이 발생하는 거래"가 있다는 것을 MetaMask가
> 보험 상품으로 인정한 것이기 때문이다.

### Delegation Framework — 구체 caveat enforcer 37종

출처: <https://github.com/MetaMask/delegation-framework/tree/main/src/enforcers>

**정정 (2026-08-08, Phase 1 실측)** — `src/enforcers/`의 `.sol` 파일은 38개지만 그 중
`CaveatEnforcer`는 `abstract contract`인 **베이스 클래스**이고 배포 대상이 아니다.
배포 가능한 구체 enforcer는 **37종**이다. 업스트림 `script/DeployCaveatEnforcers.s.sol`이
배포하는 개수도 정확히 37종이며, 파일 목록과 배포 목록의 차집합은 `CaveatEnforcer` 하나뿐임을
확인했다(Phase 1에서 실제 배포로 검증). 논문에는 **37종**을 쓴다. 파일 개수 38을 쓰면
심사자가 배포 스크립트를 세어 즉시 반박한다.

```
AllowedCalldataEnforcer                   ERC721BalanceChangeEnforcer
AllowedMethodsEnforcer                    ERC721MultiOperationIncreaseBalanceEnforcer
AllowedTargetsEnforcer                    ERC721TransferEnforcer
ApprovalRevocationEnforcer                ExactCalldataBatchEnforcer
ArgsEqualityCheckEnforcer                 ExactCalldataEnforcer
BlockNumberEnforcer                       ExactExecutionBatchEnforcer
CaveatEnforcer  ← abstract, 배포 대상 아님   ExactExecutionEnforcer
DeployedEnforcer                          IdEnforcer
ERC1155BalanceChangeEnforcer              LimitedCallsEnforcer
ERC1155MultiOperationIncreaseBalanceEnf.  LogicalOrWrapperEnforcer
ERC20BalanceChangeEnforcer                MultiTokenPeriodEnforcer
ERC20MultiOperationIncreaseBalanceEnf.    NativeBalanceChangeEnforcer
ERC20PeriodTransferEnforcer               NativeTokenMultiOperationIncreaseBalanceEnf.
ERC20StreamingEnforcer                    NativeTokenPaymentEnforcer
ERC20TransferAmountEnforcer               NativeTokenPeriodTransferEnforcer
                                          NativeTokenStreamingEnforcer
                                          NativeTokenTransferAmountEnforcer
                                          NonceEnforcer
                                          OwnershipTransferEnforcer
                                          RedeemerEnforcer
                                          SpecificActionERC20TransferBatchEnforcer
                                          TimestampEnforcer
                                          ValueLteEnforcer
```

### ICaveatEnforcer 훅

```
beforeAllHook → beforeHook → 실행 → afterHook → afterAllHook
```

출처: <https://docs.metamask.io/smart-accounts-kit/concepts/delegation/caveat-enforcers/>

## 2. 핵심 주장의 수정 (확정)

**기획안 원문은 사실과 다르므로 사용하지 않는다.**

> ~~"이 계층들은 모두 개별 호출의 정적 속성(금액·대상 주소·함수 셀렉터)만 검사하므로"~~

`ERC20BalanceChangeEnforcer` / `NativeBalanceChangeEnforcer` 및 각
`MultiOperationIncreaseBalance` 계열은 afterHook에서 **잔액 변화, 즉 상태 차분을
검사한다.** 원문을 그대로 쓰면 MetaMask 심사자에게 자사 코드로 즉시 반박당한다.

**확정 문구 방향** — 결과-인접 enforcer는 존재하나 그 단위가

- **토큰 1종** 단위 (자산 간 상계 없음)
- **redemption 1건** 범위 (건 사이 누적 없음)
- **명목 수량** 기준 (오라클 환산 가치 없음)

이므로 다음 4종을 표현할 수 없다.

| 표현 불가 | 대응 불변식 |
| --- | --- |
| 오라클 환산 포트폴리오 가치 하한 | `portfolioValueFloor` |
| redemption 경계를 넘는 롤링 윈도우 누적 손실 | `cumulativeLossCap` |
| 참조가 대비 실행 품질 | `executionPriceBand` |
| 자산 간 상계된 순 방향성 노출 | `netDeltaBound` |
| 실행 장소의 풀 규모·컨트랙트 신규성 | `venueIntegrity` |

이 대비가 불변식 5종의 도출 근거이자 RQ1의 답이다.

**부수 확인** — `beforeHook` 스냅샷 / `afterHook` 차분 구조를 프레임워크가 이미
지원하므로, Level A의 `OutcomeInvariantEnforcer` 실제 구현은 기존 enforcer 상속으로
가능하다. 기획안의 "100줄 내외, 3일 규모" 추정이 유지된다.

## 3. 미해결 — Phase 1 착수 전 확정 필요

**Agent Wallet이 Delegation Framework 위에 구현되었는지 공식 문서에 명시가 없다.**
발표 페이지에 스마트 계정·위임 언급이 없다. 한도·허용목록이 온체인 caveat이 아니라
백엔드 정책일 가능성이 있고, 그 경우 "MetaMask 컨트랙트 배포로 baseline 재구성"이라는
Level B 구현 범위의 전제가 흔들린다.

**대응 방침 (ⓐ 채택)**

- ⓐ 분석 대상을 **Delegation Framework(공개 코드)** 로 명시 고정하고, Agent Wallet은
  동기 사례로만 인용한다. 코드가 공개되어 재구성 충실도를 검증받을 수 있다.
- ⓑ Agent Wallet 정책 계층을 문서 기술대로 오프체인 재구현 — 검증 불가능한 재구성이
  되어 심사에서 불리하다.

논문에는 "공개된 Delegation Framework의 caveat enforcer 집합을 baseline으로 삼는다"고
명시하고, 비공개 계층(Blockaid 내부 규칙)은 알려진 한계로 선제 서술한다.

## 4. 고정 파라미터

확정일 2026-08-07. **여기 적힌 값은 이후 변경하지 않는다.** 결과가 안 나온다고 블록이나
한도를 옮기는 것이 심사에서 가장 먼저 의심받는 행위이므로, 실측 전에 먼저 박는다.

| 항목 | 값 |
| --- | --- |
| delegation-framework 커밋 해시 | `197463b4aba3409adef1df544dabafc3636ee82d` |
| 포크 체인 | Ethereum mainnet (chainId 1) |
| 포크 블록 번호 | `25700000` |
| 포크 블록 해시 | `0x528d3ac8a0fbb982d354cbef4f842140ed0ae75cbcdf41dbd08324e298a72abf` |
| 포크 블록 타임스탬프 | `1786068491` (2026-08-07T02:08:11Z) |
| 오라클 (Chainlink ETH/USD) | `0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419`, decimals 8 |
| 해당 블록 오라클 값 | `189811115161` = **$1898.11 / ETH** (updatedAt `1786066847`, 블록 대비 1644s 전) |
| 결제 토큰 (USDC) | `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48`, decimals 6 |
| 배포한 enforcer와 주소 | 구체 enforcer **37종 전부 배포.** 전체 목록은 `chain/deployments/manifest.json` (활성 6종은 아래) |
| 한도 파라미터 (일일 상한 등) | 일일 2,000 USDC / 회차 500 USDC / native value 상한 0 — 근거는 `docs/phase1-parameters.md` |
| 모드 설정 | Beast Mode 상당 (승인 중단 없음) |

### 활성 caveat 6종 — 배포 주소와 파라미터 (Phase 1 실측)

CREATE2 salt `"intent-as-spec-phase1"`, 배포자 anvil key #0
(`0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266`). 재실행 시 동일 주소가 재현됨을
`chain/src/deploy.ts`가 코드로 검증한다.

| index | enforcer | 주소 | 파라미터 |
| --- | --- | --- | --- |
| 0 | `AllowedTargetsEnforcer` | `0xef2f79e2A6Cda4f31bd213b0d1877a9B93F70038` | targets = [USDC] |
| 1 | `AllowedMethodsEnforcer` | `0x27aF251F5cd8AE094925aEF1722655Ea822Edbe1` | selectors = [`0xa9059cbb`] |
| 2 | `ValueLteEnforcer` | `0x0B5C5BEA5Df2fA9879Fac0AA3690aE2caD9eC498` | maxValue = 0 |
| 3 | `TimestampEnforcer` | `0x8AF1d7a43158697106953f7F2EfADa603984269A` | after 1786068491 / before 1788660491 |
| 4 | `ERC20PeriodTransferEnforcer` | `0x5c0FD678387dD9a4f6D7ae4a4a2798439a0AEBb0` | 2,000 USDC / 86400s / start 1786068491 |
| 5 | `ERC20BalanceChangeEnforcer` | `0x2Ab40067D719bc5938AA1875CB409A9DBF50022c` | decrease, 500 USDC, recipient = delegator |

**index는 caveat 배열 순서이며 의미가 있다.** `beforeHook`이 오름차순으로 평가되므로
이 순서가 위반 시 관측되는 revert 문자열을 결정한다. `docs/caveat-encoding.md` 참조.

기타 핵심 주소 — `DelegationManager` `0xeA6F34E56c9bEa6d9114A30b52e040af2b594373`,
`HybridDeleGatorImpl` `0xd321B8751D0dE55F9D8e25117216FFF1f1923805`,
delegator 스마트계정 `0x09e68b4a2335a2aaa1944bc3938d285b883f11e1`.

### G1 결정론 (실측)

| 항목 | 값 |
| --- | --- |
| 최종 블록 | `25700048` |
| 정본 상태 다이제스트 | `0xe29efd531e1a734a1fd94a6bfd53338d0a296f0185d0e072cb9c9c1f3a26ef48` |
| 독립 2회 실행 결과 | 스냅샷 바이트 단위 일치 |

**블록 헤더의 `stateRoot`는 쓰지 않는다** — anvil이 포크 모드에서 머클 상태 루트를
계산하지 않아 전부 `0x00…00`이기 때문이다(실측 확인). 근거와 대체 다이제스트의 구성은
`chain/README.md`에 있다.

### 커밋 핀 근거

`main` HEAD를 쓴다. 커밋 해시는 불변이므로 태그와 동등하게 인용 가능하다.

**실측 (2026-08-08, `git ls-tree` + 실제 배포)**

| 리비전 | `src/enforcers/*.sol` | abstract 베이스 | 구체 enforcer |
| --- | --- | --- | --- |
| 태그 `v1.3.0` | 33 | `CaveatEnforcer` 1 | **32종** |
| 핀 커밋 `197463b4` (`v1.3.0-153-g197463b`) | 38 | `CaveatEnforcer` 1 | **37종** |

최신 태그 `v1.3.0`은 구체 enforcer가 32종뿐이라 §1에 기록된 37종과 불일치한다.
핀 커밋은 정확히 37종으로 §1과 일치하며, 이 37종 전부가 Phase 1에서 실제로 배포되었다
(`chain/deployments/` 매니페스트).

### 배포 전 핀 커밋/워크트리 검사 (fail closed)

`chain/src/deploy.ts`의 `assertFrameworkPinnedAndClean()`이 어떤 배포 트랜잭션보다
먼저 실행되어, `chain/lib/delegation-framework`의 git HEAD가 위 핀 커밋과 정확히
같은지, 그리고 `broadcast/` · `out/` · `cache/` 밖에 워크트리 변경이 없는지를
`git`(`execFileSync`)으로 검사한다. `broadcast/`는 업스트림이 추적하는 디렉터리라
파이프라인 실행마다 갱신되므로 항상 허용한다. 하나라도 어긋나면 anvil 연결 전에
throw한다. 가드 통과 후에는 `forge build --force`로 핀 소스에서 `out/`과 `cache/`를
다시 생성한 다음 배포해 기존 생성물 변조가 배포 바이트코드로 이어지지 않게 한다.

### 블록 선정 근거

선정 시점 최신 블록은 25702227이었고, 25700000은 그보다 2227블록(약 7.4시간) 뒤라
확정(finalized) 구간에 안전하게 들어간다. 재구성(reorg) 가능성이 없다.
해당 블록에서 아카이브 조회·Chainlink 피드·USDC 상태를 모두 실제로 확인했다.

### G3 오라클 핀 (Phase 1 실측)

G3(누적 손실 트레이스)는 포트폴리오 가치를 오라클 환산으로 증명해야 하므로, ETH/USD 외에
USDC/USD 피드도 포크 블록에서 실측해 핀으로 박는다.

| 항목 | 값 |
| --- | --- |
| USDC/USD 어그리게이터 | `0xc9E1a09622afdB659913fefE800fEaE5DBbFe9d7`, decimals 8 |
| 포크 블록 answer | `99976752` = **$0.99976752/USDC** |
| 포크 블록 updatedAt | `1786003223` (포크 블록 ts 대비 65,268초 전) |
| ETH/USD 어그리게이터 (기존 §4 핀 재확인) | `0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419`, decimals 8 |
| ETH/USD answer / updatedAt | `189811115161` / `1786066847` (포크 블록 ts 대비 1,644초 전) |

두 피드 모두 다음 fail-closed 검증 5종 + 신선도 상한을 통과해야 G3가 트레이스를 쓴다
(`chain/src/cumulative-loss.ts`).

1. `decimals() === 8`
2. `answer > 0`
3. `updatedAt <= 포크 블록 타임스탬프`
4. `answeredInRound >= roundId`
5. `answer`/`updatedAt`이 위 표의 핀 값과 정확히 일치
6. 신선도: `포크 블록 타임스탬프 - updatedAt <= 86400`초(하루) — 이 상한은 G3 구현 시
   선택해 현재 코드와 검증기에 고정한 값이다. 피드 age를 관측하기 전에 선택했다는 기록은
   없으므로 사전선정으로 주장하지 않는다. `docs/phase1-parameters.md`의 정정 노트 참조.

모든 오라클 조회는 `blockNumber: 25700000`(포크 블록)으로 명시 조회한다 — 로컬에서 채굴된
블록이 아니라 포크 스냅샷 시점의 값임을 코드로 못박기 위함이다.

## 5. 네거티브 컨트롤 (기획안 미포함, 추가 확정)

baseline이 실제로 작동함을 증명하기 위해, **baseline이 차단하는 트레이스 1건**을
같이 제출한다 (한도 초과 호출 또는 허용목록 외 대상). 이것이 없으면 위반 트레이스
3건의 신뢰도 전체가 "설정을 약하게 한 것 아니냐"는 의심에 노출된다.
