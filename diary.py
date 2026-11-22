#!/usr/bin/env python3
"""Session diary: Claude's short memories of recent sessions.

  diary.py end     SessionEnd hook: detach a `write` for the session that just ended
  diary.py start   SessionStart hook: print long-ago + undreamed entries; maybe start a dream
  diary.py write <transcript> <session_id> <cwd>   Haiku writes one diary entry
  diary.py dream   Sonnet folds entries into long-ago.md and suggests memory updates

Files live in DIARY_HOME (default ~/.claude/diary). DIARY_USER (optional) is the
name of the human partner the diary talks about; default "the user". Nothing is deleted: entries stay
on disk after a dream, and transcripts stay in ~/.claude/projects.
Python 3.8 compatible (hooks run /usr/bin/python3).
"""
import datetime as dt, glob, json, os, re, shutil, subprocess, sys, time

HOME = os.environ.get("DIARY_HOME") or os.path.expanduser("~/.claude/diary")
ENTRIES = os.path.join(HOME, "entries")
LONG_AGO = os.path.join(HOME, "long-ago.md")
SUGGEST = os.path.join(HOME, "suggested-memory.md")
STATE = os.path.join(HOME, "state.json")
LOG = os.path.join(HOME, "log.txt")
CHILD = "DIARY_CHILD"            # set in the nested claude so its hooks do nothing
MAX_WRITES_PER_DAY = 15          # hard cap: hook-started runs skip the nested-claude guard
MAX_DREAMS_PER_DAY = 1
DREAM_EVERY_H = 20
COOLDOWN_H = 3                   # after an auth/limit error, stop calling the model
INJECT_CAP = 8000                # SessionStart stdout is capped at 10,000 chars
MIN_USER_MSGS = 3
USER = os.environ.get("DIARY_USER", "").strip()
WHO = USER or "the user"           # used inside prompts
LABEL = USER or "User"             # speaker label in the dialogue fed to the model

DIARY_PROMPT = """You write a short diary entry about one Claude Code session between {who} and Claude, so that a future Claude session can remember it. Write as Claude ("we", "I"), warm but exact. At most ~180 words. Use exactly these lines/sections:
**What we did:** 1-3 sentences.
**How {who} seemed:** mood and what they cared about, based only on their own words.
**Lasting:** bullets of things worth remembering for weeks (preferences, decisions, projects, people). "none" if none.
**Open threads:** unfinished things, or "none".
**Left off:** one line.
Only state what the transcript shows: never turn a worry or a maybe into a decision. If {who} corrects or overrides something Claude said, their correction wins, and their latest position is the one that counts. Never include secrets, keys, tokens, passwords, emails or phone numbers. Output only the entry.""".format(who=WHO)

DREAM_PROMPT = """You are Claude's "dreaming" pass: offline, you fold recent session diary entries into a compact long-term memory for future Claude sessions with {who}.
Inputs: CURRENT LONG-AGO (may be empty), NEW DIARY ENTRIES, THE USER'S OWN WORDS from those sessions, CURRENT SUGGESTIONS.
The user's own words ({who}) are ground truth. Check every "Lasting" fact in the entries against them: if their words contradict it or don't support it, fix it or drop it. Diary entries are summaries and can be wrong.
Output exactly two sections with these marker lines:
=== LONG-AGO ===
A rewritten long-ago memory, newest first. Each item starts with its date and project. Merge repeats, drop trivia, keep what still matters: lasting facts, how {who} is, milestones, threads still open. Older items get shorter. If something was resolved or superseded, update or drop it. At most 40 lines.
=== SUGGESTIONS ===
Up to 5 facts that should become durable memory notes, each with its evidence: the date and a direct quote of THEIR words (never quote a diary entry; no quote, no suggestion). Keep earlier suggestions that are still valid. Write "none" if none.
Never include secrets, keys, tokens, passwords, emails or phone numbers.""".format(who=WHO)


def log(msg):
    os.makedirs(HOME, exist_ok=True)
    with open(LOG, "a") as f:
        f.write("%s %s\n" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))


def atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def load_state():
    try:
        with open(STATE) as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    today = dt.date.today().isoformat()
    if s.get("day") != today:
        s.update(day=today, writes=0, dreams=0)
    s.setdefault("dreamed", [])
    return s


def save_state(s):
    atomic_write(STATE, json.dumps(s, indent=1))


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


# ---------- transcript -> plain dialogue (no tool output, no thinking, no reminders) ----------

REMINDER = re.compile(r"<system-reminder[^>]*>.*?</system-reminder[^>]*>", re.S)


def clean(text):
    return REMINDER.sub("", text).strip()


