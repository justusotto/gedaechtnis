#!/usr/bin/env python3
"""cloudsess.py — where a session launched with `claude --cloud` actually ran (CLOUDBRIDGE-1).

Asking for a cloud session is not having one. A launcher ending in `exec claude --cloud "<task>" …`
can produce a session on the local machine instead, a thread of a Remote Control host, with the
whole local setup loaded and plan usage spent, while its brief says "you run in the cloud". So a launch ledger row is called `cloud` only on evidence:

    cloud              the dispatch printed a session id or a claude.ai/code address, that text
                       names no Remote Control host of this machine, and no live thread of a local
                       host carries that id or the launch's name
    local-host         a live thread of a local Remote Control host carries the id or the name,
                       or the dispatch text names a local host; once seen it is kept: a thread
                       that has ended does not turn the row back into `cloud`
    cloud-unconfirmed  `--cloud` was asked for and nothing the tool could read says where it ran

What it cannot see: a session that ran on a local host and ended between two looks, with no id
captured, stays `cloud-unconfirmed` — never `cloud`. The wording of the CLI's dispatch output is
not documented; the two patterns below are the id forms the registry and the docs use.

Pure functions: the screen text, the live view and the process table are passed in.
"""
from __future__ import annotations
import json, re, shlex

KINDS = ("cloud", "local-host", "cloud-unconfirmed")
_CLOUD_FLAG = re.compile(r"^\s*exec\s+(?:\S*/)?claude\b[^\n]*?\s--cloud\b", re.M)
_URL = re.compile(r"https://claude\.ai/code/[A-Za-z0-9_/-]+")
_ID = re.compile(r"\b(?:cse|session)_([0-9][A-Za-z0-9]+)")
LOCAL_LINE = ("LANDED ON THIS MAC, NOT IN THE CLOUD: {name} asked for --cloud and runs as a thread of "
              "the Remote Control host {host!r}. It uses this machine's files, hooks and plan usage, "
              "not a cloud VM and not the cloud credit. A task text that says \"you run in the "
              "cloud\" is wrong for it.")


# CLOUDBRIDGE-1B: what the CLI's own debug log says about where the repository came from. SEEN on
# 2.1.284: "GitHub app is not installed on <owner>/<repo>", then "[teleportToRemote] Bundling
# (reason: github_preflight_failed)", then a create payload with `"sources": []` and a
# `seed_bundle_file_id` — an upload of the local repository with no remote, so no push.
_BUNDLING = re.compile(r"\[teleportToRemote\] Bundling \(reason: ([a-z_]+)\)")
_NO_APP = re.compile(r"GitHub app is not installed on (\S+)")
_CREATED = re.compile(r"Successfully created remote session: (\S+)")
BUNDLE_WHY = {
    "github_preflight_failed": "the Claude GitHub App is not installed on this repository",
    "no_github_remote": "the folder has no GitHub remote",
    "forced_bundle": "CCR_FORCE_BUNDLE is set",
    "branch_not_on_remote": "the current branch is not on the remote",
    "dir_sync_local_changes": "the checkout differs from the remote (local commits or changes)",
}
BUNDLE_LINE = ("BUNDLE, NO REMOTE: {name} was sent as an upload of the local repository, not as a "
               "GitHub clone, because {why}. It has no remote and cannot push; its work does not "
               "come back. {fix}")
BUNDLE_FIX = {
    "github_preflight_failed": "Install the app on the repository (github.com/apps/claude → Configure), "
                               "or start the session at claude.ai/code.",
}
NO_LOG_LINE = ("clone or bundle NOT CHECKED: the launcher's `exec claude` line passes no literal "
               "`--debug-file <path>`, and only that file says whether the session got a GitHub clone.")


def debug_file(text: str) -> str | None:
    """The literal `--debug-file` path of a launcher's `exec claude --cloud` line, read as the shell
    words it is (a quoted task text or a comment that mentions the flag is not the flag). None when
    there is none, or the path is built from a variable or starts with `~`: nothing here can know
    what the shell makes of it, and a wrong path would be read as "the log says nothing"."""
    text = (text or "").replace("\\\n", " ")
    for line in text.splitlines():
        if not re.match(r"\s*exec\s+(?:\S*/)?claude\b", line):
            continue
        lex = shlex.shlex(line, posix=True)
        lex.whitespace_split, lex.commenters = True, ""
        try:
            words = list(lex)
        except ValueError:
            continue
        words = words[:next((k for k, w in enumerate(words) if w.startswith("#")), len(words))]
        if "--cloud" not in words:
            continue
        for k, w in enumerate(words):
            path = (w[len("--debug-file="):] if w.startswith("--debug-file=")
                    else words[k + 1] if w == "--debug-file" and k + 1 < len(words) else None)
            if path is not None:
                return None if (not path or path.startswith("~") or "$" in path or "`" in path) else path
    return None


def _payload(log: str) -> dict | None:
    """The create payload the CLI logged, as `"Creating session with payload: {…}"` (a JSON string
    holding JSON). The last one; None when there is none or it does not parse."""
    for line in reversed(log.split("\n")):
        k = line.find('"Creating session with payload: ')
        if k < 0:
            continue
        try:
            inner = json.loads(line[k:].rstrip())
            got = json.loads(inner[len("Creating session with payload: "):])
        except (ValueError, TypeError):
            continue                                    # a line that only quotes the words: look further up
        return got if isinstance(got, dict) else None
    return None


