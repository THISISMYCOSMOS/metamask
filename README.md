# Intent as Specification

LLM 기반 불변식 합성과 실행 직전 검증을 통한 에이전트 거래 보안.

권한(authorization)의 세분화로는 의도(intent)를 표현할 수 없다. 한도와 허용목록을
아무리 촘촘히 해도 그 교집합 안에 사용자에게 손해인 경로가 남는다. 방어는 허용할
호출을 좁히는 방향이 아니라 **허용할 결과 상태를 명세하는 방향**으로 이동해야 한다.

## 구조

| 디렉터리 | 언어 | 역할 |
| --- | --- | --- |
| `backend/` | Python | Gemini 무료 티어 구조화 출력 → 공유 정책 제안 컴파일 |
| `core/` | Python | 공유 계약, 정확한 해시 승인, 로컬 시뮬레이션, 결정론적 판정, 실행 게이트 |
| `chain/` | TS (viem + anvil) | 포크 환경, 위임 실행 경로, 상태 스냅샷 → trace JSON |
| `verifier/` | Python | 불변식 스키마 + 결정론적 평가기 |
| `synth/` | Python | 기존 오프라인 fixture 기반 자연어 계약 테스트 |
| `specs/` | Solidity interface | 결과 기반 enforcer 인터페이스 명세 |
| `traces/` | JSON | 실행 트레이스 (커밋 대상 산출물) |
| `docs/` | Markdown | baseline 구성, 페이즈별 합격 기준 |

기존 Phase 1~3 재현 경로에서 `chain/`과 `verifier/`의 접점은 `traces/*.json`이다.
새 제어 실행 경로는 `backend/`와 `core/`가 같은 Pydantic 계약을 직접 공유한다.

## 현재 제어 실행 경로

`자연어 입력 → 실제 Gemini 구조화 출력 → 정책 제안 → 사용자 검토 수정(선택) → 정확한 제안 해시 승인 →`
`로컬 Anvil 스냅샷에서 예정 전송 실행 → 상태 복원 확인 → 결정론적 판정 → 승인 시 1회 전송`

- Backend는 Gemini Developer API 무료 티어를 기본으로 사용하며, fixture fallback 없이 실패 시 제안을 만들지 않는다.
- LLM은 체인·지갑·토큰·식별자를 선택할 수 없고 ERC-20 잔고 하한만 제안한다.
- Core는 시뮬레이션된 calldata·nonce·gas·잔고 변화와 실행 직전 컨텍스트를 다시 검증한다.
- Core의 직접 외부 전송 구현은 loopback Anvil 전용이다. 브라우저 MetaMask 테스트넷 경로와
  Agent Wallet CLI 어댑터는 아래처럼 별도 경계로 유지한다.
- `ui/`의 자연어 제출 경로는 Gemini Backend/Core 공유 계약에 연결되어 있다. 사용자가 LLM의
  잔고 하한을 수정하면 원본 제안과 해시는 이력에 남고, 변경 전·후 값과 수정 주체를 포함한
  `revised-policy-proposal`이 새 해시로 생성된다. 기존 승인은 무효화되며 새 해시 승인이 필요하다.
  예전 4종 구조화 편집기는 회귀 테스트용으로만 남아 있고 화면에서는 노출하지 않는다.
- 승인 후 UI는 로컬 Anvil 제어 실행 또는 브라우저 MetaMask 테스트넷 실행 API로 이어진다.
- `chain/src/delegated-floor-gate.ts`는 단일 root delegation의 실제 `redeemDelegations`
  calldata를 디코딩해 delegate·delegator·manager·토큰·수취인·금액을 직접 대조한다. 이 게이트의
  승인 결과는 `agent-wallet-cli.ts`가 활성 Agent Wallet 주소까지 다시 대조한 뒤 공식 `mm wallet
  send-transaction --wait` 명령에 정확한 outer transaction으로 전달한다. 실제 원격 실행에는
  별도의 Agent Wallet 로그인·초기화와 서명된 delegation이 필요하다.

외부 LLM 호출 없이 공유 계약과 전체 제어 경로를 검증하려면:

```powershell
uv run --cache-dir tmp\uv-cache --project verifier python -m unittest `
  backend.test_gemini_compiler backend.test_policy_compiler `
  core.test_core core.test_integration `
  core.test_rpc_simulator -v

cd ui
uv run --cache-dir ..\tmp\uv-cache --project ..\verifier python -m unittest test_server -v

