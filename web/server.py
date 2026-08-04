"""Local web UI for ocplab — a second thin UX layer, not a second product.

The rule that governs this file is the same one that governs `ocplab` itself:
**no business logic here**. It does not call AWS, does not read Terraform
state, does not know what a CSR is. Every operation is a subprocess call to
`ocplab <command>`, and everything the browser shows is that command's own
output. If something needs to change about *what* a command does, it changes
in the Ansible role — never here.

That constraint is enforced structurally rather than by discipline: the
browser cannot supply an argv. It picks an id from COMMANDS, and the server
runs the fixed argv that id maps to. There is no path from user input to a
command line, so there is nothing to inject into.

Security matters more here than in a typical local dev server, because this
one holds AWS credentials and can spend and destroy real infrastructure. Three
controls, all mandatory and none of them optional flags:

- It binds 127.0.0.1 only. There is no setting to change that.
- It rejects any request whose Host header isn't loopback. Without this, a
  malicious page you visit could point a DNS name at 127.0.0.1 and drive this
  server from your browser — DNS rebinding, and `terraform destroy` is exactly
  the kind of thing worth rebinding for.
- Every API call needs a token minted at startup and printed in the URL. It
  travels in a header for normal calls and in the query string for the event
  stream, because EventSource cannot set headers.

Stdlib only, and no build step for the frontend: this has to stay readable on
GitHub next to everything else, and adding Node to a repo that needs Python
and Terraform would cost more than it buys.
"""

import http.server
import json
import os
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OCPLAB = REPO_ROOT / "ocplab"
VENV_BIN = REPO_ROOT / ".venv" / "bin"
STATIC_DIR = Path(__file__).resolve().parent / "static"

# The whole catalogue of what the UI can run. `argv` is fixed per entry — see
# the module docstring. `confirm` is the text the browser must make the user
# accept first; it stands in for the CLI's interactive prompt, which is why
# every entry that has one is run with --yes.
COMMANDS = [
    {
        "id": "status", "argv": ["status"], "label": "Status", "group": "inspect",
        "desc": "Local summary: pinned version, where oc points, install-dir age.",
    },
    {
        "id": "validate", "argv": ["validate"], "label": "Validate", "group": "inspect",
        "desc": "Check cluster.yaml, reporting every error rather than the first.",
    },
    {
        "id": "prereqs", "argv": ["prereqs"], "label": "Prerequisites", "group": "inspect",
        "desc": "Static checklist — needs neither cluster.yaml nor AWS.",
    },
    {
        "id": "preflight", "argv": ["preflight"], "label": "Preflight", "group": "inspect",
        "desc": "Read-only: binaries, AWS credentials, pull secret, DNS, AMI.",
    },
    {
        "id": "verify", "argv": ["verify"], "label": "Verify", "group": "inspect",
        "desc": "Live health: API, ClusterVersion, nodes, ClusterOperators.",
    },
    {
        "id": "cost", "argv": ["cost"], "label": "Cost", "group": "inspect",
        "desc": "Approximate USD/hour for what is actually deployed right now.",
    },
    {
        "id": "console", "argv": ["console"], "label": "Console", "group": "inspect",
        "desc": "Console URL and the kubeadmin password.",
    },
    {
        "id": "power-status", "argv": ["power", "status"], "label": "Power status", "group": "inspect",
        "desc": "Read-only check of whether the nodes are running or stopped.",
    },
    {
        "id": "versions", "argv": ["versions", "list"], "label": "Versions", "group": "inspect",
        "desc": "What the mirror publishes and what is in the local cache.",
    },

    {
        "id": "render", "argv": ["render"], "label": "Render", "group": "prepare",
        "desc": "cluster.yaml to terraform.tfvars + generated.yml.",
    },
    {
        "id": "ignition", "argv": ["ignition"], "label": "Ignition", "group": "prepare",
        "desc": "Generate install-dir and the Ignition configs.",
    },
    {
        "id": "bootstrap", "argv": ["bootstrap", "apply"], "label": "Bootstrap AWS", "group": "prepare",
        "confirm": "Creates the IAM user, public hosted zone and SSH keypair in AWS. Continue?",
        "desc": "The one-time AWS prerequisites preflight cannot create itself.",
    },

    {
        "id": "deploy", "argv": ["deploy"], "label": "Deploy", "group": "operate",
        "confirm": "This creates real AWS infrastructure and starts billing immediately. Continue?",
        "desc": "render, ignition, terraform apply, wait for boot, finalize.",
    },
    {
        "id": "repair", "argv": ["repair"], "label": "Repair workers", "group": "operate",
        "confirm": "Runs terraform apply against the running cluster. It refuses any plan that is not add-only. Continue?",
        "desc": "Recreate workers that vanished, approve their CSRs, prune dead Nodes.",
    },
    {
        "id": "power-off", "argv": ["power", "off"], "label": "Power off", "group": "operate",
        "confirm": "Cordons, drains and stops every node. EBS, NAT and load balancers keep billing while off. Continue?",
        "desc": "Graceful shutdown — an alternative to destroy, not a cost saving.",
    },
    {
        "id": "power-on", "argv": ["power", "on"], "label": "Power on", "group": "operate",
        "confirm": "Starts the nodes again and approves their CSRs. Continue?",
        "desc": "Bring a powered-off cluster back.",
    },
    {
        "id": "safety-net", "argv": ["safety-net", "apply"], "label": "Safety net", "group": "operate",
        "confirm": "Creates the budget, budget action and killswitch Lambda. Continue?",
        "desc": "Cost safety net, managed outside Terraform.",
    },
    {
        "id": "destroy", "argv": ["destroy"], "label": "Destroy", "group": "danger",
        "confirm": "This tears down the entire cluster and everything in it. This cannot be undone. Continue?",
        "desc": "Ordered teardown of the whole cluster.",
    },
]