def source_facts(log: str | None) -> dict:
    """{"source": "bundle" | "github" | None, "reason", "repo", "remote_ids"} from the debug log.
    Read from the create payload only, never from other lines (hook output and task texts are in
    the log too): `github` when `session_context.sources` holds a git repository, `bundle` when it
    holds none and a `seed_bundle_file_id` was sent. None = the log does not say."""
    log = log or ""
    ctx = (_payload(log) or {}).get("session_context")
    ctx = ctx if isinstance(ctx, dict) else {}
    srcs = ctx.get("sources") if isinstance(ctx.get("sources"), list) else []
    source = ("github" if any(isinstance(x, dict) and x.get("type") == "git_repository" for x in srcs)
              else "bundle" if ctx.get("seed_bundle_file_id") else None)
    reasons = _BUNDLING.findall(log)
    repo = _NO_APP.search(log)
    ids = sorted({i for c in _CREATED.findall(log) for i in _ID.findall(c)})
    return {"source": source, "reason": reasons[-1] if reasons and source == "bundle" else None,
            "repo": repo.group(1) if repo else None, "remote_ids": ids}


def bundle_line(name: str, reason: str | None) -> str:
    why = BUNDLE_WHY.get(reason or "", f"the CLI gave the reason {reason!r}" if reason else "the CLI gave no reason")
    return BUNDLE_LINE.format(name=name, why=why, fix=BUNDLE_FIX.get(reason or "", "Start it at claude.ai/code instead."))


def is_cloud_launcher(text: str) -> bool:
    """The launcher's `exec claude` line (backslash continuations joined) passes `--cloud`."""
    text = (text or "").replace("\\\n", " ")
    text = re.sub(r"\"[^\"\n]*\"|'[^'\n]*'", '""', text)      # a quoted task text is not a flag
    return bool(_CLOUD_FLAG.search(text))


def dispatch_facts(screen: str | None) -> dict:
    """What the dispatch printed: id suffixes and the session address, if any."""
    text = screen or ""
    joined = re.sub(r"[ \t]*\n[ \t]*", "", text)        # an id cut where the screen wrapped the line
    url = _URL.search(joined) or _URL.search(text)
    return {"remote_ids": sorted(set(_ID.findall(text)) | set(_ID.findall(joined))),
            "url": url.group(0) if url else None}


def local_threads(view: list[dict], table: dict | None = None, own_pid=None) -> list[dict]:
    """The live sessions of THIS machine that could be the one asked for: every thread of a Remote
    Control host, and any other live session except the launcher's own process. `text` is what
    each carries of a remote id: its record's `bridgeSessionId` and its command line."""
    out = []
    for e in view or []:
        if e.get("kind") not in ("THREAD", "SESSION", "FORK") or (
                e.get("kind") != "THREAD" and e.get("pid") == own_pid):
            continue
        toks = ((table or {}).get(e.get("pid")) or {}).get("toks") or []
        out.append({"pid": e.get("pid"), "name": e.get("name"), "thread": e.get("kind") == "THREAD",
                    "host": e.get("host") or f"this Mac (pid {e.get('pid')})",
                    "text": " ".join([e.get("remote") or ""] + [str(t) for t in toks])})
    return out


def _carries(text: str, tokens: set[str]) -> bool:
    """A local session carries one of the launch's ids. An id read off a screen may be cut where the
    line wrapped, so a captured id that is the START of a local one (8+ characters) counts too."""
    theirs = set(_ID.findall(text))
    return any(t in theirs or (len(t) >= 6 and t in text)
               or (len(t) >= 8 and any(x.startswith(t) for x in theirs)) for t in tokens)


def landing(row: dict, view: list[dict], table: dict | None = None, screen: str | None = None) -> dict:
    """{"kind": one of KINDS, "host": the local host or None, "why": one clause}."""
    if row.get("kind") == "local-host":
        return {"kind": "local-host", "host": row.get("host"), "why": "seen on a local host before"}
    ids = set(row.get("remote_ids") or [])
    if row.get("url"):
        ids.add(row["url"].rstrip("/").rsplit("/", 1)[-1].split("_", 1)[-1])
    for t in local_threads(view, table, row.get("pid")):
        if _carries(t["text"], ids):
            return {"kind": "local-host", "host": t["host"],
                    "why": f"a live session of this machine carries its remote id (pid {t['pid']})"}
        if t["thread"] and row.get("name") and t["name"] == row["name"]:
            return {"kind": "local-host", "host": t["host"],
                    "why": f"a live thread of this host carries its name (pid {t['pid']})"}
    hosts = [e.get("name") for e in view or [] if e.get("kind") == "HOST" and e.get("name")]
    named = next((h for h in hosts if h in (screen or "")), None)
    if named:
        return {"kind": "local-host", "host": named, "why": "the dispatch output names this host"}
    if ids or row.get("url"):
        return {"kind": "cloud", "host": None,
                "why": "a session id was printed and no local host or thread carries it"}
    return {"kind": "cloud-unconfirmed", "host": None,
            "why": "no session id was captured, so nothing says where it ran"}


def ls_line(row: dict, verdict: dict) -> str:
    """The one `sessions.py ls` line of a session that was launched with `--cloud`."""
    where = row.get("url") or (f"session_{row['remote_ids'][0]}" if row.get("remote_ids") else "no id captured")
    tail = [f"repo {row.get('cwd') or 'unknown'}", f"row {row.get('row') or 'none'}"]
    if row.get("source") == "bundle" and verdict["kind"] == "cloud":
        tail.insert(0, "BUNDLE, NO REMOTE")
    if verdict["kind"] == "cloud":
        return f"- {row['name']} · cloud · {where} · " + " · ".join(tail)
    if verdict["kind"] == "local-host":
        return (f"- {row['name']} · LOCAL HOST {verdict.get('host') or 'unknown'} — asked for --cloud, "
                f"ran on this Mac ({verdict['why']}) · {where} · " + " · ".join(tail))
    return (f"- {row['name']} · cloud-unconfirmed — asked for --cloud; {verdict['why']} · "
            + " · ".join(tail))
