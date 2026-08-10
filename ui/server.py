#!/usr/bin/env python3
import argparse
import copy
import json
import os
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_DIR = Path(__file__).resolve().parent
TRACE_PATH = REPO_ROOT / "traces" / "cumulative-loss.json"
VERIFIER_DIR = REPO_ROOT / "verifier"
sys.path.insert(0, str(VERIFIER_DIR))

from invariant_models import CANONICAL_INVARIANT_KINDS, InvariantPolicy  # noqa: E402
from candidate_models import CandidateTrace  # noqa: E402
from evaluate_approved_candidate import evaluate_approved_candidate  # noqa: E402
from evaluate_invariants import EvaluationInputError  # noqa: E402
from synthesis_models import (  # noqa: E402
    LlmPolicyResponse,
    PolicyProposal,
    canonical_model_sha256,
)
from synthesis_workflow import (  # noqa: E402
    STRUCTURED_EDITOR_REQUEST_ID,
    SynthesisInputError,
    approve_policy_proposal,
    build_structured_policy,
    compile_local_structured_proposal,
    compile_policy_proposal,
    create_intent_request,
)

INTENT_PATH = REPO_ROOT / "specs" / "mvp-intent.ko.txt"
POLICY_TEMPLATE_PATH = REPO_ROOT / "specs" / "mvp-candidate-invariants.json"
OFFLINE_RESPONSE_PATH = REPO_ROOT / "specs" / "mvp-llm-response.fixture.json"
CANDIDATE_PATH = REPO_ROOT / "traces" / "mvp-candidate-reject.json"

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}

ALLOWED_COMMANDS = ("status", "validate", "evaluate", "run g3")

MAX_BODY_BYTES = 16384

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

run_lock = threading.Lock()
policy_lock = threading.Lock()
policy_state = None


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_policy_flow(intent_text):
    template = InvariantPolicy.model_validate(_read_json(POLICY_TEMPLATE_PATH))
    request = create_intent_request(
        request_id="mvp-intent-001",
        intent_text=intent_text,
        policy_template=template,
    )
    state = {
        "stage": "request-created",
        "compilerSource": "provider-required",
        "request": request.model_dump(mode="json"),
        "requestSha256": canonical_model_sha256(request),
        "proposal": None,
        "proposalSha256": None,
        "approval": None,
        "approvalSha256": None,
        "candidateEvaluation": None,
        "transaction": {
            "status": "not-created",
            "eligibleForBroadcast": False,
            "reason": "정확한 거래 요청과 실행 바인딩이 아직 생성되지 않았습니다.",
        },
        "logs": ["intent-compiler-request 생성 완료"],
    }

    response = LlmPolicyResponse.model_validate(_read_json(OFFLINE_RESPONSE_PATH))
    if response.requestSha256.lower() != state["requestSha256"]:
        state["logs"].append("현재 입력에 결합된 LLM 응답 없음 — 제안 생성 중단")
        return state
    proposal = compile_policy_proposal(request, response)

    state.update(
        {
            "stage": "proposal-ready",
            "compilerSource": "offline-fixture",
            "proposal": proposal.model_dump(mode="json"),
            "proposalSha256": canonical_model_sha256(proposal),
        }
    )
    state["logs"].extend(
        [
            "오프라인 llm-policy-response 스키마 검증 완료",
            "request/fork/invariant/rationale 결합 검증 완료",
            "미승인 policy-proposal 생성 완료",
        ]
    )
    return state


