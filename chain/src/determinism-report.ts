// G1 — 결정론 2회 실행 리포터.
//
// verify-determinism.sh가 reproduce.sh를 서로 다른 포트로 두 번 돌려 각 실행의
// 표준출력과 SNAPSHOT_OUT 파일을 남긴다. 이 스크립트는 그 산출물을 받아
//   1) 두 SNAPSHOT_OUT 파일이 바이트 단위로 동일한지,
//   2) 표준출력에서 뽑은 FINAL_BLOCK / DELEGATION_HASH / PERIOD_CONSUMED / STATE_DIGEST
//      네 필드가 두 실행에서 같은지
// 를 검사하고, 하나라도 어긋나면 fail closed로 종료한다(exit 1).
// 통과하면 traces/determinism.json에 두 실행의 원본 출력과 스냅샷 SHA-256을 남긴다.
//
// 벽시계 시각·소요시간·절대 임시경로·비밀값은 이 파일이 쓰는 산출물에 넣지 않는다.
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..", "..");
const OUT_PATH = join(REPO_ROOT, "traces", "determinism.json");

/** raw reproduce.sh 표준출력에서 정본 필드 4개를 뽑는다. */
const FIELD_NAMES = ["FINAL_BLOCK", "DELEGATION_HASH", "PERIOD_CONSUMED", "STATE_DIGEST"] as const;
type FieldName = (typeof FIELD_NAMES)[number];

function extractFields(raw: string): Record<FieldName, string> {
  const found: Partial<Record<FieldName, string>> = {};
  for (const line of raw.split(/\r?\n/)) {
    for (const name of FIELD_NAMES) {
      const prefix = `${name}=`;
      if (line.startsWith(prefix)) {
        found[name] = line.slice(prefix.length).trim();
      }
    }
  }
  const missing = FIELD_NAMES.filter((n) => found[n] === undefined);
  if (missing.length > 0) {
    throw new Error(`출력에서 다음 필드를 찾지 못했다: ${missing.join(", ")}`);
  }
  return found as Record<FieldName, string>;
}

function sha256Hex(content: string): string {
  return `0x${createHash("sha256").update(content, "utf8").digest("hex")}`;
}

function rawDigestOutput(fields: Record<FieldName, string>): string[] {
  return FIELD_NAMES.map((name) => `${name}=${fields[name]}`);
}

interface RunArgs {
  label: string;
  stdoutPath: string;
  snapshotPath: string;
}

function parseArgs(argv: string[]): [RunArgs, RunArgs] {
  // 사용법: determinism-report.ts <run1_stdout> <run1_snapshot> <run2_stdout> <run2_snapshot>
  if (argv.length !== 4) {
    throw new Error(
      "사용법: determinism-report.ts <run1_stdout> <run1_snapshot> <run2_stdout> <run2_snapshot>",
    );
  }
  const [s1, n1, s2, n2] = argv;
  return [
    { label: "run1", stdoutPath: s1, snapshotPath: n1 },
    { label: "run2", stdoutPath: s2, snapshotPath: n2 },
  ];
}

function main(): void {
  const [run1, run2] = parseArgs(process.argv.slice(2));

  const raw1 = readFileSync(run1.stdoutPath, "utf8");
  const raw2 = readFileSync(run2.stdoutPath, "utf8");
  const snapshot1 = readFileSync(run1.snapshotPath, "utf8");
  const snapshot2 = readFileSync(run2.snapshotPath, "utf8");

  const fields1 = extractFields(raw1);
  const fields2 = extractFields(raw2);

  const fieldMismatches: string[] = [];
  for (const name of FIELD_NAMES) {
    if (fields1[name] !== fields2[name]) {
      fieldMismatches.push(`${name}: run1=${fields1[name]} run2=${fields2[name]}`);
    }
  }

  const snapshotsIdentical = snapshot1 === snapshot2;
  const snapshotSha256 = { run1: sha256Hex(snapshot1), run2: sha256Hex(snapshot2) };

  const passed = fieldMismatches.length === 0 && snapshotsIdentical;

  const report = {
    schemaVersion: 1,
    kind: "determinism-report",
    passed,
    fields: { run1: fields1, run2: fields2 },
    fieldMismatches,
    snapshotsIdentical,
    snapshotSha256,
    // 파일명만 기록한다 — 절대 임시경로는 넣지 않는다.
    snapshotFileNames: { run1: basename(run1.snapshotPath), run2: basename(run2.snapshotPath) },
    // 전체 stdout에는 머신별 절대경로와 npm 진단이 섞일 수 있다. 검증 정본 4줄만 보존한다.
    rawDigestOutput: { run1: rawDigestOutput(fields1), run2: rawDigestOutput(fields2) },
  };

  mkdirSync(dirname(OUT_PATH), { recursive: true });
  writeFileSync(OUT_PATH, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  console.log(`[determinism-report] 기록: ${OUT_PATH}`);

  if (!passed) {
    console.error("[determinism-report] G1 불일치:");
    if (!snapshotsIdentical) console.error("  - SNAPSHOT_OUT 스냅샷이 바이트 단위로 다르다");
    for (const m of fieldMismatches) console.error(`  - ${m}`);
    process.exit(1);
  }
  console.log("[determinism-report] G1 통과 — 두 실행의 스냅샷/필드가 일치한다");
}

main();