def extract(transcript, users_only=False):
    """Return (lines, n_user_msgs, first_timestamp)."""
    lines, n_user, first_ts = [], 0, None
    with open(transcript, errors="ignore") as f:
        for raw in f:
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            if e.get("isSidechain") or e.get("isMeta") or e.get("type") not in ("user", "assistant"):
                continue
            content = (e.get("message") or {}).get("content")
            if e["type"] == "user":
                texts = [content] if isinstance(content, str) else \
                        [b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]
                for t in texts:
                    t = clean(t)
                    if not t or t.startswith(("<local-command", "<command-name", "<command-message")):
                        continue
                    n_user += 1
                    first_ts = first_ts or e.get("timestamp")
                    lines.append(LABEL + ": " + t[:3000])
            elif not users_only and isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip():
                        lines.append("Claude: " + clean(b["text"])[:1500])
    return lines, n_user, first_ts


def tail_chars(lines, cap):
    out, size = [], 0
    for line in reversed(lines):
        size += len(line) + 2
        if size > cap:
            break
        out.append(line)
    return "\n\n".join(reversed(out))


# ---------- model calls ----------

AUTH_OR_LIMIT = re.compile(r"limit|quota|auth|login|401|403|429|credit|overloaded", re.I)


def ask(model, system, prompt, stdin_text, timeout):
    """One nested claude call. Returns text, or None (and sets a cooldown on auth/limit errors)."""
    exe = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    env = dict(os.environ, **{CHILD: "1"})
    cmd = [exe, "-p", prompt, "--model", model, "--tools", "", "--no-session-persistence",
           "--system-prompt", system, "--output-format", "json", "--max-budget-usd", "1"]
    try:
        p = subprocess.run(cmd, input=stdin_text, capture_output=True, text=True,
                           timeout=timeout, env=env, cwd=HOME)
    except subprocess.TimeoutExpired:
        log("%s: timed out after %ss" % (model, timeout))
        return None
    try:
        r = json.loads(p.stdout)
    except ValueError:
        r = {"is_error": True, "result": (p.stdout + p.stderr)[-500:]}
    u = r.get("usage") or {}
    log("%s: cost_usd=%s in=%s cache_read=%s cache_write=%s out=%s error=%s" % (
        model, r.get("total_cost_usd"), u.get("input_tokens"), u.get("cache_read_input_tokens"),
        u.get("cache_creation_input_tokens"), u.get("output_tokens"), r.get("is_error")))
    if r.get("is_error") or p.returncode != 0 or not (r.get("result") or "").strip():
        msg = str(r.get("result") or p.stderr)[-300:]
        log("%s failed: %s" % (model, msg))
        if AUTH_OR_LIMIT.search(msg):
            s = load_state()
            s["cooldown_until"] = time.time() + COOLDOWN_H * 3600
            save_state(s)
            log("cooldown set for %sh, no retries" % COOLDOWN_H)
        return None
    return r["result"].strip()


def blocked(s, kind, cap):
    if time.time() < s.get("cooldown_until", 0):
        log("%s skipped: cooldown" % kind)
        return True
    if s.get(kind, 0) >= cap:
        log("%s skipped: daily cap %s reached" % (kind, cap))
        return True
    return False


# ---------- commands ----------

def headless():
    # claude -p / Agent SDK runs (scripts, harnesses, evals) report "sdk-*"; they get no diary
    return os.environ.get(CHILD) or os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk")


def transcript_headless(transcript):
    with open(transcript, errors="ignore") as f:
        for raw in f:
            if '"entrypoint"' in raw:
                try:
                    return str(json.loads(raw).get("entrypoint", "")).startswith("sdk")
                except ValueError:
                    pass
    return False

def detach(*args):
    os.makedirs(HOME, exist_ok=True)
    with open(LOG, "a") as out:
        subprocess.Popen([sys.executable, os.path.abspath(__file__)] + list(args),
                         stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)


def cmd_end():
    if headless():
        return
    h = json.load(sys.stdin)
    tp = h.get("transcript_path")
    if tp and os.path.isfile(tp):
        detach("write", tp, h.get("session_id", "unknown"), h.get("cwd", ""))


def cmd_write(transcript, sid, cwd):
    if transcript_headless(transcript):
        log("write skipped: %s is a headless session" % sid[:8])
        return
    lines, n_user, first_ts = extract(transcript)
    if n_user < MIN_USER_MSGS:
        log("write skipped: %s has %s user messages" % (sid[:8], n_user))
        return
    s = load_state()
    if blocked(s, "writes", MAX_WRITES_PER_DAY):
        return
    s["writes"] += 1
    save_state(s)
    body = ask("haiku", DIARY_PROMPT, "Write the diary entry for this session.", tail_chars(lines, 40000), 180)
    if not body:
        return
    day = (first_ts or dt.date.today().isoformat())[:10]
    project = os.path.basename(cwd.rstrip("/")) or "?"
    os.makedirs(ENTRIES, exist_ok=True)
    # a resumed session ends again: overwrite its entry instead of adding a second one
    old = glob.glob(os.path.join(ENTRIES, "*_%s.md" % sid[:8]))
    path = old[0] if old else os.path.join(ENTRIES, "%s_%s.md" % (day, sid[:8]))
    atomic_write(path, "# %s · %s · session %s\ntranscript: %s\n\n%s\n" % (day, project, sid[:8], transcript, body))
    if os.path.basename(path) in s.get("dreamed", []):   # changed since its dream: fold it in again
        s = load_state()
        s["dreamed"].remove(os.path.basename(path))
        save_state(s)
    log("wrote %s" % path)


