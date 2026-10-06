"""LLM calls through a local agent CLI, with the user's own login (no API key): Codex (`codex exec`) or Claude Code
(`claude -p`). $SWARMGRAPH_AGENT picks one (`codex` or `claude`); otherwise the first CLI found is used, Codex first.
$SWARMGRAPH_CODEX and $SWARMGRAPH_CLAUDE set the commands, $SWARMGRAPH_MODEL the model. Results are cached in the
work store.

Parallelism: every call is its own process, so the LLM stages (tag, cards, auto) run calls in worker threads.
$SWARMGRAPH_WORKERS sets the default number of parallel calls (12). Child processes are tracked and stopped when
swarmgraph exits or is interrupted, so a stopped run never leaves LLM calls running."""
import atexit
import glob
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

BUNDLED_CODEX = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"
BUNDLED_CLAUDE = "~/Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude"
CLIS = ("codex", "claude")


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


# Calls must not inherit the user's own agent setup: its MCP servers and plugins (calendars, course sites, to-do lists,
# a browser, computer use), web search, image generation or helper agents. Sub-agents read untrusted text (an agent's
# record holds other agents' messages, web pages, game moves), and a prompt in that text must find no tool to use.
# Measured with Codex: with the user config a call could call web search, a computer-use REPL and spawn agents; with
# these flags only a read-only sandbox's basics remain.
NO_TOOLS = ("apps", "browser_use", "browser_use_external", "computer_use", "image_generation", "goals", "multi_agent",
            "multi_agent_v2", "agent_message_board", "code_mode_host", "plugins", "remote_plugin", "sleep_tool",
            "skill_search", "tool_suggest", "in_app_browser", "hooks")
