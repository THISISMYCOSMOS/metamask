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

### Delegation Framework — caveat enforcer 38종

출처: <https://github.com/MetaMask/delegation-framework/tree/main/src/enforcers>

```
AllowedCalldataEnforcer                   ERC721BalanceChangeEnforcer
AllowedMethodsEnforcer                    ERC721MultiOperationIncreaseBalanceEnforcer
AllowedTargetsEnforcer                    ERC721TransferEnforcer
ApprovalRevocationEnforcer                ExactCalldataBatchEnforcer
ArgsEqualityCheckEnforcer                 ExactCalldataEnforcer
BlockNumberEnforcer                       ExactExecutionBatchEnforcer
CaveatEnforcer                            ExactExecutionEnforcer
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

## 4. 고정 파라미터 (Phase 1에서 채움)

| 항목 | 값 |
| --- | --- |
| delegation-framework 커밋 해시 | *TBD* |
| 포크 체인 / 블록 번호 | *TBD* |
| 배포한 enforcer와 주소 | *TBD* |
| 한도 파라미터 (일일 상한 등) | *TBD* |
| 오라클 (Chainlink 피드 주소) | *TBD* |
| 모드 설정 | Beast Mode 상당 (승인 중단 없음) |

## 5. 네거티브 컨트롤 (기획안 미포함, 추가 확정)

baseline이 실제로 작동함을 증명하기 위해, **baseline이 차단하는 트레이스 1건**을
같이 제출한다 (한도 초과 호출 또는 허용목록 외 대상). 이것이 없으면 위반 트레이스
3건의 신뢰도 전체가 "설정을 약하게 한 것 아니냐"는 의심에 노출된다.
