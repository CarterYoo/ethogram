"""The model runner works the same through Codex and Claude Code: fake CLIs record what they were given and answer
the way the real ones do."""
import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from swarmgraph import llm  # noqa: E402

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}

FAKE_CODEX = r'''
import json, os, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
log = os.environ["FAKE_LOG"]
with open(log, "a") as f:
    f.write(json.dumps({"argv": args, "cwd": os.getcwd(), "prompt": prompt}) + "\n")
out = args[args.index("-o") + 1]
with open(out, "w") as f:
    f.write(json.dumps({"ok": True}) if "--output-schema" in args else "plain answer")
'''

FAKE_CLAUDE = r'''
import json, os, sys
args = sys.argv[1:]
if args == ["--help"]:
    print(os.environ.get("FAKE_HELP", "  -p, --print\n  --restricted   Restricted mode\n  --tools <tools...>"))
    sys.exit(0)
prompt = sys.stdin.read()
log = os.environ["FAKE_LOG"]
calls = sum(1 for _ in open(log)) if os.path.exists(log) else 0
with open(log, "a") as f:
    f.write(json.dumps({"argv": args, "cwd": os.getcwd(), "prompt": prompt,
                        "claudecode": "CLAUDECODE" in os.environ}) + "\n")
mode = os.environ.get("FAKE_MODE", "structured")
res = {"type": "result", "subtype": "success", "is_error": False, "result": "plain answer"}
if mode == "structured" and "--json-schema" in args:
    res["structured_output"] = {"ok": True}
elif mode == "error_then_text_json":
    if calls == 0:
        print(json.dumps({"type": "result", "subtype": "error_max_structured_output_retries", "is_error": True,
                          "result": "could not produce the structured output"}))
        sys.exit(1)
    res["result"] = 'Here it is: {"ok": true}'
print(json.dumps(res))
'''


def fake(dirname, name, body):
    path = os.path.join(dirname, name)
    with open(path, "w") as f:
        f.write(f"#!{sys.executable}\n" + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


class Runner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "calls.jsonl")
        self.env = mock.patch.dict(os.environ, {
            "SWARMGRAPH_CODEX": fake(self.tmp, "codex", FAKE_CODEX),
            "SWARMGRAPH_CLAUDE": fake(self.tmp, "claude", FAKE_CLAUDE),
            "FAKE_LOG": self.log, "CLAUDECODE": "1"})
        self.env.start()
        os.environ.pop("SWARMGRAPH_MODEL", None)

    def tearDown(self):
        self.env.stop()

    def calls(self):
        with open(self.log) as f:
            return [json.loads(line) for line in f]

    def test_codex_isolated_with_schema(self):
        os.environ["SWARMGRAPH_AGENT"] = "codex"
        a = llm.Agent(retries=0, model="m")
        self.assertEqual(a.run("hello", SCHEMA)[0], {"ok": True})
        argv = self.calls()[0]["argv"]
        for flag in ("exec", "--ignore-user-config", "--output-schema", "--ephemeral"):
            self.assertIn(flag, argv)
        self.assertIn("shell_tool", argv)  # no shell for a reader or judge
        self.assertEqual(self.calls()[0]["prompt"], "hello")

    def test_claude_has_no_tools_and_no_user_setup(self):
        os.environ["SWARMGRAPH_AGENT"] = "claude"
        a = llm.Agent(retries=0, effort="minimal")
        self.assertEqual(a.run("hello", SCHEMA)[0], {"ok": True})
        c = self.calls()[0]
        argv = c["argv"]
        for flag in ("-p", "--restricted", "--strict-mcp-config", "--no-session-persistence", "--json-schema"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--effort") + 1], "low")
        self.assertNotIn("--allowedTools", argv)
        self.assertFalse(c["claudecode"])  # runs even when called from inside a Claude Code session

    def test_claude_analyst_gets_a_sandboxed_shell_in_its_workdir(self):
        os.environ["SWARMGRAPH_AGENT"] = "claude"
        work = tempfile.mkdtemp()
        llm.Agent(retries=0, shell=True, workdir=work, sandbox="workspace-write").run("analyse", SCHEMA)
        c = self.calls()[0]
        argv = c["argv"]
        self.assertEqual(os.path.realpath(c["cwd"]), os.path.realpath(work))
        self.assertIn("Bash", argv[argv.index("--tools") + 1].split(","))
        self.assertIn("Bash", argv[argv.index("--allowedTools") + 1:])
        self.assertTrue(json.loads(argv[argv.index("--settings") + 1])["sandbox"]["enabled"])

    def test_claude_retry_asks_for_the_json_in_words(self):
        os.environ.update({"SWARMGRAPH_AGENT": "claude", "FAKE_MODE": "error_then_text_json"})
        with mock.patch.object(llm.time, "sleep"):
            out, _ = llm.Agent(retries=1).run("hello", SCHEMA)
        self.assertEqual(out, {"ok": True})
        first, second = self.calls()
        self.assertIn("--json-schema", first["argv"])
        self.assertNotIn("--json-schema", second["argv"])
        self.assertIn("It must match this JSON schema", second["prompt"])

    def test_claude_error_is_reported(self):
        os.environ.update({"SWARMGRAPH_AGENT": "claude", "FAKE_MODE": "error_then_text_json"})
        with self.assertRaisesRegex(RuntimeError, "claude.*structured output"):
            llm.Agent(retries=0).run("hello", SCHEMA)

    def test_text_answers(self):
        for cli in llm.CLIS:
            os.environ["SWARMGRAPH_AGENT"] = cli
            self.assertEqual(llm.Agent(retries=0).run("hello")[0], "plain answer")

    def test_choice_of_cli(self):
        os.environ["SWARMGRAPH_AGENT"] = "gemini"
        with self.assertRaises(RuntimeError):
            llm.agent_cli()
        os.environ.pop("SWARMGRAPH_AGENT")
        self.assertEqual(llm.agent_cli(), "codex")
        with mock.patch.object(llm, "codex_path", return_value=None):
            self.assertEqual(llm.agent_cli(), "claude")

    def test_codex_cache_keys_are_unchanged(self):
        os.environ["SWARMGRAPH_AGENT"] = "codex"
        a = llm.Agent(effort="low", model="m")
        old = hashlib.sha256(json.dumps(["m", "low", SCHEMA, "p"], sort_keys=True).encode()).hexdigest()
        self.assertEqual(a.key("p", SCHEMA), old)
        os.environ["SWARMGRAPH_AGENT"] = "claude"
        self.assertNotEqual(llm.Agent(effort="low", model="m").key("p", SCHEMA), old)

    def test_claude_too_old_to_isolate_is_refused(self):
        os.environ.update({"SWARMGRAPH_AGENT": "claude", "FAKE_HELP": "  -p, --print"})
        llm._CHECKED.clear()
        try:
            with self.assertRaisesRegex(RuntimeError, "cannot isolate"):
                llm.Agent()
            self.assertFalse(os.path.exists(self.log))  # nothing was asked of the model
        finally:
            llm._CHECKED.clear()

    def test_old_name_still_works(self):
        self.assertIs(llm.Codex, llm.Agent)


if __name__ == "__main__":
    unittest.main()
