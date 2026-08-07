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
