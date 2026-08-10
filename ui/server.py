#!/usr/bin/env python3
import argparse
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

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}

ALLOWED_COMMANDS = ("status", "validate", "evaluate", "run g3")

MAX_BODY_BYTES = 4096

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

run_lock = threading.Lock()
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

    def do_POST(self):
        if not self._has_local_host():
            self._send_json(403, {"error": "invalid host"})
            return
        if self.path != "/api/run":
            self._send_json(400, {"error": "unknown endpoint"})
            return
        if not self._has_local_origin():
            self._send_json(403, {"error": "invalid origin"})
            return
        if self.headers.get("X-G3-Console") != "1":
            self._send_json(403, {"error": "missing console header"})
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_json(415, {"error": "unsupported content-type"})
            return
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            self._send_json(400, {"error": "missing content-length"})
            return
        try:
            length = int(length_header)
        except ValueError:
            self._send_json(400, {"error": "invalid content-length"})
            return
        if length < 0:
            self._send_json(400, {"error": "invalid content-length"})
            return
        if length > MAX_BODY_BYTES:
            try:
                self.rfile.read(MAX_BODY_BYTES)
            except Exception:
                pass
            self._send_json(413, {"error": "body too large"})
            return
        try:
            raw_body = self.rfile.read(length)
        except Exception:
            self._send_json(400, {"error": "read error"})
            return
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "invalid json"})
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