cd ..\chain
npm test
```

실제 Gemini 컴파일에는 Google AI Studio의 `GEMINI_API_KEY`가 필요하다.
`GEMINI_MODEL`의 기본값은 `gemini-3.5-flash-lite`다. 무료 티어 입력은 Google 제품 개선에
사용될 수 있으므로 비밀키나 비공개 지갑 메타데이터를 자연어 입력에 포함하지 않는다.
실패 시 오프라인 응답으로 자동 전환하지 않는다.

### 로컬 제어 실행 프로그램

UI는 승인 이후 예정 ERC-20 전송을 받아 다음 세로 흐름을 실제로 실행한다.

`예정 거래 입력 → loopback Anvil snapshot 실행 → receipt/잔고 변화 확인 → evm_revert 복원 →`
`결정론적 판정 → 컨텍스트 재확인 → 승인 시 동일 거래 1회 제출`

브라우저는 수취인·전송량·선택적 gas limit만 보낼 수 있다. chain·wallet·token은 사용자가
승인한 제안과 서버의 제어 실행 설정에서 가져오며, 원격 RPC는 Core가 거부한다. 로컬 데모는
다음처럼 별도 Anvil에서 실행한다.

```powershell
# 터미널 1
anvil --port 18547 --chain-id 31337

# 터미널 2: 출력되는 Deployed to 주소를 아래 CONTROLLED_TOKEN_ADDRESS에 사용
forge create src/TestERC20.sol:TestERC20 --root core/test_contract `
  --rpc-url http://127.0.0.1:18547 `
  --from 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266 `
  --unlocked --broadcast --constructor-args 1000000000000000000000000

$env:ANVIL_RPC_URL = "http://127.0.0.1:18547"
$env:CONTROLLED_CHAIN_ID = "31337"
$env:CONTROLLED_WALLET_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
$env:CONTROLLED_TOKEN_ADDRESS = "<Deployed to 주소>"
$env:CONTROLLED_TOKEN_SYMBOL = "CTT"
$env:CONTROLLED_TOKEN_DECIMALS = "18"
$env:GEMINI_API_KEY = "<Google AI Studio key>"

uv run --env-file .env --cache-dir tmp\uv-cache --project verifier python ui\server.py
```

`CONTROLLED_*` 중 하나라도 설정하면 다섯 값을 모두 요구한다. 실행 API는 `ANVIL_RPC_URL`이
없거나 loopback Anvil이 아니면 실패하며, 동일 승인·수취인·금액·gas plan의 재제출을 현재
서버 프로세스에서 거부한다. 이것은 로컬 연구 프로그램이며 Agent Wallet 네이티브 집행이나
원격 체인 전송이 아니다.

### MetaMask devnet/testnet 실행

테스트넷 ERC-20을 MetaMask 계정으로 받은 뒤 `.env`에 공개 바인딩 정보를 설정한다. 지갑
주소는 환경변수로 고정하지 않고 브라우저에서 연결된 MetaMask 계정을 사용한다.

```dotenv
METAMASK_CHAIN_ID=<MetaMask에 추가한 devnet/testnet chain id>
METAMASK_TOKEN_ADDRESS=<해당 네트워크의 테스트 ERC-20 주소>
METAMASK_TOKEN_SYMBOL=TESTUSDC
METAMASK_TOKEN_DECIMALS=6
```

서버를 `uv run --env-file .env ...`로 실행하고 화면에서 MetaMask를 연결한다. 자연어 정책은
연결된 `chainId + wallet`과 서버의 토큰 정보에 결합된다. 승인 후 예정 거래를 제출하면
브라우저가 MetaMask RPC로 `balanceOf`, nonce, `eth_call`, gas estimate를 구하고 서버가
정책 하한과 정확한 ERC-20 calldata를 결정론적으로 검사한다. 통과한 요청만
`eth_sendTransaction`으로 MetaMask 확인창에 전달된다.

전송 후 브라우저는 테스트넷 영수증을 기다리고 영수증 블록의 토큰 잔고를 다시 읽는다. 서버는
성공 status, transaction/from/to 결합, 정확히 한 개의 ERC-20 `Transfer` 이벤트, 승인된 하한 이상인
사후 잔고를 모두 확인해야 `wallet-confirmed`로 기록한다.

이 경로는 devnet/testnet 통합용 **애플리케이션 수준 게이트**다. MetaMask 밖에서 보내는
거래를 막는 지갑 네이티브 정책은 아니며, 실제 서명·전송은 MetaMask 확인창에서 사용자가 결정한다.