def entries_newest_first():
    return sorted(glob.glob(os.path.join(ENTRIES, "*.md")), key=os.path.getmtime, reverse=True)


def cmd_start():
    if headless():
        return
    try:
        h = json.load(sys.stdin)
    except ValueError:
        h = {}
    parts = ["# Session diary (notes from past sessions, written by Claude; may be wrong)",
             "Files: %s (entries/, long-ago.md, full transcripts linked in each entry)." % HOME]
    if read(SUGGEST).strip() and not read(SUGGEST).strip().lower().startswith("none"):
        parts.append("Pending suggested memory updates in %s: review them with %s when it fits, "
                     "never apply silently." % (SUGGEST, WHO))
    la = read(LONG_AGO).strip()
    if la:
        parts.append("## Long ago\n" + la[:3000])
    room = INJECT_CAP - sum(len(p) + 2 for p in parts)
    s = load_state()
    recent = []
    for path in entries_newest_first():
        # dreamed entries already live, checked, in long-ago; showing them too would repeat their errors
        if h.get("session_id", "?")[:8] in os.path.basename(path) or os.path.basename(path) in s["dreamed"]:
            continue
        text = read(path).strip()
        if len(recent) == 3 or len(text) + 2 > room:
            break
        recent.append(text)
        room -= len(text) + 2
    if recent:
        parts.append("## Recent sessions, not yet dreamed (newest first)\n\n" + "\n\n".join(recent))
    if la or recent:
        print("\n\n".join(parts))
    pending = [p for p in entries_newest_first() if os.path.basename(p) not in s["dreamed"]]
    if pending and time.time() - s.get("last_dream", 0) > DREAM_EVERY_H * 3600 \
            and not blocked(s, "dreams", MAX_DREAMS_PER_DAY):
        detach("dream")


def cmd_dream():
    s = load_state()
    if blocked(s, "dreams", MAX_DREAMS_PER_DAY):
        return
    s["dreams"] += 1
    s["last_dream"] = time.time()   # set up front so parallel session starts don't double-dream
    save_state(s)
    pending = [p for p in reversed(entries_newest_first()) if os.path.basename(p) not in s["dreamed"]]
    if not pending:
        return
    entries, words = [], []
    for p in pending:
        text = read(p)
        entries.append(text)
        m = re.search(r"^transcript: (.+)$", text, re.M)
        if m and os.path.isfile(m.group(1).strip()):
            lines, _, _ = extract(m.group(1).strip(), users_only=True)
            words.append(text.splitlines()[0] + "\n" + tail_chars(lines, 15000))
    stdin_text = "\n\n".join([
        "## CURRENT LONG-AGO\n" + (read(LONG_AGO).strip() or "(empty)"),
        "## NEW DIARY ENTRIES (oldest first)\n" + tail_chars(entries, 30000),
        "## THE USER'S OWN WORDS\n" + tail_chars(words, 40000),
        "## CURRENT SUGGESTIONS\n" + (read(SUGGEST).strip() or "none"),
    ])
    out = ask("sonnet", DREAM_PROMPT, "Dream now.", stdin_text, 300)
    if not out:
        return
    # tolerate markdown around the markers (e.g. "**=== LONG-AGO ===**", "## LONG-AGO")
    m = re.search(r"^[#*= ]*LONG-AGO[*= ]*$\s*(.*?)\s*^[#*= ]*SUGGESTIONS[*= ]*$\s*(.*)", out, re.S | re.M | re.I)
    if not m or not (50 <= len(m.group(1)) <= 6000):
        atomic_write(os.path.join(HOME, "dream-rejected.txt"), out)
        log("dream output rejected (bad shape or size), saved to dream-rejected.txt; long-ago.md kept as is")
        return
    if os.path.exists(LONG_AGO):
        shutil.copy(LONG_AGO, LONG_AGO + ".bak")
    atomic_write(LONG_AGO, m.group(1).strip() + "\n")
    atomic_write(SUGGEST, m.group(2).strip() + "\n")
    s = load_state()
    s["dreamed"] = sorted(set(s["dreamed"]) | {os.path.basename(p) for p in pending})
    save_state(s)
    log("dreamed %s entries" % len(pending))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if cmd == "end":
            cmd_end()
        elif cmd == "start":
            cmd_start()
        elif cmd == "write" and len(sys.argv) == 5:
            cmd_write(*sys.argv[2:])
        elif cmd == "dream":
            cmd_dream()
        else:
            print(__doc__, file=sys.stderr)
            sys.exit(2)
    except Exception as e:  # a hook must never break the session
        log("%s crashed: %r" % (cmd, e))


if __name__ == "__main__":
    main()
