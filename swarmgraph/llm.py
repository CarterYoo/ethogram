"""LLM calls through a local agent CLI (default: Codex `codex exec`, using the user's ChatGPT login — no API key).
Swap the command with $SWARMGRAPH_CODEX, the model with $SWARMGRAPH_MODEL. Results are cached in the work store.

Parallelism: every call is its own process, so the LLM stages (tag, cards, auto) run calls in worker threads.
$SWARMGRAPH_WORKERS sets the default number of parallel calls (12). Child processes are tracked and stopped when
swarmgraph exits or is interrupted, so a stopped run never leaves LLM calls running."""
import atexit
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

BUNDLED_CODEX = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"


_LIVE, _LOCK = set(), threading.Lock()


def default_workers():
    try:
        return max(1, int(os.environ.get("SWARMGRAPH_WORKERS", "12")))
    except ValueError:
        return 12


def _kill_group(p, sig=signal.SIGKILL):
    """end a CLI call and everything it started (it runs in its own process group)"""
    try:
        os.killpg(p.pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            p.kill()
        except OSError:
            pass
    try:
        p.wait(5)
    except subprocess.TimeoutExpired:
        pass


def stop_all():
    """Terminate every LLM call still running (used at exit and on SIGINT/SIGTERM)."""
    with _LOCK:
        procs = list(_LIVE)
    for p in procs:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.time() + 3
    for p in procs:
        try:
            p.wait(max(0.1, deadline - time.time()))
        except (subprocess.TimeoutExpired, OSError):
            _kill_group(p)


def install_signal_handlers():
    def handler(signum, frame):
        stop_all()
        sys.exit(128 + signum)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handler)


atexit.register(stop_all)


# Calls must not inherit the user's own Codex setup: its MCP servers and plugins (calendars, course sites, to-do lists,
# a browser, computer use), web search, image generation or helper agents. Sub-agents read untrusted text (an agent's
# record holds other agents' messages, web pages, game moves), and a prompt in that text must find no tool to use.
# Measured: with the user config a call could call web search, a computer-use REPL and spawn agents; with these
# flags only a read-only sandbox's basics remain.
NO_TOOLS = ("apps", "browser_use", "browser_use_external", "computer_use", "image_generation", "goals", "multi_agent",
            "multi_agent_v2", "agent_message_board", "code_mode_host", "plugins", "remote_plugin", "sleep_tool",
            "skill_search", "tool_suggest", "in_app_browser", "hooks")
SHELL = ("shell_tool", "unified_exec")


def user_model():
    """the model the user's Codex config names (kept, since the user config itself is not loaded)"""
    import re
    try:
        with open(os.path.join(os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex")), "config.toml")) as f:
            for line in f:
                if line.startswith("["):
                    break
                m = re.match(r'\s*model\s*=\s*"([^"]+)"', line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


def isolation(shell=False):
    """CLI flags for a call that sees no user tools (shell: keep the sandboxed shell, for analysts). In this Codex
    the shell runs through the code-mode host: an analyst without it cannot run a command (measured: three analyst
    runs answered in 30 s that they could read nothing)"""
    flags = ["--ignore-user-config", "-c", 'web_search="disabled"']
    for f in NO_TOOLS + (() if shell else SHELL):
        if shell and f == "code_mode_host":
            continue
        flags += ["--disable", f]
    return flags


def codex_path():
    for p in (os.environ.get("SWARMGRAPH_CODEX"), shutil.which("codex"), BUNDLED_CODEX):
        if p and os.path.exists(p):
            return p
    raise RuntimeError("no LLM CLI found: install Codex (`npm i -g @openai/codex`, `codex login`) or set SWARMGRAPH_CODEX")


class Codex:
    """One non-interactive turn per call; safe to run in parallel threads (each call is its own process)."""

    def __init__(self, effort="low", model=None, timeout=900, retries=1, sandbox="read-only", workdir=None):
        self.bin = codex_path()
        self.effort, self.timeout, self.retries = effort, timeout, retries
        self.model = model or os.environ.get("SWARMGRAPH_MODEL") or user_model()
        self.sandbox, self.workdir = sandbox, workdir

    def key(self, prompt, schema):
        return hashlib.sha256(json.dumps([self.model, self.effort, schema, prompt], sort_keys=True).encode()).hexdigest()

    def run(self, prompt, schema=None):
        """→ (parsed JSON if schema else text, seconds)"""
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "last.txt")
            cmd = [self.bin, "exec", "--skip-git-repo-check", "--ephemeral", "--color", "never", "-s", self.sandbox,
                   "-C", self.workdir or tmp, "-o", out, "-c", f'model_reasoning_effort="{self.effort}"'] + isolation()
            if self.model:
                cmd += ["-m", self.model]
            if schema:
                path = os.path.join(tmp, "schema.json")
                with open(path, "w") as f:
                    json.dump(schema, f)
                cmd += ["--output-schema", path]
            cmd.append("-")
            prompt_path, log_path = os.path.join(tmp, "prompt.txt"), os.path.join(tmp, "log.txt")
            with open(prompt_path, "w") as f:
                f.write(prompt)
            err = ""
            for attempt in range(self.retries + 1):
                t = time.time()
                # Files, not pipes, and a wall-clock deadline checked here: communicate(timeout=) measures monotonic
                # time, which stops while a Mac sleeps, and a killed CLI's children (its MCP servers) can keep a pipe
                # open so a second communicate() never returns — both left calls hanging for hours. The CLI runs in its
                # own process group so a timeout ends it and everything it started.
                with open(prompt_path) as fin, open(log_path, "w") as flog:
                    p = subprocess.Popen(cmd, stdin=fin, stdout=flog, stderr=subprocess.STDOUT, start_new_session=True)
                with _LOCK:
                    _LIVE.add(p)
                try:
                    while p.poll() is None:
                        if time.time() - t > self.timeout:
                            _kill_group(p)
                            break
                        time.sleep(1)
                    if p.returncode == 0 and os.path.exists(out) and os.path.getsize(out):
                        text = open(out).read()
                        return (json.loads(text) if schema else text), time.time() - t
                    err = (f"timed out after {self.timeout}s" if time.time() - t > self.timeout else
                           open(log_path, errors="replace").read()[-1500:])
                except json.JSONDecodeError as ex:
                    err = f"invalid JSON: {ex}"
                finally:
                    with _LOCK:
                        _LIVE.discard(p)
                time.sleep(3 * (attempt + 1))
            raise RuntimeError(f"LLM call failed: {err}")

    def cached(self, work, prompt, schema=None):
        """Cache lookups/writes must happen on the thread that owns `work`; use run() inside worker threads."""
        k = self.key(prompt, schema)
        row = work.execute("SELECT response FROM llm_cache WHERE key=?", (k,)).fetchone()
        if row:
            return json.loads(row[0]), 0.0
        result, secs = self.run(prompt, schema)
        work.execute("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?,?,?)",
                     (k, self.model or "default", self.effort, time.strftime("%Y-%m-%d %H:%M:%S"), secs, json.dumps(result)))
        work.commit()
        return result, secs
