#!/usr/bin/env bash
# Phase 1 전체 재현 — G2와 G3를 각각 독립된 fresh fork에서 순차 재생성한다.
#
#   bash chain/scripts/reproduce-phase1.sh
#
# 두 단계는 서로 다른 포트에서 각자 자기 anvil을 새로 띄우고 끝나면 내린다 — 한 단계의
# 온체인 상태(PC0 잔고, period 소진량 등)가 다른 단계로 새어 들어가지 않는다. G2와 G3는
# delegation도 서로 다르다(같은 caveat 6종·파라미터지만 각자 새로 서명한다).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
G2_PORT="${PHASE1_G2_PORT:-8545}"
G3_PORT="${PHASE1_G3_PORT:-8547}"

if [ "$G2_PORT" = "$G3_PORT" ]; then
  echo "PHASE1_G2_PORT과 PHASE1_G3_PORT는 달라야 한다 (G2/G3 상태가 섞이면 안 된다)." >&2
  exit 1
fi

echo "[reproduce-phase1] 1/2 — G2 (baseline 생존 증명), 포트 ${G2_PORT}"
ANVIL_PORT="$G2_PORT" bash "$REPO_ROOT/chain/scripts/reproduce.sh"

echo "[reproduce-phase1] 2/2 — G3 (누적 손실 트레이스), 포트 ${G3_PORT}"
G3_PORT="$G3_PORT" bash "$REPO_ROOT/chain/scripts/reproduce-g3.sh"

echo "[reproduce-phase1] 완료"
echo "  G2 트레이스: $REPO_ROOT/traces/negative-control.json"
echo "  G3 트레이스: $REPO_ROOT/traces/cumulative-loss.json"
