#!/usr/bin/env bash
# G3 결정론 2회 실행 오케스트레이터.
#
#   bash chain/scripts/verify-g3-determinism.sh
#
# reproduce-g3.sh를 서로 다른 포트로 두 번 돌려(기본 8547 / 8548, G3_DETERMINISM_PORT_1 /
# G3_DETERMINISM_PORT_2로 재지정 가능) 각 실행의 표준출력과 트레이스 사본을 임시 디렉터리에
# 남기고, g3-determinism-report.ts에 넘겨 traces/g3-determinism.json을 만든다.
# 하나라도 어긋나면 fail closed(exit != 0)한다.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PORT1="${G3_DETERMINISM_PORT_1:-8547}"
PORT2="${G3_DETERMINISM_PORT_2:-8548}"

if [ "$PORT1" = "$PORT2" ]; then
  echo "G3_DETERMINISM_PORT_1과 G3_DETERMINISM_PORT_2는 달라야 한다." >&2
  exit 1
fi

export PATH="$HOME/.foundry/bin:$PATH"

WORKDIR="$(mktemp -d -t g3-determinism.XXXXXX)"
cleanup() {
  case "$WORKDIR" in
    */g3-determinism.*) rm -rf -- "$WORKDIR" ;;
    *) echo "예상하지 못한 임시경로라 삭제하지 않는다: $WORKDIR" >&2 ;;
  esac
}
trap cleanup EXIT

RUN1_STDOUT="$WORKDIR/run1.stdout.log"
RUN2_STDOUT="$WORKDIR/run2.stdout.log"
RUN1_TRACE="$WORKDIR/run1.cumulative-loss.json"
RUN2_TRACE="$WORKDIR/run2.cumulative-loss.json"

echo "[verify-g3-determinism] 실행 1/2 (포트 ${PORT1})"
G3_PORT="$PORT1" G3_TRACE_OUT="$RUN1_TRACE" \
  bash "$REPO_ROOT/chain/scripts/reproduce-g3.sh" > "$RUN1_STDOUT" 2>&1
cat "$RUN1_STDOUT"

echo "[verify-g3-determinism] 실행 2/2 (포트 ${PORT2})"
G3_PORT="$PORT2" G3_TRACE_OUT="$RUN2_TRACE" \
  bash "$REPO_ROOT/chain/scripts/reproduce-g3.sh" > "$RUN2_STDOUT" 2>&1
cat "$RUN2_STDOUT"

cd "$REPO_ROOT/chain"
npx tsx src/g3-determinism-report.ts "$RUN1_STDOUT" "$RUN1_TRACE" "$RUN2_STDOUT" "$RUN2_TRACE"
