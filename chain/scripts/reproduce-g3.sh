#!/usr/bin/env bash
# G3 재현 스크립트 — 한 명령으로 새 포크 위에 배포 + 누적 손실 트레이스를 재생성하고
# Pydantic 검증기로 트레이스를 검증한다.
#
#   bash chain/scripts/reproduce-g3.sh
#
# G2의 PC0 상태와 절대 섞이지 않는다 — 이 스크립트는 자기 anvil을 새로 띄우고
# negative-control.ts를 실행하지 않는다. 포트도 G2(8545/8546)와 분리된 8547을 기본값으로 쓴다.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PORT="${G3_PORT:-8547}"
RPC="http://127.0.0.1:${PORT}"
FORK_BLOCK=25700000

export PATH="$HOME/.foundry/bin:$PATH"

# RPC_URL은 포크 소스 전용이다. 부모 셸에서 export돼 있던 속성까지 제거한 뒤,
# .env 로드는 서브셸에 격리하고 비-export 변수에 포크 URL만 담는다.
unset RPC_URL
FORK_RPC_URL="$({
  set +x
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  printf '%s' "${RPC_URL:-}"
})"
if [ -z "${FORK_RPC_URL:-}" ]; then
  echo "RPC_URL이 .env에 없다 (아카이브 노드 필요)" >&2
  exit 1
fi

ANVIL_LOG="$(mktemp -t anvil-g3.XXXXXX.log)"

cleanup() {
  if [ -n "${ANVIL_PID:-}" ]; then
    kill "$ANVIL_PID" 2>/dev/null || true
    wait "$ANVIL_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# 기존 anvil이 같은 포트를 물고 있으면 결정론이 깨진다 (이전 실행 상태가 남는다).
if cast block-number --rpc-url "$RPC" >/dev/null 2>&1; then
  echo "포트 ${PORT}에 이미 노드가 떠 있다. 먼저 내려라." >&2
  exit 1
fi

echo "[reproduce-g3] anvil 시작 (포크 블록 ${FORK_BLOCK}, 포트 ${PORT})"
anvil --fork-url "$FORK_RPC_URL" \
      --fork-block-number "$FORK_BLOCK" \
      --port "$PORT" --host 127.0.0.1 \
      --order fifo --chain-id 1 \
      > "$ANVIL_LOG" 2>&1 &
ANVIL_PID=$!

for _ in $(seq 1 60); do
  if cast block-number --rpc-url "$RPC" >/dev/null 2>&1; then break; fi
  sleep 1
done
if ! cast block-number --rpc-url "$RPC" >/dev/null 2>&1; then
  echo "[reproduce-g3] anvil이 뜨지 않았다. 로그: $ANVIL_LOG" >&2
  exit 1
fi

cd "$REPO_ROOT/chain"
ANVIL_RPC="$RPC" npx tsx src/deploy.ts

G3_LOG="$(mktemp -t g3-run.XXXXXX.log)"
ANVIL_RPC="$RPC" G3_TRACE_OUT="${G3_TRACE_OUT:-}" npx tsx src/cumulative-loss.ts | tee "$G3_LOG"

echo "[reproduce-g3] 트레이스 검증기 실행"
if ! command -v uv >/dev/null 2>&1; then
  echo "uv가 없다 — verifier/를 실행할 수 없다. https://docs.astral.sh/uv/ 참고." >&2
  exit 1
fi
uv run --project "$REPO_ROOT/verifier" python "$REPO_ROOT/verifier/validate_trace.py" "$REPO_ROOT/traces/cumulative-loss.json"

echo "[reproduce-g3] 결정론 요약 필드"
grep '^G3_' "$G3_LOG"
