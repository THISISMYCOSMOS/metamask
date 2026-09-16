# Caveat terms 인코딩 정본 (핀 커밋 실측)

출처: `chain/lib/delegation-framework` @ `197463b4aba3409adef1df544dabafc3636ee82d`
추출 방법: `src/enforcers/*.sol`의 `getTermsInfo` 본문을 직접 읽음. 문서·추측 아님.

이 문서의 목적은 **`...:invalid-terms-length` 계열 revert를 "baseline이 작동했다"로
오해하는 것을 막는 것**이다. 그 문자열은 baseline 작동 증거가 아니라 우리 인코딩이 틀렸다는
증거다. 네거티브 컨트롤을 라벨하기 전에 반드시 이 표와 대조한다.

## 공통 구조

`terms`는 전부 **packed bytes**(`abi.encodePacked` 상당)다. ABI 인코딩(32B 정렬·오프셋)이
아니다. viem 기준 `concat([...])` + `pad`/`toHex`로 만든다.

```solidity
struct Caveat { address enforcer; bytes terms; bytes args; }
```

`args`는 해싱에서 제외되므로 서명 후 조작 가능하다. 아래 6종은 `args`를 쓰지 않으므로 `0x`.

## 1. AllowedTargetsEnforcer — 20B × N

```
[target0 (20B)][target1 (20B)]...
```

- 검증: `length % 20 == 0 && length != 0`, 아니면 `invalid-terms-length`
- 불일치 시: `AllowedTargetsEnforcer:target-address-not-allowed`
- hook: `beforeHook` (Single callType + Default execType 강제)

## 2. AllowedMethodsEnforcer — 4B × N

```
[selector0 (4B)][selector1 (4B)]...
```

- 검증: `length % 4 == 0 && length != 0`, 아니면 `invalid-terms-length`
- `callData.length >= 4` 아니면 `invalid-execution-data-length`
- 불일치 시: `AllowedMethodsEnforcer:method-not-allowed`

## 3. ValueLteEnforcer — 32B

```
[maxValue (uint256, 32B)]
```

- `length == 32` 아니면 `invalid-terms-length`
- `value > maxValue` → `ValueLteEnforcer:value-too-high`

## 4. TimestampEnforcer — 32B (16B + 16B)

```
[timestampAfterThreshold (uint128, 16B)][timestampBeforeThreshold (uint128, 16B)]
```

**앞 16B가 after(시작), 뒤 16B가 before(만료)다.** 순서를 뒤집으면 즉시
`early-delegation` 또는 `expired-delegation`이 난다.

- 비교는 **strict**: `block.timestamp > after`, `block.timestamp < before`
  경계값이 아니라 초과/미달이어야 한다.
- `0`은 "제한 없음"으로 취급된다.
- hook: `beforeHook` (Default execType만 강제, callType 제한 없음)

## 5. ERC20BalanceChangeEnforcer — 73B

```
[enforceDecrease (1B)][token (20B)][recipient (20B)][amount (uint256, 32B)]
```

- `enforceDecrease`: `0x01` = 감소 상한 검사, `0x00` = 증가 하한 검사
  (`_terms[0] != 0` 이므로 0 이외 아무 값이나 true)
- `enforceDecrease == true`: `afterBalance >= beforeBalance - amount`
  위반 → `exceeded-balance-decrease`
- `enforceDecrease == false`: `afterBalance >= beforeBalance + amount`
  위반 → `insufficient-balance-increase`
- `beforeHook`이 `isLocked[hashKey]`를 세우고 `afterHook`이 지운다. 같은
  `(delegationManager, token, delegationHash)`를 **한 번의 `redeemDelegations` 배치 안에서
  두 번** 쓰면 `enforcer-is-locked`. 배치 크기 1 · 블록당 1건이면 걸리지 않는다.
- `hashKey = keccak256(abi.encode(msg.sender, token, delegationHash))`
  여기서 `msg.sender`는 **DelegationManager**다.

> **주의 — 이 enforcer의 범위가 논문의 논거다.** 검사 단위가 redemption 1건이고
> 토큰 1종이며 명목 수량 기준이다. 건 사이 누적이 없다. `baseline-config.md` §2 참조.

## 6. ERC20PeriodTransferEnforcer — 116B

```
[token (20B)][periodAmount (32B)][periodDuration (32B)][startDate (32B)]
```

- `length == 116` 아니면 `invalid-terms-length`
- `callData.length == 68` **정확히** 아니면 `invalid-execution-length`
  (= `transfer(address,uint256)` 셀렉터 4 + 인자 64)
