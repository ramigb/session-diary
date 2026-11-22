#!/usr/bin/env python3
"""Self-test with a fake `claude` on PATH: no real model calls, nothing outside a temp dir.
Run: python3 test_diary.py"""
import json, os, stat, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
tmp = tempfile.mkdtemp()
home, bindir = os.path.join(tmp, "diary"), os.path.join(tmp, "bin")
os.makedirs(bindir)

# fake claude: haiku -> an entry, sonnet -> a dream (or junk when FAKE_BAD_DREAM is set)
fake = os.path.join(bindir, "claude")
with open(fake, "w") as f:
    f.write('''#!/usr/bin/env python3
import json, os, sys
sys.stdin.read()
model = sys.argv[sys.argv.index("--model") + 1]
if model == "haiku":
    out = "**What we did:** tested %s" % os.environ.get("FAKE_TAG", "")
elif os.environ.get("FAKE_BAD_DREAM"):
    out = "no markers here"
else:
    out = "=== LONG-AGO ===\\n- 2026-01-01 · proj: we built a test that checks the dream path works.\\n=== SUGGESTIONS ===\\nnone"
print(json.dumps({"result": out, "is_error": False, "usage": {}}))
''')
os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)

env = dict(os.environ, DIARY_HOME=home, DIARY_USER="Sam", PATH=bindir + os.pathsep + os.environ["PATH"])
env.pop("DIARY_CHILD", None)
env["CLAUDE_CODE_ENTRYPOINT"] = "cli"


def run(*args, stdin="", **extra):
    return subprocess.run([sys.executable, os.path.join(HERE, "diary.py")] + list(args), input=stdin,
                          capture_output=True, text=True, env=dict(env, **extra), timeout=60)


def line(kind, content, **kw):
    return json.dumps(dict(type=kind, message={"content": content}, timestamp="2026-01-01T10:00:00Z", **kw))


transcript = os.path.join(tmp, "s.jsonl")
with open(transcript, "w") as f:
    f.write("\n".join([
        line("user", "hello <system-reminder>SECRET REMINDER</system-reminder>"),
        line("assistant", [{"type": "text", "text": "hi Sam"}, {"type": "thinking", "thinking": "THINKING"}]),
        line("user", "sidechain text", isSidechain=True),
        line("user", "<command-name>/clear</command-name>"),
        line("user", "second"),
        line("user", [{"type": "text", "text": "third"}]),
    ]) + "\n")

# extract: reminders, thinking, sidechains and slash commands are dropped
sys.path.insert(0, HERE)
os.environ.update(DIARY_HOME=home, DIARY_USER="Sam")
import diary
lines, n_user, ts = diary.extract(transcript)
assert lines == ["Sam: hello", "Claude: hi Sam", "Sam: second", "Sam: third"], lines
assert n_user == 3 and ts.startswith("2026-01-01")
assert "Sam" in diary.DIARY_PROMPT and "Sam" in diary.DREAM_PROMPT

# write: one entry; a resumed session overwrites it instead of adding a second
for tag in ("one", "two"):
    r = run("write", transcript, "abcdef1234", "/x/proj", FAKE_TAG=tag)
    assert r.returncode == 0, r.stderr
entries = os.listdir(os.path.join(home, "entries"))
assert entries == ["2026-01-01_abcdef12.md"], entries
body = open(os.path.join(home, "entries", entries[0])).read()
assert "tested two" in body and "· proj ·" in body, body

def set_state(**kw):
    path = os.path.join(home, "state.json")
    s = json.load(open(path)); s.update(kw); json.dump(s, open(path, "w"))


import time
set_state(last_dream=time.time())   # keep `start` from detaching a dream in the background
# start: shows the undreamed entry
r = run("start", stdin=json.dumps({"session_id": "zzz"}))
assert "tested two" in r.stdout, r.stdout

# a bad dream is rejected and long-ago is left alone
run("dream", FAKE_BAD_DREAM="1")
assert not os.path.exists(os.path.join(home, "long-ago.md"))
assert os.path.exists(os.path.join(home, "dream-rejected.txt"))

# a good dream writes long-ago and marks the entry dreamed (daily cap is 1, so reset it)
set_state(dreams=0)
run("dream")
assert "dream path works" in open(os.path.join(home, "long-ago.md")).read()
assert entries[0] in json.load(open(os.path.join(home, "state.json")))["dreamed"]
r = run("start", stdin="{}")
assert "## Long ago" in r.stdout and "tested two" not in r.stdout, r.stdout

# headless runs (claude -p, SDK) get no diary
r = run("start", stdin="{}", CLAUDE_CODE_ENTRYPOINT="sdk-cli")
assert r.stdout == "", r.stdout
print("all good")
