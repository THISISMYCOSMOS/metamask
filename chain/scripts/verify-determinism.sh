#!/usr/bin/env bash
# G1 결정론 2회 실행 오케스트레이터.
#
#   bash chain/scripts/verify-determinism.sh
#
# reproduce.sh를 서로 다른 포트로 두 번 돌려(기본 8545 / 8546, DETERMINISM_PORT_1 /
# DETERMINISM_PORT_2로 재지정 가능) 각 실행의 표준출력과 SNAPSHOT_OUT 파일을 임시
# 디렉터리에 남기고, determinism-report.ts에 넘겨 traces/determinism.json을 만든다.
# 하나라도 어긋나면 fail closed(exit != 0)한다.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PORT1="${DETERMINISM_PORT_1:-8545}"
PORT2="${DETERMINISM_PORT_2:-8546}"

if [ "$PORT1" = "$PORT2" ]; then
  echo "DETERMINISM_PORT_1과 DETERMINISM_PORT_2는 달라야 한다." >&2
  exit 1
fi

export PATH="$HOME/.foundry/bin:$PATH"

WORKDIR="$(mktemp -d -t determinism.XXXXXX)"
cleanup() {
  case "$WORKDIR" in
    */determinism.*) rm -rf -- "$WORKDIR" ;;
    *) echo "예상하지 못한 임시경로라 삭제하지 않는다: $WORKDIR" >&2 ;;
  esac
}
trap cleanup EXIT

RUN1_STDOUT="$WORKDIR/run1.stdout.log"
RUN2_STDOUT="$WORKDIR/run2.stdout.log"
RUN1_SNAPSHOT="$WORKDIR/run1.snapshot.json"
RUN2_SNAPSHOT="$WORKDIR/run2.snapshot.json"

echo "[verify-determinism] 실행 1/2 (포트 ${PORT1})"
ANVIL_PORT="$PORT1" SNAPSHOT_OUT="$RUN1_SNAPSHOT" \
  bash "$REPO_ROOT/chain/scripts/reproduce.sh" > "$RUN1_STDOUT" 2>&1
cat "$RUN1_STDOUT"

echo "[verify-determinism] 실행 2/2 (포트 ${PORT2})"
ANVIL_PORT="$PORT2" SNAPSHOT_OUT="$RUN2_SNAPSHOT" \
  bash "$REPO_ROOT/chain/scripts/reproduce.sh" > "$RUN2_STDOUT" 2>&1
cat "$RUN2_STDOUT"

cd "$REPO_ROOT/chain"
npx tsx src/determinism-report.ts "$RUN1_STDOUT" "$RUN1_SNAPSHOT" "$RUN2_STDOUT" "$RUN2_SNAPSHOT"