COMMANDS_BY_ID = {c["id"]: c for c in COMMANDS}

# Shown in the About panel. Read from the CLI rather than hardcoded, so the
# version cannot drift from `ocplab --version`; the rest is repository fact.
ABOUT = {
    "name": "ocplab",
    "tagline": "OpenShift 4 on AWS with user-provisioned infrastructure.",
    "license": "MIT",
    "author": "Luis Garcia",
    "github_user": "LuixyToledo97",
    "github_url": "https://github.com/LuixyToledo97",
    "repo_url": "https://github.com/LuixyToledo97/openshift-upi-aws",
    "fonts": "Inter and JetBrains Mono, bundled under the SIL Open Font License 1.1.",
}

# `ocplab ssh` is deliberately absent and stays absent: it hands the terminal
# over with execvp, which a browser has nowhere to put. Wiring a web terminal
# to it would also hand whoever reached this server a root shell on the nodes,
# which is the one thing the loopback binding exists to prevent. `ocplab env`
# is absent for a simpler reason — it prints a line for a shell to eval, and
# there is no shell here.
EXCLUDED = {
    "ssh": "needs a real terminal — run it from a shell.",
    "env": "prints a line for your shell to eval; the browser has no shell.",
    "init": "creates cluster.yaml; use the config editor here instead.",
    "setup": "builds the venv this server is already running inside.",
}


def child_env():
    """Environment for the ocplab subprocesses.

    Prepends .venv/bin so playbook-backed commands find ansible-playbook even
    when the server was started from a shell that never activated the venv —
    which is most of them, since the point of a web UI is not having to.
    """
    env = os.environ.copy()
    if VENV_BIN.is_dir():
        env["PATH"] = f"{VENV_BIN}{os.pathsep}{env.get('PATH', '')}"
    # The callback already drops colour and in-place rewrites when stdout is
    # not a terminal, which is exactly what we want piped into a browser. This
    # only makes it explicit for anything else in the chain.
    env["NO_COLOR"] = "1"
    return env


class Job:
    """One ocplab run, and the lines it has produced so far.

    Lines are kept, not just forwarded. A browser that reloads mid-deploy —
    or connects late, forty minutes in — gets the whole history and then
    follows live. Losing the live view must never lose the record; that lesson
    was already paid for once, when a closed terminal killed a running deploy.
    """

    def __init__(self, job_id, command, dry_run):
        self.id = job_id
        self.command = command
        self.dry_run = dry_run
        self.lines = []
        self.exit_code = None
        self.started = time.time()
        self.finished = None
        self.proc = None
        self._lock = threading.Lock()
        self._subscribers = []

    def add_line(self, text):
        with self._lock:
            self.lines.append(text)
            for q in self._subscribers:
                q.put(text)

    def subscribe(self):
        """Return (history, queue). Taken under the lock so a line emitted
        between reading the history and registering can't slip through."""
        q = queue.Queue()
        with self._lock:
            history = list(self.lines)
            self._subscribers.append(q)
        return history, q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def finish(self, code):
        self.exit_code = code
        self.finished = time.time()
        with self._lock:
            for q in self._subscribers:
                q.put(None)  # sentinel: stream over

    @property
    def running(self):
        return self.exit_code is None

    def as_dict(self):
        return {
            "id": self.id,
            "command": self.command["id"],
            "label": self.command["label"],
            # Carried so the runs list can separate read-only operations from
            # the ones that changed something, without re-deriving it client-side.
            "group": self.command["group"],
            "dry_run": self.dry_run,
            "running": self.running,
            "exit_code": self.exit_code,
            "started": self.started,
            "finished": self.finished,
            "line_count": len(self.lines),
        }


