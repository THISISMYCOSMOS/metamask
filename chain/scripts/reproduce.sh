#!/usr/bin/env bash
# Phase 1 재현 스크립트 — 한 명령으로 배포 + G2 트레이스를 재생성하고 최종 상태 해시를 찍는다.
#
#   bash chain/scripts/reproduce.sh
#
# G1의 증거는 이 스크립트를 두 번 돌렸을 때 마지막 줄의 stateRoot가 같다는 것이다.
# anvil이 실제 머클 상태 루트를 계산하므로, 손수 만든 다이제스트보다 강한 증거다.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PORT="${ANVIL_PORT:-8545}"
RPC="http://127.0.0.1:${PORT}"
FORK_BLOCK=25700000

export PATH="$HOME/.foundry/bin:$PATH"

# RPC_URL은 포크 소스 전용이다. anvil --fork-url 외 어디에도 넘기지 않는다.
set -a
# shellcheck disable=SC1091
source "$REPO_ROOT/.env"
set +a
if [ -z "${RPC_URL:-}" ]; then
  echo "RPC_URL이 .env에 없다 (아카이브 노드 필요)" >&2
  exit 1
fi

ANVIL_LOG="$(mktemp -t anvil.XXXXXX.log)"

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

echo "[reproduce] anvil 시작 (포크 블록 ${FORK_BLOCK})"
anvil --fork-url "$RPC_URL" \
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
  echo "[reproduce] anvil이 뜨지 않았다. 로그: $ANVIL_LOG" >&2
  exit 1
fi

cd "$REPO_ROOT/chain"
ANVIL_RPC="$RPC" npx tsx src/deploy.ts
ANVIL_RPC="$RPC" npx tsx src/negative-control.ts

ANVIL_RPC="$RPC" SNAPSHOT_OUT="${SNAPSHOT_OUT:-}" npx tsx src/state-digest.ts
