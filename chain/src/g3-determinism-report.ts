// G3 — 결정론 2회 실행 리포터.
//
// verify-g3-determinism.sh가 reproduce-g3.sh를 서로 다른 포트로 두 번 돌려 각 실행의
// 표준출력과 traces/cumulative-loss.json 사본(G3_TRACE_OUT)을 남긴다. 이 스크립트는
//   1) 표준출력에서 뽑은 G3_* 필드 전부가 두 실행에서 같은지,
//   2) 각 트레이스의 `hashed`만 정본 정렬 직렬화한 결과가 바이트 단위로 같은지
// 를 검사하고, 하나라도 어긋나면 fail closed로 종료한다(exit 1). `meta`는 벽시계 시각뿐이므로
// 비교에서 제외한다.
//
// 절대 임시경로를 기록하지 않는다 — 파일명(basename)만 남긴다. 비밀값도 남기지 않는다.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { keccak256, toHex } from "viem";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..", "..");
const OUT_PATH = join(REPO_ROOT, "traces", "g3-determinism.json");

// cumulative-loss.ts가 표준출력 마지막에 찍는 정본 필드 전부.
const FIELD_NAMES = [
  "G3_STEP_COUNT",
  "G3_DELEGATION_HASH",
  "G3_END_USDC",
  "G3_PERIOD_DISTRIBUTION",
  "G3_PORTFOLIO_START",
  "G3_PORTFOLIO_END",
  "G3_LOSS",
  "G3_LOSS_BPS",
  "G3_TRACE_DIGEST",
] as const;
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

/** 키를 재귀적으로 정렬해 JSON 직렬화를 정본화한다. state-digest.ts/cumulative-loss.ts와 같은 규약. */
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(value as Record<string, unknown>).sort()) {
      out[k] = canonical((value as Record<string, unknown>)[k]);
    }
    return out;
  }
  if (typeof value === "bigint") return value.toString();
  return value;
}

/** 트레이스 JSON을 읽어 hashed만 정본 직렬화한다. meta(벽시계 시각)는 제외한다. */
function canonicalHashedJson(tracePath: string): string {
  const parsed = JSON.parse(readFileSync(tracePath, "utf8"));
  if (!parsed.hashed) {
    throw new Error(`${tracePath}: hashed 필드가 없다`);
  }
  return JSON.stringify(canonical(parsed.hashed));
}

interface RunArgs {
  label: string;
  stdoutPath: string;
  tracePath: string;
}

function parseArgs(argv: string[]): [RunArgs, RunArgs] {
  // 사용법: g3-determinism-report.ts <run1_stdout> <run1_trace> <run2_stdout> <run2_trace>
  if (argv.length !== 4) {
    throw new Error("사용법: g3-determinism-report.ts <run1_stdout> <run1_trace> <run2_stdout> <run2_trace>");
  }
  const [s1, t1, s2, t2] = argv;
  return [
    { label: "run1", stdoutPath: s1, tracePath: t1 },
    { label: "run2", stdoutPath: s2, tracePath: t2 },
  ];
}

function main(): void {
  const [run1, run2] = parseArgs(process.argv.slice(2));

  const raw1 = readFileSync(run1.stdoutPath, "utf8");
  const raw2 = readFileSync(run2.stdoutPath, "utf8");

  const fields1 = extractFields(raw1);
  const fields2 = extractFields(raw2);

  const fieldMismatches: string[] = [];
  for (const name of FIELD_NAMES) {
    if (fields1[name] !== fields2[name]) {
      fieldMismatches.push(`${name}: run1=${fields1[name]} run2=${fields2[name]}`);
    }
  }

  const canonical1 = canonicalHashedJson(run1.tracePath);
  const canonical2 = canonicalHashedJson(run2.tracePath);
  const snapshotsIdentical = canonical1 === canonical2;

  const digest1 = keccak256(toHex(canonical1));
  const digest2 = keccak256(toHex(canonical2));

  const passed = fieldMismatches.length === 0 && snapshotsIdentical;

  const report = {
    schemaVersion: 1,
    kind: "g3-determinism-report",
    passed,
    digests: { run1: digest1, run2: digest2 },
    snapshotsIdentical,
    fields: { run1: fields1, run2: fields2 },
    fieldMismatches,
    // 절대 임시경로는 기록하지 않는다 — 파일명만.
    traceFileNames: { run1: basename(run1.tracePath), run2: basename(run2.tracePath) },
  };

  mkdirSync(dirname(OUT_PATH), { recursive: true });
  writeFileSync(OUT_PATH, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  console.log(`[g3-determinism-report] 기록: ${OUT_PATH}`);

  if (!passed) {
    console.error("[g3-determinism-report] G3 결정론 불일치:");
    if (!snapshotsIdentical) console.error("  - hashed 정본 직렬화가 바이트 단위로 다르다");
    for (const m of fieldMismatches) console.error(`  - ${m}`);
    process.exit(1);
  }
  console.log("[g3-determinism-report] 통과 — 두 실행의 트레이스/필드가 일치한다");
}

main();