### MetaMask Agent Wallet 실행 어댑터

공식 Agent Wallet CLI가 준비된 환경에서는 실행 게이트의 `send`에
`createAgentWalletCliSender()`를 주입한다. 어댑터는 먼저 `mm wallet address`를 호출해 활성 지갑이
검증된 delegation의 delegate와 정확히 같은지 확인하고, 이후에만 chain/to/value/data/gas/nonce가
고정된 raw transaction을 `mm wallet send-transaction --wait`로 한 번 전달한다. 트랜잭션 해시가
하나로 확인되지 않으면 성공으로 처리하지 않는다.

실제 실행 진입점은 승인 envelope와 서명된 delegation을 포함한 `delegated-floor-candidate`를
다음 wrapper로 묶어 입력받는다. 파서는 중첩 객체의 누락·추가 필드까지 거부한다.

고정 메인넷 포크의 실제 EIP-712 서명·`redeemDelegations` 재현과 공개 Sepolia Agent Wallet
경계는 [`docs/conditional-signed-delegation-test-results-ko.md`](docs/conditional-signed-delegation-test-results-ko.md)에
부분 검증된 조건부 증거로 분리해 기록한다. 로컬 포크 성공을 원격 Agent Wallet 서명 위임 성공으로 해석하지 않는다.

```json
{
  "schemaVersion": 1,
  "kind": "agent-wallet-execution-bundle",
  "approval": { "...": "approved-policy-envelope" },
  "candidate": { "...": "delegated-floor-candidate" }
}
```

먼저 전송 없는 사전 검증을 수행한다. 대상 RPC에서 정확한 outer transaction을 `eth_call`하고,
최신 block hash·delegate nonce·delegator token balance가 candidate와 같은지 다시 확인한다.

```powershell
$env:AGENT_WALLET_RPC_URL = "<target chain RPC>"
cd chain
npm run agent-wallet:execute -- --bundle <bundle.json>
```

사전 검증 결과가 `eligibleForBroadcast=true`인 동일 bundle에만 명시적으로 `--broadcast`를 붙인다.
그때만 활성 Agent Wallet 주소 대조와 `mm wallet send-transaction --wait`가 실행된다. 반환된 해시는
RPC 영수증·from/to/calldata/value/gas/nonce 및 영수증 블록의 사후 토큰 잔고와 다시 대조된다.

```powershell
npm run agent-wallet:execute -- --bundle <bundle.json> --broadcast
```

```powershell
npm install -g @metamask/agent-wallet@latest
mm login browser
mm init
mm doctor --json
```

CLI 설치, bundle 사전 검증, 코드 테스트는 실제 자금 이동 증거가 아니다. 원격 실행 완료를 주장하려면
실제 로그인된 동일 지갑 주소, 대상 체인, 서명된 delegation, 라이브 candidate와 최종 영수증이 필요하다.

서명된 delegation을 아직 준비하지 않은 Agent Wallet 자체 잔고에 대해서는 별도의 direct
`assetBalanceFloor` 경로를 사용할 수 있다. 이 경로는 승인된 wallet을 transaction sender로,
승인된 token을 transaction target으로 고정하고 ERC-20 `transfer` calldata의 수취인·수량과
시뮬레이션된 사후 잔액을 직접 결합한다. 블록이 전진하더라도 pending nonce와 token balance가
같아야 하며, 통과한 exact request만 Agent Wallet CLI에 전달한다.

```powershell
uv run --env-file .env --project verifier python -B research\build_agent_wallet_direct_bundle.py `
  research\evidence\agent-wallet\direct-floor-bundle.json --overwrite