def build_structured_policy_flow(intent_text, invariants_data):
    if not isinstance(invariants_data, list) or not invariants_data:
        raise SynthesisInputError("at least one structured condition is required")
    if len(invariants_data) > 4:
        raise SynthesisInputError("at most four structured conditions are supported")

    kind_order = {kind: index for index, kind in enumerate(CANONICAL_INVARIANT_KINDS)}
    try:
        ordered = sorted(invariants_data, key=lambda item: kind_order.get(item.get("kind"), len(kind_order)))
    except AttributeError as exc:
        raise SynthesisInputError("each structured condition must be an object with a kind field") from exc

    template = InvariantPolicy.model_validate(_read_json(POLICY_TEMPLATE_PATH))
    probe_policy = build_structured_policy(
        policy_id=template.policyId,
        fork=template.fork,
        invariants=ordered,
    )
    request = create_intent_request(
        request_id=STRUCTURED_EDITOR_REQUEST_ID,
        intent_text=intent_text,
        policy_template=probe_policy,
    )
    proposal = compile_local_structured_proposal(request, probe_policy.invariants)

    return {
        "stage": "proposal-ready",
        "compilerSource": "local-structured-editor",
        "request": request.model_dump(mode="json"),
        "requestSha256": canonical_model_sha256(request),
        "proposal": proposal.model_dump(mode="json"),
        "proposalSha256": canonical_model_sha256(proposal),
        "approval": None,
        "approvalSha256": None,
        "candidateEvaluation": None,
        "transaction": {
            "status": "not-created",
            "eligibleForBroadcast": False,
            "reason": "정확한 거래 요청과 실행 바인딩이 아직 생성되지 않았습니다.",
        },
        "logs": [
            "구조화된 조건 편집기에서 intent-compiler-request 생성 완료",
            "로컬 구조화 편집기 컴파일 완료 — 실시간 LLM 호출 없음",
            "미승인 policy-proposal 생성 완료 (compilerSource=local-structured-editor)",
            "이전 승인·후보 평가가 있었다면 새 제안으로 무효화됨",
        ],
    }


def replace_structured_policy_flow(intent_text, invariants_data):
    global policy_state
    next_state = build_structured_policy_flow(intent_text, invariants_data)
    with policy_lock:
        policy_state = next_state
        return copy.deepcopy(policy_state)


def current_policy_flow():
    global policy_state
    with policy_lock:
        if policy_state is None:
            policy_state = build_policy_flow(INTENT_PATH.read_text(encoding="utf-8").strip())
        return copy.deepcopy(policy_state)


def replace_policy_flow(intent_text):
    global policy_state
    next_state = build_policy_flow(intent_text)
    with policy_lock:
        policy_state = next_state
        return copy.deepcopy(policy_state)


def approve_current_policy(confirmation):
    global policy_state
    with policy_lock:
        if policy_state is None or policy_state.get("proposal") is None:
            raise SynthesisInputError("승인할 정책 제안이 없습니다")
        proposal = PolicyProposal.model_validate(policy_state["proposal"])
        # approve_policy_proposal raises SynthesisInputError (HTTP 422) on a
        # mismatched confirmation hash; that is the only approval failure.
        # An approval that hashes correctly must be recorded even if the
        # downstream candidate evaluation input later turns out to be invalid.
        approval = approve_policy_proposal(
            proposal,
            confirmation=confirmation,
            approved_by="user",
            approval_scope="user",
        )
        policy_state["stage"] = "approved"
        policy_state["approval"] = approval.model_dump(mode="json")
        policy_state["approvalSha256"] = canonical_model_sha256(approval)
        policy_state["logs"].append("정확한 proposalSha256 사용자 승인 기록 완료")

        candidate = CandidateTrace.model_validate(_read_json(CANDIDATE_PATH))
        try:
            evaluation = evaluate_approved_candidate(approval, candidate)
        except EvaluationInputError as exc:
            policy_state["candidateEvaluation"] = {
                "status": "evaluation-invalid",
                "reason": str(exc),
            }
            policy_state["logs"].append(f"승인 기록 완료 — 후보 평가 입력 무효: {exc}")
            policy_state["logs"].append("거래 요청/실행 바인딩 미생성 — 브로드캐스트 불가")
            return copy.deepcopy(policy_state)

        policy_state["candidateEvaluation"] = {**evaluation, "status": "evaluated"}
        policy_state["logs"].append("승인된 정책으로 G3 후보 결정론 평가 완료")
        accepted_label = "true — 정책 승인" if evaluation["accepted"] else "false — 정책 거절"
        policy_state["logs"].append(f"candidate accepted={accepted_label}")
        policy_state["logs"].append("거래 요청/실행 바인딩 미생성 — 브로드캐스트 불가")
        return copy.deepcopy(policy_state)