- `token != target` → `invalid-contract`
- 셀렉터 != `IERC20.transfer.selector` (`0xa9059cbb`) → `invalid-method`
- 첫 사용 시 0 검사: `invalid-zero-start-date` / `invalid-zero-period-amount` /
  `invalid-zero-period-duration`
- `block.timestamp < startDate` → `transfer-not-started`
- `transferAmount > available` → **`transfer-amount-exceeded`** ← G2 정답
- `transferAmount`는 `callData[36:68]`에서 읽는다.
- 기간 인덱스: `currentPeriod = (block.timestamp - startDate) / periodDuration + 1`
  기간이 바뀌면 `transferredInCurrentPeriod`가 0으로 리셋된다.
  **이 리셋이 "일일 한도 리셋"에 해당하고, Phase 1 위반 클래스(누적 손실)의 정조준 지점이다.**

## caveat 배열 순서가 revert 문자열을 결정한다

`DelegationManager.redeemDelegations`의 `beforeHook` 루프는 `caveats` 배열을
**오름차순(index 0 → N)** 으로 돈다. `afterHook`/`afterAllHook`은 역순이다.

따라서 **허용목록 밖 대상** 네거티브 컨트롤에서 `AllowedTargetsEnforcer`가
`ERC20PeriodTransferEnforcer`보다 **앞 인덱스**에 있어야 한다. 뒤에 있으면
`ERC20PeriodTransferEnforcer:invalid-contract`가 먼저 터지고, 그러면 우리가 의도한
"허용목록이 막았다"가 아니라 "토큰 주소가 안 맞았다"를 관측한 것이 된다.

**고정 caveat 순서 (변경 금지):**

| index | enforcer |
| --- | --- |
| 0 | `AllowedTargetsEnforcer` |
| 1 | `AllowedMethodsEnforcer` |
| 2 | `ValueLteEnforcer` |
| 3 | `TimestampEnforcer` |
| 4 | `ERC20PeriodTransferEnforcer` |
| 5 | `ERC20BalanceChangeEnforcer` |

`ERC20BalanceChangeEnforcer`가 마지막인 이유: `beforeHook`에서 lock을 잡으므로 앞선
enforcer가 revert하면 lock이 남지 않는다(트랜잭션 전체가 되돌아가므로 실제로는 무관하지만,
`afterHook`이 역순이라 index 5가 실행 직후 가장 먼저 차분을 본다).

## EIP-712 서명 파라미터

```
domain = {
  name: "DelegationManager",
  version: "1",              // DOMAIN_VERSION, NOT the "1.3.0" NAME/VERSION constant
  chainId: 1,
  verifyingContract: <배포한 DelegationManager 주소>
}

DELEGATION_TYPEHASH = keccak256(
  "Delegation(address delegate,address delegator,bytes32 authority,Caveat[] caveats,uint256 salt)"
  "Caveat(address enforcer,bytes terms)"
)
CAVEAT_TYPEHASH = keccak256("Caveat(address enforcer,bytes terms)")
```

- `Delegation.signature`는 해싱에서 **제외**된다. `Caveat.args`도 제외된다.
- caveat 배열 해시 = 각 caveat 패킷 해시를 `abi.encodePacked`로 이어 붙인 뒤 keccak256.
- `authority = ROOT_AUTHORITY = 0xff...ff` (32바이트 전부 0xff).
- `ANY_DELEGATE = address(0xa11)`. 우리는 실제 delegate EOA를 쓴다.
- delegator가 컨트랙트(스마트계정)이면 `IERC1271.isValidSignature`로 검증된다.
  `HybridDeleGator`는 **서명 길이 65B이면 ECDSA로 `owner()`와 비교**한다. 즉 소유자 EOA의
  평범한 secp256k1 서명이면 통과한다. P256/WebAuthn 경로는 쓰지 않는다.

## 계정 구성 (업스트림 테스트 하니스와 동일 경로)

`test/utils/BaseTest.t.sol`의 `deployDeleGator_Hybrid`가 정본이다.

```
delegator = new ERC1967Proxy(
  hybridDeleGatorImpl,
  abi.encodeWithSignature("initialize(address,string[],uint256[],uint256[])",
                          owner, [], [], [])
)
```

`HybridDeleGator` 생성자는 `(IDelegationManager, IEntryPoint)`를 받는다. EntryPoint v0.7
(`0x0000000071727De22E5E9d8BAf0edAc6f37da032`)은 메인넷에 이미 배포되어 있으므로 포크에서
그대로 참조한다 — Phase 1은 UserOp 경로를 쓰지 않으므로 EntryPoint는 생성자 인자로만 필요하다.
`DelegationManager` 생성자는 `(address owner)`를 받고 `whenNotPaused` 상태로 시작한다.
