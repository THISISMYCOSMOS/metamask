# Intent as Specification

LLM 기반 불변식 합성과 실행 직전 검증을 통한 에이전트 거래 보안.

권한(authorization)의 세분화로는 의도(intent)를 표현할 수 없다. 한도와 허용목록을
아무리 촘촘히 해도 그 교집합 안에 사용자에게 손해인 경로가 남는다. 방어는 허용할
호출을 좁히는 방향이 아니라 **허용할 결과 상태를 명세하는 방향**으로 이동해야 한다.

## 구조

| 디렉터리 | 언어 | 역할 |
| --- | --- | --- |
| `chain/` | TS (viem + anvil) | 포크 환경, 위임 실행 경로, 상태 스냅샷 → trace JSON |
| `verifier/` | Python | 불변식 스키마 + 결정론적 평가기 |
| `synth/` | Python | 자연어 의도 → 불변식 JSON 합성 |
| `specs/` | Solidity interface | 결과 기반 enforcer 인터페이스 명세 |
| `traces/` | JSON | 실행 트레이스 (커밋 대상 산출물) |
| `docs/` | Markdown | baseline 구성, 페이즈별 합격 기준 |

`chain/`과 `verifier/`의 유일한 접점은 `traces/*.json` 이다. 불변식 스키마는
`verifier/`에 단일 정의하고 `synth/`가 같은 스키마를 공유한다.

## 설계 원칙

LLM은 **컴파일러이지 심판이 아니다.** 의도 → 불변식 변환은 실행 전 1회,
사용자 승인을 거친다. 거래 판정은 매 거래 결정론적 검증기가 한다. 실행 시점에
LLM이 개입하면 검증자도 같은 프롬프트 인젝션 표면을 갖는다.

## 시작하기

```bash
cp .env.example .env   # RPC_URL 을 채운다
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
  specs\mvp-test-policy-approval.json traces\mvp-candidate-reject.json `
  --allow-test-fixture --expect reject
```

`mvp-llm-response.fixture.json`은 오프라인 계약 테스트이며 실제 모델 호출 증거가 아니다.
실제 승인은 `synth/approve_policy.py`에서 출력된 제안 해시와 정확히 같은 확인 문구를 사용자가
직접 제공해야 생성된다.