def build_argv(name):
    if name == "validate":
        return [
            "uv", "run", "--cache-dir", "tmp/uv-cache", "--project", "verifier",
            "python", "verifier/validate_trace.py", "traces/cumulative-loss.json",
        ]
    if name == "evaluate":
        return [
            "uv", "run", "--cache-dir", "tmp/uv-cache", "--project", "verifier",
            "python", "verifier/evaluate_invariants.py",
            "specs/phase1-demo-invariants.json", "traces/cumulative-loss.json",
            "--expect", "reject",
        ]
    if name == "run g3":
        if os.name == "nt":
            program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            bash_exe = program_files / "Git" / "bin" / "bash.exe"
            if not bash_exe.exists():
                return None
            script = 'export PATH="$HOME/.foundry/bin:$PATH"; bash chain/scripts/reproduce-g3.sh'
            return [str(bash_exe), "--noprofile", "--norc", "-lc", script]
        return ["bash", "chain/scripts/reproduce-g3.sh"]
    return None


def child_env():
    env = dict(os.environ)
    env.setdefault("G3_PORT", "18550")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class Handler(BaseHTTPRequestHandler):
    server_version = "TraceUI/1.0"

    def log_message(self, format, *args):
        pass

    def _has_local_host(self):
        host = self.headers.get("Host", "").split(":", 1)[0].lower()
        return host in {"127.0.0.1", "localhost"}

    def _has_local_origin(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        hostname = urllib.parse.urlparse(origin).hostname
        return (hostname or "").lower() in {"127.0.0.1", "localhost"}

    def _send_json(self, status, payload, no_store=False):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if no_store:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        if not self._has_local_host():
            self._send_json(403, {"error": "invalid host"})
            return
        if self.path == "/api/trace":
            self._handle_trace()
            return
        if self.path == "/api/policy":
            try:
                self._send_json(200, current_policy_flow(), no_store=True)
            except Exception:
                self._send_json(500, {"error": "policy state unavailable"}, no_store=True)
            return
        entry = STATIC_FILES.get(self.path)
        if entry is None:
            self._send_json(404, {"error": "not found"})
            return
        filename, mime = entry
        file_path = UI_DIR / filename
        try:
            data = file_path.read_bytes()
        except OSError:
            self._send_json(404, {"error": "not found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def _handle_trace(self):
        try:
            raw = TRACE_PATH.read_text(encoding="utf-8")
        except OSError:
            self._send_json(404, {"error": "trace not found"}, no_store=True)
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self._send_json(422, {"error": "invalid trace json"}, no_store=True)
            return
        self._send_json(200, data, no_store=True)

    def _read_json_body(self):
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_json(415, {"error": "unsupported content-type"})
            return None
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            self._send_json(400, {"error": "missing content-length"})
            return None
        try:
            length = int(length_header)
        except ValueError:
            self._send_json(400, {"error": "invalid content-length"})
            return None
        if length < 0:
            self._send_json(400, {"error": "invalid content-length"})
            return None
        if length > MAX_BODY_BYTES:
            try:
                self.rfile.read(MAX_BODY_BYTES)
            except Exception:
                pass
            self._send_json(413, {"error": "body too large"})
            return None
        try:
            raw_body = self.rfile.read(length)
        except Exception:
            self._send_json(400, {"error": "read error"})
            return None
        try:
            return json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "invalid json"})
            return None

    def do_POST(self):
        if not self._has_local_host():
            self._send_json(403, {"error": "invalid host"})
            return
        if not self._has_local_origin():
            self._send_json(403, {"error": "invalid origin"})
            return

        if self.path in {"/api/policy/intent", "/api/policy/approve", "/api/policy/conditions"}:
            if self.headers.get("X-Policy-Console") != "1":
                self._send_json(403, {"error": "missing policy header"})
                return
            body = self._read_json_body()
            if not isinstance(body, dict):
                if body is not None:
                    self._send_json(400, {"error": "invalid body"})
                return
            try:
                if self.path == "/api/policy/intent":
                    intent = body.get("intent")
                    if not isinstance(intent, str) or not intent.strip() or len(intent.strip()) > 4000:
                        self._send_json(400, {"error": "invalid intent"})
                        return
                    state = replace_policy_flow(intent.strip())
                elif self.path == "/api/policy/conditions":
                    intent = body.get("intent")
                    invariants = body.get("invariants")
                    if not isinstance(intent, str) or not intent.strip() or len(intent.strip()) > 4000:
                        self._send_json(400, {"error": "invalid intent"})
                        return
                    if not isinstance(invariants, list):
                        self._send_json(400, {"error": "invalid invariants"})
                        return
                    state = replace_structured_policy_flow(intent.strip(), invariants)
                else:
                    confirmation = body.get("confirmation")
                    if not isinstance(confirmation, str):
                        self._send_json(400, {"error": "invalid confirmation"})
                        return
                    state = approve_current_policy(confirmation)
            except SynthesisInputError as exc:
                self._send_json(422, {"error": str(exc)}, no_store=True)
                return
            except Exception:
                self._send_json(500, {"error": "policy workflow failed"}, no_store=True)
                return
            self._send_json(200, state, no_store=True)
            return

        if self.path != "/api/run":
            self._send_json(400, {"error": "unknown endpoint"})
            return
        if self.headers.get("X-G3-Console") != "1":
            self._send_json(403, {"error": "missing console header"})
            return
        body = self._read_json_body()
        if body is None:
            return
        if not isinstance(body, dict) or not isinstance(body.get("command"), str):
            self._send_json(400, {"error": "invalid body"})
            return
        command = body["command"]
        if command not in ALLOWED_COMMANDS:
            self._send_json(400, {"error": "unknown command"})
            return

        acquired = run_lock.acquire(blocking=False)
        if not acquired:
            self._send_json(409, {"error": "busy"})
            return
        try:
            self._stream_command(command)
        finally:
            run_lock.release()

    def _write_ndjson(self, record):
        line = json.dumps(record).encode("utf-8") + b"\n"
        try:
            self.wfile.write(line)
            self.wfile.flush()
        except Exception:
            raise ConnectionError("client disconnected")

    def _stream_command(self, command):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        try:
            self._write_ndjson({"type": "start", "command": command})
        except ConnectionError:
            return

        if command == "status":
            try:
                state = "trace ready" if TRACE_PATH.is_file() else "trace missing"
                self._write_ndjson({"type": "line", "text": "status: " + state})
                self._write_ndjson(
                    {"type": "done", "exitCode": 0, "refreshTrace": True}
                )
            except ConnectionError:
                pass
            return

        argv = build_argv(command)
        if argv is None:
            try:
                self._write_ndjson({"type": "line", "text": "executable not found"})
                self._write_ndjson(
                    {"type": "done", "exitCode": 1, "refreshTrace": False}
                )
            except ConnectionError:
                pass
            return

        proc = None
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(REPO_ROOT),
                env=child_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
            client_connected = True
            for text_line in proc.stdout:
                if client_connected:
                    try:
                        self._write_ndjson({"type": "line", "text": text_line.rstrip("\n")})
                    except ConnectionError:
                        client_connected = False
            exit_code = proc.wait()
            if client_connected:
                self._write_ndjson(
                    {"type": "done", "exitCode": exit_code, "refreshTrace": True}
                )
        except FileNotFoundError:
            try:
                self._write_ndjson({"type": "line", "text": "executable not found"})
                self._write_ndjson(
                    {"type": "done", "exitCode": 1, "refreshTrace": False}
                )
            except ConnectionError:
                pass
        except ConnectionError:
            if proc is not None and proc.poll() is None:
                proc.wait()
        except Exception:
            if proc is not None and proc.poll() is None:
                proc.kill()
            try:
                self._write_ndjson({"type": "line", "text": "internal error"})
                self._write_ndjson(
                    {"type": "done", "exitCode": 1, "refreshTrace": False}
                )
            except ConnectionError:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4173)
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("Serving at http://127.0.0.1:%d" % args.port)
    print("Allowed commands: %s" % ", ".join(ALLOWED_COMMANDS))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
