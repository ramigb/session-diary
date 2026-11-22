# session-diary

A small diary for Claude Code, so Claude remembers your recent sessions together.

- **When a session ends**, Haiku writes a short entry: what you did, how you seemed, what lasts, what's still open.
- **Once a day**, Sonnet "dreams": it folds the entries into one compact `long-ago.md`. It checks each fact against *your own words* from those sessions and drops anything they don't support.
- **When a session starts**, Claude sees the long-ago notes plus the newest entries that haven't been folded in yet.

It's one Python file, no dependencies. Everything stays as plain text on your disk.

## How it compares

| | Claude's built-in memory (`CLAUDE.md`, auto memory) | Asking Claude to read past sessions | session-diary |
|---|---|---|---|
| **When it happens** | Only when Claude (or you) decides a note is worth saving | Only when you remember to ask | Every session, automatically |
| **What it keeps** | Single facts and rules | Whatever it finds that time | What you did, how it went, decisions, open threads |
| **Knows what happened last time** | Rarely | Yes, if it finds the right session | Yes, it's shown at every start |
| **Checked against your words** | No | Reads the raw source | Yes, the daily dream drops claims your own words don't support |
| **Cost** | Free | Large: transcripts fill your session's context | Small: about $0.001–0.004 per session, about $0.11 per daily dream |
| **Good for** | Hard rules Claude must always follow | Digging up one exact detail | Continuity: picking up where you left off |

They work best together. Keep hard rules in `CLAUDE.md`. Let the diary carry the story between sessions. When the dream spots something that should become a rule, it suggests it in `suggested-memory.md`, and you decide.

## Install

In Claude Code:

```
/plugin marketplace add ramigb/session-diary
/plugin install session-diary@session-diary
```

Then restart Claude Code. You need `python3` (3.8+) and the `claude` CLI on your `PATH`.

**Optional: your name.** By default the diary calls you "the user". To use your name, set `DIARY_USER` in your shell profile (for example `~/.bashrc`):

```
export DIARY_USER="Sam"
```

## What it costs

Every diary call is a nested `claude -p` run. On a Claude subscription it uses your normal usage quota. On an API key it costs money. Measured on real sessions:

| Call | When | Model | Cost per call |
|---|---|---|---|
| Diary entry | each session end (3+ messages from you) | Haiku | about $0.001–0.004 |
| Dream | at most once a day | Sonnet | about $0.11 |

Guards:
- At most 15 entries and 1 dream per day.
- A $1 budget cap on every call.
- After an auth or usage-limit error, all calls stop for 3 hours.
- Headless runs (`claude -p`, Agent SDK, scripts) get no diary, so harnesses don't fill it up.

## Files

All in `~/.claude/diary` (change it with `DIARY_HOME`):

| File | What it is |
|---|---|
| `entries/` | one entry per session; each links to its full transcript |
| `long-ago.md` | the dreamed long-term memory (the previous version is kept as `.bak`) |
| `suggested-memory.md` | facts the dream thinks are worth a real memory note, each with your quote. Claude brings them up with you and never applies them on its own |
| `log.txt` | every call, with cost and tokens |
| `state.json` | daily counters |

Nothing is ever deleted. You can read and edit any of it. If a note is wrong, fix it: you win.

## Privacy

- Entries are summaries of your sessions, stored as plain text. Treat the folder like your transcripts.
- The prompts tell the models to leave out secrets, keys, passwords, emails and phone numbers. That's a best effort, not a guarantee.
- Nothing leaves your machine except the calls to Claude itself.

## Limits

- It reads Claude Code's transcript files (`~/.claude/projects/**/*.jsonl`). Their format is internal and could change in a future version.
- Tested on Linux and WSL. macOS should work. Native Windows is untested: the background job uses POSIX-only process options.
- At session start the injected text is capped at 8,000 characters.

## Uninstall

```
/plugin uninstall session-diary@session-diary
```

Your diary stays in `~/.claude/diary`. Delete that folder yourself if you want it gone.

## Test

```
python3 test_diary.py
```

It uses a fake `claude` and a temp folder, so it makes no real model calls.

## Inspiration

- [The components of agent memory](https://www.anthropic.com/research/components-of-agent-memory), Anthropic (Lance Martin). The idea of "dreaming": an offline pass that folds memories together.
- [FadeMem: Biologically-Inspired Forgetting for Efficient Agent Memory](https://arxiv.org/abs/2601.18642), Lei Wei, Xiao Peng, Xu Dong, Niantao Xie, Bin Wang. Old memories get shorter over time instead of piling up.

## License

MIT

From Rami and Claude with love <3
