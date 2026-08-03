"""ocplab's stdout callback — human-readable output instead of raw Ansible.

Why a custom callback rather than one of the stock ones (`default`, `dense`,
`unixy`): none of them can be told to keep this project's *actual* user-facing
output — the `debug` messages and `assert` fail_msg/success_msg that roles like
`verify`, `cost` and `bootstrap` use as their report — while dropping the
`TASK [role : ...] ****` scaffolding around them. Here that distinction is
explicit: `debug` output is promoted, everything routine is collapsed to one
line, and skipped tasks are hidden entirely.

This plugin also owns the on-disk execution log (path in `OCPLAB_LOG_FILE`,
set by the `ocplab` CLI). It is written here rather than via Ansible's own
`ANSIBLE_LOG_PATH` because that logs whatever goes through `Display`, which
would capture this plugin's terminal formatting — including the in-place
line rewrites — instead of a clean, plain-text record. Writing both streams
from one place keeps the terminal readable and the log complete and
ANSI-free.

The log is best-effort: any failure writing it is swallowed, because losing
the log must never take down a `deploy` that is otherwise fine.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

from ansible import context
from ansible.plugins.callback import CallbackBase

DOCUMENTATION = """
    name: ocplab_output
    type: stdout
    short_description: ocplab's human-readable output
    version_added: "1.1.0"
    description:
      - Renders playbook progress as a compact, readable stream instead of
        Ansible's default task banners.
      - Promotes C(debug) messages and C(assert) results, which is what the
        ocplab roles use to report to the user.
      - Writes a full plain-text execution log to the path in the
        C(OCPLAB_LOG_FILE) environment variable, when set.
    extends_documentation_fragment:
      - default_callback
    requirements:
      - set as stdout_callback in ansible.cfg