SHELL = ("shell_tool", "unified_exec")
# Claude Code: --restricted ignores the user's, project's and local settings files and keeps the file tools inside
# the working directory; --strict-mcp-config with no --mcp-config loads no MCP server; --tools "" leaves no built-in
# tool at all. An analyst gets a shell and the read-only file tools, its commands sandboxed to its working directory
# (a temporary copy of the data), with no network.
CLAUDE_SHELL_TOOLS = ("Bash", "Read", "Grep", "Glob")
CLAUDE_SANDBOX = {"sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True}}
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
INLINE_SCHEMA = "\n\nAnswer with one JSON object and nothing else. It must match this JSON schema:\n{schema}\n"


def user_model():
    """the model the user's Codex config names (kept, since the user config itself is not loaded)"""
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
    """Codex flags for a call that sees no user tools (shell: keep the sandboxed shell, for analysts). In this Codex
    the shell runs through the code-mode host: an analyst without it cannot run a command (measured: three analyst
    runs answered in 30 s that they could read nothing)"""
    flags = ["--ignore-user-config", "-c", 'web_search="disabled"']
    for f in NO_TOOLS + (() if shell else SHELL):
        if shell and f == "code_mode_host":
            continue
        flags += ["--disable", f]
    return flags


def claude_isolation(shell=False):
    """Claude Code flags for a call that sees no user tools (shell: the sandboxed shell and read-only file tools)"""
    flags = ["--no-session-persistence", "--restricted", "--strict-mcp-config", "--disable-slash-commands"]
    if shell:
        return flags + ["--tools", ",".join(CLAUDE_SHELL_TOOLS), "--allowedTools", *CLAUDE_SHELL_TOOLS,
                        "--settings", json.dumps(CLAUDE_SANDBOX)]
    return flags + ["--tools", ""]


def _found(*candidates):
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None


def codex_path(required=True):
    p = _found(os.environ.get("SWARMGRAPH_CODEX"), shutil.which("codex"), BUNDLED_CODEX)
    if p or not required:
        return p
    raise RuntimeError("Codex CLI not found: install it (`npm i -g @openai/codex`, `codex login`) or set SWARMGRAPH_CODEX")


def claude_path(required=True):
    bundled = sorted(glob.glob(os.path.expanduser(BUNDLED_CLAUDE)), key=lambda p: [int(x) if x.isdigit() else 0 for x in
                                                                                  re.findall(r"\d+", p)])
    p = _found(os.environ.get("SWARMGRAPH_CLAUDE"), shutil.which("claude"), os.path.expanduser("~/.claude/local/claude"),
               *bundled[::-1])
    if p or not required:
        return p
    raise RuntimeError("Claude Code CLI not found: install it (`npm i -g @anthropic-ai/claude-code`, then `claude` "
                       "and log in) or set SWARMGRAPH_CLAUDE")


_CHECKED = {}


def check_claude(path):
    """refuse a Claude Code too old to isolate a call (it has no --restricted): a sub-agent must never run with the
    user's own tools because the CLI was old"""
    if path not in _CHECKED:
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
        try:
            r = subprocess.run([path, "--help"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
                               env=env)
            _CHECKED[path] = "--restricted" in (r.stdout + r.stderr)
        except (OSError, subprocess.TimeoutExpired):
            _CHECKED[path] = False
    if not _CHECKED[path]:
        raise RuntimeError(f"this Claude Code ({path}) cannot isolate a call (no --restricted): update it with "
                           "`claude update`, or use Codex (SWARMGRAPH_AGENT=codex)")


def agent_cli():
    """which agent CLI runs the calls: $SWARMGRAPH_AGENT, else Codex if installed, else Claude Code"""
    want = os.environ.get("SWARMGRAPH_AGENT", "").strip().lower()
    if want:
        if want not in CLIS:
            raise RuntimeError(f"SWARMGRAPH_AGENT must be one of {', '.join(CLIS)}, not {want!r}")
        return want
    if codex_path(required=False):
        return "codex"
    if claude_path(required=False):
        return "claude"
    raise RuntimeError("no agent CLI found: install Codex (`npm i -g @openai/codex`, `codex login`) or Claude Code "
                       "(`npm i -g @anthropic-ai/claude-code`, then log in), or set SWARMGRAPH_CODEX / SWARMGRAPH_CLAUDE")


def _read(path):
    if not os.path.exists(path):
        return ""
    with open(path, errors="replace") as f:
        return f.read()


def _json_in(text):
    """the JSON object in a reply (the whole reply, or the outermost {...} in it)"""
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        m = re.search(r"\{.*\}", text or "", re.S)
        if not m:
            raise json.JSONDecodeError("no JSON object in the reply", text or "", 0)
        return json.loads(m.group(0))


class Agent:
    """One non-interactive turn per call through the agent CLI (Codex or Claude Code); safe to run in parallel threads
    (each call is its own process). shell: an analyst that runs commands in `workdir` (a sandboxed shell)."""

    def __init__(self, effort="low", model=None, timeout=900, retries=1, sandbox="read-only", workdir=None, shell=False,
                 cli=None):
        self.cli = cli or agent_cli()
        if self.cli not in CLIS:
            raise RuntimeError(f"unknown agent CLI {self.cli!r}")
        self.bin = codex_path() if self.cli == "codex" else claude_path()
        if self.cli == "claude":
            check_claude(self.bin)
        self.effort, self.timeout, self.retries = effort, timeout, retries
        self.model = model or os.environ.get("SWARMGRAPH_MODEL") or (user_model() if self.cli == "codex" else None)
        self.sandbox, self.workdir, self.shell = sandbox, workdir, shell

    def key(self, prompt, schema):
        # the CLI joins the key only when it is not Codex, so the calls cached before Claude Code was supported still hit
        parts = [self.model, self.effort, schema, prompt] + ([self.cli] if self.cli != "codex" else [])
        return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()

    def command(self, tmp, schema, inline=False):
        """(command, working directory, file the answer is written to); inline: the schema goes in the prompt instead
        (Claude Code's retry when its structured output failed)"""
        cwd = self.workdir or tmp
        out = os.path.join(tmp, "answer.txt")
        if self.cli == "codex":
            cmd = [self.bin, "exec", "--skip-git-repo-check", "--ephemeral", "--color", "never", "-s", self.sandbox,
                   "-C", cwd, "-o", out, "-c", f'model_reasoning_effort="{self.effort}"'] + isolation(self.shell)
            if self.model:
                cmd += ["-m", self.model]
            if schema:
                path = os.path.join(tmp, "schema.json")
                with open(path, "w") as f:
                    json.dump(schema, f)
                cmd += ["--output-schema", path]
            return cmd + ["-"], cwd, out
        cmd = [self.bin, "-p", "--output-format", "json"] + claude_isolation(self.shell)
        effort = "low" if self.effort in ("minimal", "none") else self.effort
        if effort in CLAUDE_EFFORTS:
            cmd += ["--effort", effort]
        if self.model:
            cmd += ["--model", self.model]
        if schema and not inline:
            cmd += ["--json-schema", json.dumps(schema)]
        return cmd, cwd, out  # the prompt goes in on stdin; the answer is stdout (one JSON object)

    def answer(self, text, schema):
        """the CLI's output -> the parsed JSON (with a schema) or the text"""
        if self.cli == "codex":
            return json.loads(text) if schema else text
        lines = [ln for ln in text.splitlines() if ln.lstrip().startswith("{")]
        res = json.loads(lines[-1]) if lines else json.loads(text)
        if res.get("is_error") or res.get("subtype", "success") != "success":
            raise RuntimeError(f"Claude Code: {res.get('subtype')}: {str(res.get('result'))[:500]}")
        if not schema:
            return res.get("result", "")
        got = res.get("structured_output")
        return got if isinstance(got, dict) else _json_in(res.get("result"))

    def run(self, prompt, schema=None):
        """→ (parsed JSON if schema else text, seconds)"""
        with tempfile.TemporaryDirectory() as tmp:
            prompt_path, log_path = os.path.join(tmp, "prompt.txt"), os.path.join(tmp, "log.txt")
            env = dict(os.environ)
            for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):  # a call made from inside a Claude Code session
                env.pop(k, None)
            err = ""
            for attempt in range(self.retries + 1):
                inline = self.cli == "claude" and bool(schema) and attempt > 0  # a retry asks for the JSON in words
                cmd, cwd, out = self.command(tmp, schema, inline)
                if os.path.exists(out):
                    os.remove(out)  # never read a previous attempt's answer
                with open(prompt_path, "w") as f:
                    f.write(prompt + (INLINE_SCHEMA.format(schema=json.dumps(schema)) if inline else ""))
                t = time.time()
                # Files, not pipes, and a wall-clock deadline checked here: communicate(timeout=) measures monotonic
                # time, which stops while a Mac sleeps, and a killed CLI's children (its MCP servers) can keep a pipe
                # open so a second communicate() never returns — both left calls hanging for hours. The CLI runs in its
                # own process group so a timeout ends it and everything it started.
                with open(prompt_path) as fin, open(log_path, "w") as flog:
                    fout = flog if self.cli == "codex" else open(out, "w")  # Claude Code answers on stdout
                    try:
                        p = subprocess.Popen(cmd, stdin=fin, stdout=fout, stderr=flog, cwd=cwd, env=env,
                                             start_new_session=True)
                    finally:
                        if fout is not flog:
                            fout.close()
                with _LOCK:
                    _LIVE.add(p)
                try:
                    while p.poll() is None:
                        if time.time() - t > self.timeout:
                            _kill_group(p)
                            break
                        time.sleep(0.2)
                    text = _read(out)
                    if p.returncode == 0 and text.strip():
                        return self.answer(text, schema), time.time() - t
                    if self.cli == "claude" and text.strip():
                        self.answer(text, schema)  # an error result explains itself
                    err = (f"timed out after {self.timeout}s" if time.time() - t > self.timeout else
                           _read(log_path)[-1500:] or f"exit status {p.returncode}")
                except json.JSONDecodeError as ex:
                    err = f"invalid JSON: {ex}"
                except RuntimeError as ex:
                    err = str(ex)
                finally:
                    with _LOCK:
                        _LIVE.discard(p)
                if attempt < self.retries:
                    time.sleep(3 * (attempt + 1))
            raise RuntimeError(f"LLM call failed ({self.cli}): {err}")

    def cached(self, work, prompt, schema=None):
        """Cache lookups/writes must happen on the thread that owns `work`; use run() inside worker threads."""
        k = self.key(prompt, schema)
        row = work.execute("SELECT response FROM llm_cache WHERE key=?", (k,)).fetchone()
        if row:
            return json.loads(row[0]), 0.0
        result, secs = self.run(prompt, schema)
        work.execute("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?,?,?)",
                     (k, self.model or f"{self.cli} default", self.effort, time.strftime("%Y-%m-%d %H:%M:%S"), secs,
                      json.dumps(result)))
        work.commit()
        return result, secs


Codex = Agent  # the name the package used before it ran on more than one CLI