$env:AGENT_WALLET_RPC_URL = "https://ethereum-sepolia-rpc.publicnode.com"
cd chain
npm run agent-wallet:direct -- --bundle ..\research\evidence\agent-wallet\direct-floor-bundle.json
npm run agent-wallet:direct -- --bundle ..\research\evidence\agent-wallet\direct-floor-bundle.json --broadcast
```

기본 연구 bundle은 Ethereum Sepolia의 Circle USDC만 대상으로 1 USDC 잔고에서 0.5 USDC
하한을 유지하며 0.1 USDC를 전송하도록 제한된다. `--broadcast`는 Agent Wallet의 독립적인
정책과 MFA를 우회하지 않는다. MFA 대기는 거래 제출 성공이나 온체인 브로드캐스트 증거가 아니다.

2026-09-04 실행에서는 해당 bundle이 실제 Agent Wallet Guard Mode와 이메일 MFA를 통과해
Sepolia 거래 `0xaf7566c59d0b10c3983f2478088ac31df165b1acaf1b6084acacd96d08d4f500`으로
브로드캐스트됐다. 블록 `11628030`의 성공 영수증, 정확한 transaction 필드와 Transfer 이벤트,
사후 잔액 0.9 USDC를 검증했다. 이는 application-level floor guard와 Agent Wallet 전송의
end-to-end 증거이지만, Agent Wallet 네이티브 floor 정책이나 서명된 delegation redemption 증거는 아니다.

같은 승인 정책과 Agent Wallet에서 0.9 USDC 중 0.5 USDC를 전송해 사후 잔액이 0.4 USDC가
되는 대응 후보도 실행했다. RPC 시뮬레이션 뒤 `ASSET_BALANCE_FLOOR_VIOLATION`으로 거부됐고,
`--broadcast` 호출에서도 Agent Wallet 요청 ID가 추가되지 않았으며 거래 해시는 생성되지 않았다.

## 설계 원칙

LLM은 **컴파일러이지 심판이 아니다.** 의도 → 불변식 변환은 실행 전 1회,
사용자 승인을 거친다. 거래 판정은 매 거래 결정론적 검증기가 한다. 실행 시점에
LLM이 개입하면 검증자도 같은 프롬프트 인젝션 표면을 갖는다.

## 시작하기

```powershell
Copy-Item .env.example .env
uv run --env-file .env --cache-dir tmp\uv-cache --project verifier python ui\server.py
```

`.env`는 커밋하지 않는다.

## 현재 검증 가능한 세로 슬라이스

Phase 1의 누적 손실 트레이스를 Phase 3 결정론적 평가기의 골든 네거티브 입력으로 사용한다.
데모 정책은 실제 사용자 정책이 아니며, 고정된 Phase 1 파라미터로 평가 경로를 재현하기 위한
fixture다. 기준과 남은 범위는 `docs/phase3-acceptance.md`에 있다.

```powershell
uv run --cache-dir tmp\uv-cache --project verifier python verifier\validate_trace.py traces\cumulative-loss.json --quiet
uv run --cache-dir tmp\uv-cache --project verifier python -m unittest discover -s verifier -p 'test_*.py' -v
uv run --cache-dir tmp\uv-cache --project verifier python verifier\evaluate_invariants.py `
  specs\phase1-demo-invariants.json traces\cumulative-loss.json --expect reject
```

실제 통합에서는 `--expect`의 기본값인 `accept`를 유지한다. 차단된 거래와 유효하지 않은
입력은 성공 코드로 처리되지 않는다.

## MVP 후보 상태 판정

MVP 범위는 `docs/mvp-scope.md`에 고정했다. 현재 후보 상태 슬라이스는 검증된 G3의 마지막
실행과 직전 24시간 이력을 strict JSON으로 변환해 같은 두 불변식으로 차단한다.

```powershell
uv run --cache-dir tmp\uv-cache --project verifier python verifier\evaluate_candidate.py `
  specs\mvp-candidate-invariants.json traces\mvp-candidate-reject.json --expect reject
```

이 정책은 재현용 demo fixture이며 사용자 승인 증거가 아니다. 제한된 자연어 합성과 명시적
승인 분리는 provider-neutral artifact 흐름으로 구현되어 있다.

```powershell
uv run --cache-dir tmp\uv-cache --project verifier python synth\create_request.py `
  specs\mvp-intent.ko.txt specs\mvp-candidate-invariants.json tmp\intent-request.json

uv run --cache-dir tmp\uv-cache --project verifier python synth\compile_response.py `
  specs\mvp-intent-request.json specs\mvp-llm-response.fixture.json tmp\policy-proposal.json

uv run --cache-dir tmp\uv-cache --project verifier python verifier\evaluate_approved_candidate.py `
  specs\mvp-user-policy-approval.json traces\mvp-candidate-reject.json --expect reject
```

`mvp-llm-response.fixture.json`은 오프라인 계약 테스트이며 실제 모델 호출 증거가 아니다.
`mvp-user-policy-approval.json`은 사용자가 제안 해시와 정확히 같은 확인 문구를 제공한 뒤
생성된 승인 기록이다. `approvedBy`는 인증 신원이 아닌 로컬 감사 라벨이다.