"""

# Kept deliberately small: the point is a calm, uniform output, not a palette.
RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"

# Erase the whole line and return to column 0 — used to rewrite the
# "currently running" line in place once the task finishes.
CLEAR_LINE = "\r\033[2K"


def _fmt_duration(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "ocplab_output"

    def __init__(self):
        super().__init__()
        self._color = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
        # In-place rewriting only makes sense on a terminal; when piped or
        # redirected it would leave duplicated lines behind.
        self._interactive = sys.stdout.isatty()
        self._pending = None          # task name currently shown as "running"
        self._task_started = None     # monotonic timestamp of that task
        self._play_started = None
        self._item_count = 0
        self._item_details = []       # (symbol, text) collected during a loop
        self._counts = {"ok": 0, "changed": 0, "skipped": 0, "failed": 0, "ignored": 0}
        self._structural = False   # current task is an include/import
        self._logfile = None
        self._open_log()

    # -- log ---------------------------------------------------------------

    def _open_log(self):
        path = os.environ.get("OCPLAB_LOG_FILE")
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self._logfile = open(path, "a", encoding="utf-8", buffering=1)
        except OSError:
            self._logfile = None  # never let logging break the run

    def _log(self, text):
        if not self._logfile:
            return
        try:
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            for line in str(text).splitlines() or [""]:
                self._logfile.write(f"{stamp}  {line}\n")
        except (OSError, ValueError):
            self._logfile = None

    def _log_result(self, label, result):
        """Full, unabridged result into the log only — never onto the terminal."""
        if not self._logfile:
            return
        try:
            # ensure_ascii=False: this project's messages are full of em-dashes
            # and accented text, which would otherwise land in the log as
            # \uXXXX escapes — unreadable in exactly the situation the log exists for.
            payload = json.dumps(
                result._result, indent=2, default=str, sort_keys=True, ensure_ascii=False
            )
        except (TypeError, ValueError):
            payload = repr(result._result)
        self._log(f"{label} — full result:")
        self._log(payload)

    # -- terminal ----------------------------------------------------------

    def _c(self, text, *codes):
        if not self._color or not codes:
            return text
        return "".join(codes) + text + RESET

    def _clear_pending(self):
        if self._pending is not None and self._interactive:
            sys.stdout.write(CLEAR_LINE)
            sys.stdout.flush()
        self._pending = None

    def _emit(self, line, log=True):
        """Print a finished line, replacing any in-place 'running' line."""
        self._clear_pending()
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
        if log:
            self._log(self._strip(line))

    @staticmethod
    def _strip(text):
        out, i = [], 0
        while i < len(text):
            if text[i] == "\033":
                while i < len(text) and text[i] not in "m":
                    i += 1
                i += 1
                continue
            out.append(text[i])
            i += 1
        return "".join(out).replace("\r", "")

    def _show_running(self, text):
        if not self._interactive:
            return
        sys.stdout.write(CLEAR_LINE + self._c(f"  ⋯ {text}", DIM))
        sys.stdout.flush()

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _task_name(task):
        try:
            role = task._role.get_name() if task._role else None
        except AttributeError:
            role = None
        name = task.get_name().strip()
        # get_name() already prefixes the role on some ansible versions;
        # don't double it up.
        if role and not name.startswith(role + " :"):
            return f"{role} : {name}"
        return name

    # What `command`/`shell` report as `msg` when the real reason is on the
    # failed program's own stderr. Seen for real on 2026-08-02: a failed
    # `openshift-install wait-for` showed only "The command exited with a
    # non-zero return code." while stderr held the actual diagnosis, which
    # reached the log but never the terminal — the precise thing this block
    # exists to prevent.
    _USELESS_MSGS = (
        "the command exited with a non-zero return code",
        "non-zero return code",
        "module failed",
    )

    @staticmethod
    def _join(value):
        if isinstance(value, (list, tuple)):
            return "\n".join(str(v) for v in value)
        return str(value) if value is not None else ""

    @classmethod
    def _error_text(cls, res):
        """The one thing worth showing a human out of a failed result."""
        msg = cls._join(res.get("msg")).strip()
        # A generic module message is a label, not a diagnosis — when the
        # program's own output is available, that's what the user needs.
        if msg and msg.lower().rstrip(".") not in cls._USELESS_MSGS:
            return msg
        for key in ("stderr", "module_stderr", "stdout", "reason"):
            detail = cls._join(res.get(key)).strip()
            if detail:
                rc = res.get("rc")
                header = f"exit status {rc}" if rc is not None else msg or "failed"
                return f"{header}\n{detail}"
        return msg or "no error message reported by Ansible"

    # Keys Ansible adds to every result — never the thing a `debug: var=...`
    # was actually asked to show.
    _RESULT_NOISE = frozenset({
        "changed", "failed", "skipped", "skip_reason", "item", "results",
        "attempts", "retries", "warnings", "deprecations", "exception",
        "censored", "msg",
    })

    @classmethod
    def _debug_payload(cls, res):
        """What a `debug` task meant to show, for both its `msg` and `var` forms."""
        if "msg" in res:
            return res["msg"]
        # `debug: var=foo` returns the value under the variable's own name,
        # so there's no fixed key to read — take the first non-bookkeeping one.
        for key, value in res.items():
            if key.startswith("_ansible") or key in cls._RESULT_NOISE:
                continue
            return value
        return ""

    def _emit_payload(self, payload):
        if isinstance(payload, (list, tuple)):
            for entry in payload:
                self._emit(self._indent(entry))
        elif isinstance(payload, dict):
            self._emit(self._indent(json.dumps(payload, indent=2, default=str, ensure_ascii=False)))
        elif str(payload).strip():
            self._emit(self._indent(payload))

    def _indent(self, text, prefix="      "):
        lines = []
        for line in str(text).splitlines():
            lines.append(prefix + line)
        return "\n".join(lines) if lines else prefix

    # -- playbook lifecycle ------------------------------------------------

    def v2_playbook_on_start(self, playbook):
        self._log(f"=== ansible-playbook {playbook._file_name}")
        self._log(f"=== argv: {' '.join(sys.argv)}")

    def v2_playbook_on_play_start(self, play):
        self._play_started = time.monotonic()
        name = play.get_name().strip()
        mode = ""
        if context.CLIARGS.get("check"):
            mode = self._c("  [check mode — nothing will be changed]", YELLOW)
        self._emit("")
        self._emit(self._c(f"▶ {name}", BOLD, BLUE) + mode)
        self._emit("")

    # Scaffolding, not results: an include is a control-flow statement, and
    # the tasks it pulls in report for themselves right afterwards. Reporting
    # the include too (and, with a loop, as "N items") describes the playbook's
    # structure rather than anything that happened.
    _STRUCTURAL = frozenset({
        "include_tasks", "ansible.builtin.include_tasks",
        "import_tasks", "ansible.builtin.import_tasks",
        "include_role", "ansible.builtin.include_role",
        "import_role", "ansible.builtin.import_role",
        "include", "ansible.builtin.include",
    })

    # `pause` writes its own countdown straight to the terminal, so it must
    # not be given an open in-place line to collide with — that produced
    # "…wait before the next roundPausing for 180 seconds" on one line.
    _WRITES_OWN_OUTPUT = frozenset({"pause", "ansible.builtin.pause"})

    def v2_playbook_on_task_start(self, task, is_conditional):
        self._item_count = 0
        self._item_details = []
        self._task_started = time.monotonic()
        self._structural = task.action in self._STRUCTURAL
        name = self._task_name(task)
        self._log(f"--- TASK: {name}")
        if self._structural:
            self._pending = None
            return
        if task.action in self._WRITES_OWN_OUTPUT:
            # Nothing at all up front. An in-place line would be overwritten by
            # the module's own countdown, and a committed line would be a lie
            # whenever the task turns out to be skipped by its `when` — which
            # is exactly what happened to teardown's conditional ELB pause,
            # leaving "⋯ Wait for AWS to finish releasing…" on screen for a
            # task that never ran. Whether a task runs is only known at result
            # time, so the honest option is to announce it then.
            self._pending = None
            return
        self._pending = name
        self._show_running(name)

    def v2_playbook_on_handler_task_start(self, task):
        self.v2_playbook_on_task_start(task, False)

    def v2_playbook_on_include(self, included_file):
        # Same reasoning as _STRUCTURAL: the stock callback prints an
        # "included: <path>" line per file, which is playbook structure.
        self._log(f"    included: {included_file._filename}")

    def v2_playbook_on_no_hosts_matched(self):
        self._emit(self._c("  no hosts matched", YELLOW))

    # -- task results ------------------------------------------------------

    def _elapsed_suffix(self):
        """Only annotate tasks slow enough that the wait needs explaining."""
        if self._task_started is None:
            return ""
        elapsed = time.monotonic() - self._task_started
        if elapsed < 10:
            return ""
        return self._c(f"  ({_fmt_duration(elapsed)})", DIM)

    def v2_runner_on_ok(self, result):
        res = result._result
        name = self._task_name(result._task)
        action = result._task.action

        if action in self._STRUCTURAL:
            self._clear_pending()
            return

        # `debug` is how these roles report to the user — it is the payload,
        # not scaffolding, so it gets promoted instead of collapsed.
        if action in ("debug", "ansible.builtin.debug"):
            self._counts["ok"] += 1
            self._emit(self._c(f"  ▸ {name}", CYAN))
            # A looped debug must be unwrapped per item: the aggregate result
            # of a loop carries the useless summary msg "All items completed",
            # and rendering that instead of each item's own message silently
            # throws away the entire point of the task.
            if isinstance(res.get("results"), list):
                for item_res in res["results"]:
                    if item_res.get("skipped"):
                        continue
                    self._emit_payload(self._debug_payload(item_res))
            else:
                self._emit_payload(self._debug_payload(res))
            return

        if "results" in res and isinstance(res["results"], list):
            self._finish_loop(name, failed=False)
            return

        suffix = self._elapsed_suffix()
        if res.get("changed"):
            self._counts["changed"] += 1
            self._emit(self._c("  ✚ ", YELLOW) + name + suffix)
        else:
            self._counts["ok"] += 1
            self._emit(self._c(f"  · {name}", DIM) + suffix)

        # assert's success_msg is deliberate, user-facing confirmation.
        if action in ("assert", "ansible.builtin.assert") and res.get("msg"):
            msg = str(res["msg"]).strip()
            if msg and msg != "All assertions passed":
                self._emit(self._c(self._indent(msg), GREEN))

    def v2_runner_on_failed(self, result, ignore_errors=False):
        res = result._result
        name = self._task_name(result._task)

        if "results" in res and isinstance(res["results"], list):
            self._finish_loop(name, failed=not ignore_errors)
            if not ignore_errors:
                self._failure_block(name, self._error_text(res), result)
            return

        if ignore_errors:
            self._counts["ignored"] += 1
            self._emit(self._c("  ! ", YELLOW) + name + self._c("  (ignored)", DIM))
            self._log_result(name, result)
            return

        self._counts["failed"] += 1
        self._emit(self._c("  ✗ ", RED, BOLD) + name)
        self._failure_block(name, self._error_text(res), result)

    def v2_runner_on_skipped(self, result):
        # Hidden on purpose: skipped tasks are the single largest source of
        # noise in these playbooks and carry no information. Counted in the
        # recap, and still recorded in the log.
        self._counts["skipped"] += 1
        self._clear_pending()
        self._log(f"    skipped: {self._task_name(result._task)}")

    def v2_runner_on_unreachable(self, result):
        name = self._task_name(result._task)
        self._counts["failed"] += 1
        self._emit(self._c("  ✗ ", RED, BOLD) + name + self._c("  (unreachable)", RED))
        self._failure_block(name, self._error_text(result._result), result)

    def v2_runner_retry(self, result):
        """`until`/`retries` — the live progress signal on the long waits."""
        res = result._result
        attempt = res.get("attempts", 0)
        retries = res.get("retries", 0)
        name = self._pending or self._task_name(result._task)
        self._log(f"    retry {attempt}/{retries - 1 if retries else '?'}: {name}")
        self._show_running(f"{name}  [attempt {attempt}/{retries - 1 if retries else '?'}]")

    # -- loops -------------------------------------------------------------

    def v2_runner_item_on_ok(self, result):
        self._item_count += 1
        res = result._result
        label = self._item_label(result)
        if res.get("changed"):
            self._item_details.append((self._c("✚", YELLOW), label))
        self._show_running(f"{self._pending}  [{self._item_count}]")

    def v2_runner_item_on_failed(self, result):
        self._item_count += 1
        label = self._item_label(result)
        self._item_details.append((self._c("✗", RED), f"{label} — {self._error_text(result._result)}"))
        self._log_result(f"item {label}", result)

    def v2_runner_item_on_skipped(self, result):
        self._item_count += 1

    @staticmethod
    def _item_label(result):
        item = result._result.get("item", result._result.get("_ansible_item_label", ""))
        if isinstance(item, dict):
            for key in ("name", "item", "key"):
                if key in item:
                    return str(item[key])
            return json.dumps(item, default=str)[:80]
        return str(item)[:80]

    def _finish_loop(self, name, failed):
        symbol = self._c("  ✗ ", RED, BOLD) if failed else self._c("  ✚ ", YELLOW)
        count = self._c(f"  ({self._item_count} items)", DIM)
        if failed:
            self._counts["failed"] += 1
        else:
            self._counts["changed"] += 1
        self._emit(symbol + name + count + self._elapsed_suffix())
        for sym, text in self._item_details:
            self._emit(f"      {sym} {text}")

    # -- diff (--dry-run passes --check --diff) ----------------------------

    def v2_on_file_diff(self, result):
        diff = result._result.get("diff")
        if not diff:
            return
        rendered = self._get_diff(diff)
        if rendered and rendered.strip():
            self._emit(self._indent(rendered.rstrip(), "      "))

    # -- failure block -----------------------------------------------------

    def _failure_block(self, name, message, result=None):
        self._emit("")
        self._emit(self._c("  ─── what failed ───", RED))
        self._emit(self._c(self._indent(f"task:  {name}", "    "), RED))
        self._emit(self._c("    error:", RED))
        self._emit(self._indent(message, "      "))
        self._emit("")
        if result is not None:
            self._log_result(name, result)

    # -- recap -------------------------------------------------------------

    def v2_playbook_on_stats(self, stats):
        self._clear_pending()
        elapsed = _fmt_duration(time.monotonic() - self._play_started) if self._play_started else "?"
        failed = self._counts["failed"] > 0 or bool(stats.failures) or bool(stats.dark)

        parts = [
            f"{self._counts['ok']} ok",
            f"{self._counts['changed']} changed",
            f"{self._counts['skipped']} skipped",
        ]
        if self._counts["ignored"]:
            parts.append(f"{self._counts['ignored']} ignored")
        if self._counts["failed"]:
            parts.append(f"{self._counts['failed']} failed")

        self._emit("")
        if failed:
            self._emit(self._c(f"✗ failed after {elapsed}", BOLD, RED))
        else:
            self._emit(self._c(f"✓ completed in {elapsed}", BOLD, GREEN))
        self._emit(self._c("  " + " · ".join(parts), DIM))

        self._log(f"=== end: {'FAILED' if failed else 'OK'} after {elapsed}")
        if self._logfile:
            try:
                self._logfile.close()
            except OSError:
                pass
            self._logfile = None