class Runner:
    """Serialises jobs: one at a time, because there is one cluster.

    Refusing a second concurrent operation is not a simplification, it is the
    correct behaviour — two `terraform apply` runs against one state file is a
    way to corrupt it, and `destroy` racing `deploy` is worse.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs = {}
        self._current = None
        self._counter = 0

    @property
    def current(self):
        return self._current

    def get(self, job_id):
        return self._jobs.get(job_id)

    def recent(self, limit=20):
        return sorted(self._jobs.values(), key=lambda j: j.started, reverse=True)[:limit]

    def start(self, command, dry_run):
        with self._lock:
            if self._current is not None and self._current.running:
                raise RuntimeError(
                    f"'{self._current.command['label']}' is still running. "
                    "Only one operation can run at a time."
                )
            self._counter += 1
            job = Job(f"job-{self._counter}", command, dry_run)
            self._jobs[job.id] = job
            self._current = job

        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job):
        argv = [sys.executable, str(OCPLAB)] + list(job.command["argv"])
        # --yes always: the browser already collected the confirmation, and an
        # input() prompt in a subprocess nobody can type into would hang for
        # ever. --dry-run maps straight onto the CLI's own flag.
        argv.append("--yes")
        if job.dry_run:
            argv.append("--dry-run")

        job.add_line(f"$ ocplab {' '.join(job.command['argv'])}"
                     f"{' --dry-run' if job.dry_run else ''}")
        try:
            proc = subprocess.Popen(
                argv, cwd=str(REPO_ROOT), env=child_env(),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, text=True, bufsize=1,
            )
        except OSError as exc:
            job.add_line(f"failed to start: {exc}")
            job.finish(127)
            return

        job.proc = proc
        try:
            for line in iter(proc.stdout.readline, ""):
                job.add_line(line.rstrip("\n"))
        finally:
            proc.stdout.close()
            job.finish(proc.wait())

    def cancel(self, job):
        if job.proc and job.running:
            job.add_line("--- cancelled from the web UI ---")
            job.proc.terminate()
            return True
        return False


RUNNER = Runner()


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "ocplab-web"
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):
        pass  # the terminal running this belongs to the user, not to an access log

    def _host_is_loopback(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("localhost", "127.0.0.1", "[::1]", "::1")

    def _authorised(self, params):
        supplied = self.headers.get("X-Ocplab-Token") or params.get("token", [""])[0]
        return secrets.compare_digest(supplied or "", self.server.token)

    def _send(self, code, body=b"", ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Nothing here should ever be embedded, cached or referrer-leaked.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, code, payload):
        self._send(code, json.dumps(payload).encode(), "application/json")

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode())
        except (ValueError, UnicodeDecodeError):
            return None

    # -- routing ----------------------------------------------------------

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        path = parsed.path

        if not self._host_is_loopback():
            self._json(403, {"error": "this server only answers to localhost"})
            return

        if path == "/" or path.startswith("/static/"):
            self._serve_static(path, params)
            return

        if not self._authorised(params):
            self._json(401, {"error": "bad or missing token"})
            return

        if path == "/api/state":
            self._json(200, {
                "commands": COMMANDS,
                "excluded": EXCLUDED,
                "about": {**ABOUT, "version": self.server.cli_version},
                "repo": str(REPO_ROOT),
                "config_exists": (REPO_ROOT / "cluster.yaml").exists(),
                "current": RUNNER.current.as_dict() if RUNNER.current else None,
                "recent": [j.as_dict() for j in RUNNER.recent()],
            })
        elif path == "/api/overview":
            self._overview()
        elif path == "/api/config":
            cfg = REPO_ROOT / "cluster.yaml"
            self._json(200, {
                "exists": cfg.exists(),
                "content": cfg.read_text() if cfg.exists() else "",
                "templates": sorted(p.name for p in (REPO_ROOT / "examples").glob("*.yaml")),
            })
        elif path == "/api/template":
            name = params.get("name", [""])[0]
            # Resolved against examples/ and checked to still be inside it —
            # ../ in a query string must not be able to read the filesystem.
            target = (REPO_ROOT / "examples" / name).resolve()
            if target.parent != (REPO_ROOT / "examples").resolve() or not target.is_file():
                self._json(404, {"error": "no such template"})
                return
            self._json(200, {"content": target.read_text()})
        elif path.startswith("/api/jobs/") and path.endswith("/stream"):
            self._stream(path.split("/")[3])
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if not self._host_is_loopback():
            self._json(403, {"error": "this server only answers to localhost"})
            return
        if not self._authorised(params):
            self._json(401, {"error": "bad or missing token"})
            return

        body = self._read_json()
        if body is None:
            self._json(400, {"error": "malformed JSON"})
            return

        if parsed.path == "/api/run":
            self._run(body)
        elif parsed.path == "/api/config":
            self._save_config(body)
        elif parsed.path == "/api/validate":
            self._validate(body)
        elif parsed.path == "/api/cancel":
            job = RUNNER.get(body.get("id", ""))
            if not job:
                self._json(404, {"error": "no such job"})
            else:
                self._json(200, {"cancelled": RUNNER.cancel(job)})
        else:
            self._json(404, {"error": "not found"})

    # -- handlers ---------------------------------------------------------

    def _serve_static(self, path, params):
        name = "index.html" if path == "/" else path[len("/static/"):]
        target = (STATIC_DIR / name).resolve()
        # Containment, not parent equality: the fonts live in a subdirectory,
        # and requiring the parent to *be* STATIC_DIR quietly 404s them. What
        # matters is that the resolved path stays inside, which is still what
        # stops ../ from escaping.
        if not target.is_file() or STATIC_DIR.resolve() not in target.parents:
            self._send(404, b"not found", "text/plain")
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".woff2": "font/woff2",
            ".svg": "image/svg+xml",
            ".md": "text/plain; charset=utf-8",
        }.get(target.suffix, "application/octet-stream")
        data = target.read_bytes()
        if name == "index.html":
            # The token reaches the page exactly once, through the page
            # itself, so it never has to live in localStorage or a cookie.
            data = data.replace(b"__OCPLAB_TOKEN__", self.server.token.encode())
        self._send(200, data, ctype)

    def _run(self, body):
        command = COMMANDS_BY_ID.get(body.get("command"))
        if not command:
            self._json(400, {"error": "unknown command"})
            return
        try:
            job = RUNNER.start(command, bool(body.get("dry_run")))
        except RuntimeError as exc:
            self._json(409, {"error": str(exc)})
            return
        self._json(200, job.as_dict())

    def _save_config(self, body):
        content = body.get("content")
        if not isinstance(content, str):
            self._json(400, {"error": "expected a 'content' string"})
            return
        cfg = REPO_ROOT / "cluster.yaml"
        try:
            # Overwriting the only copy of a hand-edited config with no way
            # back would be its own kind of destructive operation.
            if cfg.exists():
                (REPO_ROOT / "cluster.yaml.bak").write_text(cfg.read_text())
            cfg.write_text(content)
        except OSError as exc:
            self._json(500, {"error": f"could not write cluster.yaml: {exc}"})
            return
        self._json(200, {"saved": True, "backup": cfg.exists()})

    def _overview(self):
        """The dashboard's data, from `ocplab status --json`.

        Structured on purpose rather than scraped from the text output: a
        dashboard built on parsing human prose breaks the first time someone
        improves a sentence. Everything it reports is local and instant — no
        AWS call — which is what makes it safe for the page to poll. Live state
        (nodes, operators, power, cost) stays behind explicit actions, because
        those cost time and money and should be asked for.
        """
        try:
            proc = subprocess.run(
                [sys.executable, str(OCPLAB), "status", "--json"],
                cwd=str(REPO_ROOT), env=child_env(), text=True, timeout=30,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            self._json(504, {"error": "ocplab status timed out"})
            return
        if proc.returncode != 0:
            # The usual cause is an invalid or missing cluster.yaml, and the
            # message says which — the dashboard shows it instead of empty cards.
            self._json(200, {"ok": False, "error": (proc.stderr or proc.stdout).strip()})
            return
        try:
            self._json(200, {"ok": True, "status": json.loads(proc.stdout)})
        except ValueError:
            self._json(500, {"ok": False, "error": "could not parse the status output"})

    def _validate(self, body):
        """Validate YAML that hasn't been saved yet.

        The only place a path reaches an ocplab argv, and it is one this server
        wrote itself into a temp directory — the browser supplies the file's
        *contents*, never its name. Validation has to work before saving, or
        "paste your cluster.yaml and see what's wrong" turns into "overwrite
        your working config to find out".

        It runs synchronously rather than as a Job: it is quick, local, touches
        no AWS, and queueing it behind a 40-minute deploy would be absurd.
        """
        content = body.get("content")
        if not isinstance(content, str):
            self._json(400, {"error": "expected a 'content' string"})
            return
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "cluster.yaml"
            candidate.write_text(content)
            try:
                proc = subprocess.run(
                    [sys.executable, str(OCPLAB), "validate", "-f", str(candidate)],
                    cwd=str(REPO_ROOT), env=child_env(), text=True, timeout=30,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired:
                self._json(504, {"error": "validation timed out"})
                return
        self._json(200, {"ok": proc.returncode == 0, "output": proc.stdout.strip()})

    def _stream(self, job_id):
        job = RUNNER.get(job_id)
        if not job:
            self._json(404, {"error": "no such job"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        history, q = job.subscribe()
        try:
            for line in history:
                self._event({"line": line})
            # A job that already finished has no sentinel left to send: finish()
            # delivered it to whoever was subscribed at the time, and this
            # connection wasn't. Without this the stream would replay the
            # history and then block for ever, holding a thread — which is
            # exactly what viewing a past run from the history tab does.
            if not job.running:
                self._event({"done": True, "exit_code": job.exit_code})
                return
            while True:
                try:
                    line = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")   # keeps proxies and
                    self.wfile.flush()                      # idle timeouts away
                    continue
                if line is None:
                    self._event({"done": True, "exit_code": job.exit_code})
                    return
                self._event({"line": line})
        except (BrokenPipeError, ConnectionResetError):
            pass  # the tab was closed; the job carries on and the lines are kept
        finally:
            job.unsubscribe(q)

    def _event(self, payload):
        self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
        self.wfile.flush()


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, token):
        super().__init__(addr, Handler)
        self.token = token
        self.cli_version = read_cli_version()


def read_cli_version():
    """`ocplab --version`, asked once at startup.

    Read rather than hardcoded so About cannot drift from the CLI, and asked
    once rather than per request because it cannot change while the server is
    up — the file it comes from would have to be edited underneath it.
    """
    try:
        proc = subprocess.run(
            [sys.executable, str(OCPLAB), "--version"],
            cwd=str(REPO_ROOT), env=child_env(), text=True, timeout=15,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        )
        if proc.returncode == 0:
            return proc.stdout.strip().split()[-1]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        pass
    return "unknown"


def serve(port=8770, token=None, announce=True):
    """Run the server in the foreground until interrupted.

    `ocplab web start` daemonises this and mints the token itself, so it can
    print the URL and record it before the child is even listening. The token
    arrives through the environment rather than argv on purpose: on Linux
    /proc/<pid>/cmdline is world-readable, so an argv token leaks to every
    other user on the box, while /proc/<pid>/environ is owner-only.
    """
    token = token or os.environ.get("OCPLAB_WEB_TOKEN") or secrets.token_urlsafe(24)
    try:
        httpd = Server(("127.0.0.1", port), token)
    except OSError as exc:
        if exc.errno in (socket.EADDRINUSE if hasattr(socket, "EADDRINUSE") else 98, 98):
            raise SystemExit(
                f"port {port} is already in use — another ocplab web may be running "
                f"(try 'ocplab web --port {port + 1}')."
            )
        raise SystemExit(f"could not start the server: {exc}")

    # flush explicitly: when daemonised this goes to logs/web.log, and Python
    # buffers stdout whenever it isn't a terminal — which for a background
    # service means the line only lands when the process dies.
    if announce:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] listening on 127.0.0.1:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] stopped", flush=True)


# Entry point for the daemonised child. `ocplab web start` spawns this file
# directly rather than re-entering the CLI, which keeps the background process
# obviously identifiable in `ps` — and `stop` relies on exactly that to avoid
# signalling an unrelated process that inherited a recycled PID.
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ocplab web server (started by 'ocplab web start')")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    serve(port=args.port)
