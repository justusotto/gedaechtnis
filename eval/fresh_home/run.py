#!/usr/bin/env python3
"""fresh_home/run.py — q:CU-2026-09-10-FRESHHOME-1: the stranger's install path, end to end.

Council 3 (all five seats) asked for the one thing no other test covers: a completely FRESH
`HOME` (no config, no credentials, no `~/.claude/skills/gedaechtnis` symlink of its own) →
`init.py --yes --repo <scratch repo>` → one pinned `claude -p` from that repo with
`--setting-sources user,project` → proof, on DISK, that the plugin's hooks really loaded via the
INSTALL PATH (the symlink + config `init.py` writes) and not merely via whatever this machine's
own `~/.claude/skills/gedaechtnis` happens to carry.

    python3 run.py --dry-run                                   # everything except the model call
    python3 run.py --live --claude "$(command -v claude)" \\
                   --model claude-sonnet-5 --effort low --ceiling-usd 1

--------------------------------------------------------------------------------------------------
WHAT THIS EXERCISES, AND HOW IT DIFFERS FROM `memory_eval`'s `gedaechtnis` ARM
--------------------------------------------------------------------------------------------------

`memory_eval/run.py`'s vault arms (`sub_env`) deliberately point `GEDAECHTNIS_CONFIG` and
`GEDAECHTNIS_STATE_DIR` at a scratch location UNDER the fixture home, and — because `--live` there
runs with `--setting-sources project` — stage the plugin's own hook table into the fixture repo's
`.claude/settings.json` by hand (see that module's docstring, limitation 4). That proves the
HOOKS work; it does not prove the ORDINARY INSTALL does, because nothing there ever exercises
`~/.claude/skills/gedaechtnis` — the symlink `init.py` creates, which is the actual mechanism a
stranger's `claude` uses to find this plugin at all.

This harness does the opposite on purpose: `install_env()` overrides ONLY `HOME` (after stripping
any `GEDAECHTNIS_*` the parent process happens to carry) and sets nothing else. `init.py` then
resolves every path — config file, state dir, the skills symlink — through its own, real,
UNMODIFIED defaults (`hooks/config.py`), exactly as it would on a stranger's laptop. The `claude
-p` step runs with `--setting-sources user,project` (not `project` alone) specifically so the
USER-level skills directory — where the symlink lives — is loaded. **This test therefore exercises
the INSTALL path, not the staged-hooks path** — the distinction the build brief asked to be named
explicitly.

--------------------------------------------------------------------------------------------------
THE AUTH FINDING (read before assuming this needs credentials)
--------------------------------------------------------------------------------------------------

A fresh `HOME` has no Claude Code OAuth token, and it turns out the CLI does NOT fall back to the
macOS login Keychain when `HOME` is overridden: a probe run returned `"result": "Not logged in ·
Please run /login"`, `"is_error": true`, `"total_cost_usd": 0`, `"modelUsage": {}` — zero-cost, not
a partial charge.

But the SessionStart/UserPromptSubmit hooks still ran and `session.log` still gained a row, BEFORE
the API auth check failed: Claude Code's local hook pipeline executes independently of whether the
subsequent model call can authenticate. So the actual proof this test exists for — the plugin's
hooks loaded via the install path — is obtained at **zero cost, authenticated or not**. This
harness therefore does not need real credentials to prove its core claim; it separately records
whether the model call itself authenticated (informational), and only prices the call — importing
`price_result`/`load_pricing` from `memory_eval/run.py`, never a second copy of the price table —
when `modelUsage` is non-empty (i.e., the call actually reached the model).

--------------------------------------------------------------------------------------------------
"""
from __future__ import annotations
import argparse, datetime, importlib.util, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
EVAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "hooks"))
sys.path.insert(0, str(PLUGIN))
import init as init_mod  # noqa: E402  (region_name/lane_name/marker_lane, so this file never re-derives them)

MEMORY_EVAL_RUN = EVAL / "memory_eval" / "run.py"
CORE_SIX = ("Map", "Position", "Canon", "Patterns", "Errata", "Aporia")
PROBE_PROMPT = "reply with the single word ok"
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_EFFORT = "low"


class Refusal(Exception):
    """A run that must not proceed, saying exactly why. Never a stack trace to stdout."""


def _load_memory_eval():
    """Import `memory_eval/run.py` by path, for its pricing machinery ONLY
    (`load_pricing`/`price_result`/`LiveRefusal`) — imported, never copied, same reasoning as that
    module's own docstring about the price table it in turn imports from `scripts/concilium.py`."""
    spec = importlib.util.spec_from_file_location("_fresh_home_memory_eval", MEMORY_EVAL_RUN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------- fixture ----
def install_env(home: Path) -> dict:
    """The environment a stranger's `init.py`/`claude` actually runs under: every `GEDAECHTNIS_*`
    the PARENT process happens to carry is stripped (so this harness's own test run never leaks a
    setting into the fixture), and exactly one thing is overridden: `HOME`. Nothing else — no
    `GEDAECHTNIS_CONFIG`, no `GEDAECHTNIS_STATE_DIR`. That is the entire point of this file: every
    path the plugin touches must come from `hooks/config.py`'s own default resolution relative to
    this `HOME`, the same as it would for a stranger who has set nothing."""
    e = {k: v for k, v in os.environ.items() if not k.startswith("GEDAECHTNIS_")}
    e["HOME"] = str(home)
    return e


def make_scratch_repo(repo: Path) -> None:
    """A minimal real git repo — not required by `init.py --repo` (which accepts any directory),
    but the honest shape of "a stranger's project", and what let the manual probe run (recorded in
    this build's report) exercise `common.git_root` the same way a real checkout would."""
    repo.mkdir(parents=True, exist_ok=True)
    ident = ["-c", "user.name=fresh-home-eval", "-c", "user.email=fresh-home-eval@local"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True, stdin=subprocess.DEVNULL,
                   capture_output=True, text=True, timeout=30)
    subprocess.run(["git", "-C", str(repo), *ident, "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)


def resolve_state_dir(env: dict) -> Path:
    """`hooks/config.py`'s own `STATE`, resolved by actually importing that module under `env` —
    never re-typed here. This is what "derive the path from hooks/config.py's precedence, never
    hardcode" means in practice: a subprocess that imports the real module is the only way to be
    sure this file's idea of the precedence has not drifted from the module's."""
    code = ("import sys; sys.path.insert(0, %r); import config; print(config.STATE)"
           % str(PLUGIN / "hooks"))
    p = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=30)
    if p.returncode != 0:
        raise Refusal(f"could not resolve config.STATE under the fixture env: {p.stderr.strip()}")
    return Path(p.stdout.strip())


# --------------------------------------------------------------------- init ----
def run_init(repo: Path, vault: Path, env: dict) -> subprocess.CompletedProcess:
    p = subprocess.run([sys.executable, str(PLUGIN / "init.py"), "--repo", str(repo),
                        "--vault", str(vault), "--yes"],
                       cwd=str(repo), env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise Refusal(f"init.py exited {p.returncode}: {p.stderr.strip() or p.stdout.strip()}")
    return p


def assert_install_artifacts(home: Path, repo: Path, vault: Path, region: str) -> dict:
    """Every path `init.py`'s own docstring says it creates, checked on disk. Returns a
    {label: bool} map so a failing case names exactly which artifact is missing, rather than one
    bare assertion that could be satisfied by any of a dozen things going right."""
    found = {}
    for stem in CORE_SIX:
        found[f"vault/{region}/{stem}.md"] = (vault / region / f"{stem}.md").is_file()
    found["vault/Global/Kernel.md"] = (vault / "Global" / "Kernel.md").is_file()
    found["vault/Global/fleet-roster.md"] = (vault / "Global" / "fleet-roster.md").is_file()
    found["repo/.atlas-lane"] = (repo / ".atlas-lane").is_file()
    found["repo/CLAUDE.md"] = (repo / "CLAUDE.md").is_file()
    cfg_path = home / ".claude" / "gedaechtnis" / "config.json"
    found["config.json"] = cfg_path.is_file()
    names_vault = False
    if cfg_path.is_file():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            named = Path(os.path.expanduser(str(data.get("vault", ""))))
            names_vault = named.resolve() == vault.resolve()
        except (OSError, ValueError):
            pass
    found["config.json names the fixture vault"] = names_vault
    link = home / ".claude" / "skills" / "gedaechtnis"
    found["~/.claude/skills/gedaechtnis symlink"] = link.is_symlink()
    target_ok = False
    if link.is_symlink():
        try:
            target_ok = Path(os.path.realpath(str(link))) == PLUGIN.resolve()
        except OSError:
            pass
    found["symlink points at this plugin checkout"] = target_ok
    found["~/.claude/settings.json (outage check)"] = (home / ".claude" / "settings.json").is_file()
    return found


# --------------------------------------------------------------- the live call ----
def build_probe_cmd(claude_cmd: str, model: str, effort: str, max_turns: int) -> list[str]:
    """Exactly the argv named in the build brief and proven in the manual probe: the prompt
    immediately after the boolean `-p`, `--setting-sources user,project` (NOT `project` alone —
    that is what loads the user-level skills directory the symlink lives in), `--output-format
    json`, `--max-turns` pinned. `--model`/`--effort` are never optional here (a bare launch is
    never a routing decision, same rule `memory_eval`'s `build_live_cmd` follows)."""
    if not model or not effort:
        raise Refusal("--model and --effort are both required for --live — a bare launch is never "
                      "a routing decision.")
    return [str(claude_cmd), "-p", PROBE_PROMPT, "--model", model, "--effort", effort,
           "--setting-sources", "user,project", "--output-format", "json",
           "--max-turns", str(max_turns)]


def run_probe(claude_cmd: str, model: str, effort: str, repo: Path, env: dict,
             max_turns: int = 1, timeout: int = 120) -> dict:
    cmd = build_probe_cmd(claude_cmd, model, effort, max_turns)
    started = datetime.datetime.now().isoformat(timespec="seconds")
    proc = subprocess.run(cmd, cwd=str(repo), env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=timeout)
    ended = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        result = {"PARSE_FAILED": True, "stdout": proc.stdout[:20000], "stderr": proc.stderr[:4000]}
    return {"argv": cmd, "cwd": str(repo), "started": started, "ended": ended,
           "returncode": proc.returncode, "stderr_tail": proc.stderr.strip()[-2000:],
           "result": result}


def classify_auth(record: dict) -> str:
    """'authenticated' | 'unauthenticated' | 'unknown' — from the shape of the result JSON alone,
    never from a fixed string match on a whole sentence (the CLI's exact wording is not a contract
    this file should depend on more than it has to)."""
    result = record.get("result")
    if not isinstance(result, dict) or result.get("PARSE_FAILED"):
        return "unknown"
    text = str(result.get("result") or "").lower()
    if result.get("is_error") and "not logged in" in text:
        return "unauthenticated"
    if result.get("is_error") and "/login" in text:
        return "unauthenticated"
    if result.get("is_error"):
        return "unknown"
    return "authenticated"


def price_probe(memory_eval_mod, pricing, record: dict) -> dict:
    """USD for the probe call, or an explicit zero when there is nothing to price (an
    unauthenticated call carries no `modelUsage` at all — see the module docstring's AUTH
    FINDING). Never guesses a price for a call that never reached the model."""
    result = record.get("result") or {}
    if not isinstance(result, dict) or not result.get("modelUsage"):
        return {"usd_total": 0.0, "priced": False,
               "note": "no modelUsage in the result — the call never reached the model (see "
                       "auth_status), so there is nothing to price"}
    cost = memory_eval_mod.price_result(pricing, result)
    cost["priced"] = True
    return cost


# ------------------------------------------------------------------ the run ----
def do_dry_run(base: Path, out: Path) -> dict:
    home, repo = base / "home", base / "repo"
    vault = home / "vault"
    make_scratch_repo(repo)
    env = install_env(home)
    init_report = run_init(repo, vault, env)
    marker = repo / ".atlas-lane"
    lane = init_mod.marker_lane(marker) if marker.is_file() else None
    region = init_mod.region_name(repo.name)
    artifacts = assert_install_artifacts(home, repo, vault, region)
    state_dir = resolve_state_dir(env)
    session_log = state_dir / "session.log"
    report = {
        "schema": "gedaechtnis-fresh-home-eval/1", "mode": "dry-run",
        "home": str(home), "repo": str(repo), "vault": str(vault), "region": region, "lane": lane,
        "state_dir": str(state_dir), "session_log": str(session_log),
        "session_log_lines_before_any_call": (len(session_log.read_text(encoding="utf-8").splitlines())
                                              if session_log.is_file() else 0),
        "init_stdout": init_report.stdout, "artifacts": artifacts,
        "artifacts_all_present": all(artifacts.values()),
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "fresh-home-dry-run.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def do_live(base: Path, out: Path, claude_cmd: str, model: str, effort: str,
           max_turns: int, ceiling_usd: float, pricing_path: str | None) -> dict:
    dry = do_dry_run(base, out)
    if not dry["artifacts_all_present"]:
        raise Refusal("init.py did not create every expected artifact (see 'artifacts' in "
                      "fresh-home-dry-run.json) — refusing to spend a live call on a broken install.")
    home, repo = base / "home", base / "repo"
    env = install_env(home)
    session_log = Path(dry["session_log"])
    before = session_log.read_text(encoding="utf-8").splitlines() if session_log.is_file() else []

    memory_eval_mod = _load_memory_eval()
    pricing = memory_eval_mod.load_pricing(pricing_path)
    record = run_probe(claude_cmd, model, effort, repo, env, max_turns=max_turns)

    after = session_log.read_text(encoding="utf-8").splitlines() if session_log.is_file() else []
    new_lines = after[len(before):] if after[:len(before)] == before else after
    result = record.get("result") or {}
    sid = result.get("session_id") if isinstance(result, dict) else None
    row_for_session = [l for l in new_lines if sid and sid in l]

    auth_status = classify_auth(record)
    cost = price_probe(memory_eval_mod, pricing, record)

    report = dict(dry)
    report.update({
        "mode": "live", "claude": str(claude_cmd), "model": model, "effort": effort,
        "call": record, "auth_status": auth_status,
        "session_log_lines_before_call": len(before), "session_log_lines_after_call": len(after),
        "session_log_new_lines": new_lines, "session_log_gained_a_row": len(after) > len(before),
        "session_log_new_row_matches_session_id": bool(row_for_session),
        "cost": cost, "ceiling_usd": ceiling_usd,
        "over_ceiling": cost["usd_total"] > ceiling_usd,
    })
    out.mkdir(parents=True, exist_ok=True)
    (out / "fresh-home-live.json").write_text(json.dumps(report, indent=2, default=str) + "\n",
                                               encoding="utf-8")
    return report


# ------------------------------------------------------------------- main ----
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="q:CU-2026-09-10-FRESHHOME-1 — the stranger's "
                                             "install path, end to end, against a fresh HOME.")
    ap.add_argument("--out", default=None, help="output directory (default: ./results next to this script)")
    ap.add_argument("--base", default=None, help="the fresh HOME + scratch repo go here (default: a fresh mkdtemp)")
    ap.add_argument("--dry-run", action="store_true", help="everything except the model call")
    ap.add_argument("--live", action="store_true", help="also run the one pinned `claude -p` probe (spends money)")
    ap.add_argument("--claude", default=None, help="--live only: the claude binary (default: $(command -v claude))")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="--live only (default: %(default)s)")
    ap.add_argument("--effort", default=DEFAULT_EFFORT, help="--live only (default: %(default)s)")
    ap.add_argument("--max-turns", type=int, default=1, help="--live only (default: %(default)s)")
    ap.add_argument("--ceiling-usd", type=float, default=1.0,
                    help="--live only: printed/recorded as a POSTCONDITION check (one call is made "
                         "either way — the price is not known until after it runs); default $1")
    ap.add_argument("--pricing", default=None,
                    help="--live only: path to the module carrying PRICE/PRICE_ASOF "
                         "(default: $GEDAECHTNIS_PRICING_PY, else scripts/concilium.py)")
    a = ap.parse_args(argv)

    if a.dry_run and a.live:
        ap.error("--dry-run and --live are mutually exclusive.")
    if not a.dry_run and not a.live:
        ap.error("pass --dry-run (no model call) or --live (spends money).")

    base = Path(a.base).expanduser().resolve() if a.base else Path(tempfile.mkdtemp(prefix="gedaechtnis-freshhome-"))
    base.mkdir(parents=True, exist_ok=True)
    out = Path(a.out).expanduser().resolve() if a.out else Path(__file__).resolve().parent / "results"

    try:
        if a.dry_run:
            report = do_dry_run(base, out)
        else:
            claude_cmd = a.claude or shutil.which("claude")
            if not claude_cmd:
                raise Refusal("no claude binary found (pass --claude PATH, or put one on PATH) — "
                              "refusing to start a --live run with nothing to launch.")
            report = do_live(base, out, claude_cmd, a.model, a.effort, a.max_turns,
                             a.ceiling_usd, a.pricing)
    except Refusal as e:
        sys.stderr.write(f"fresh_home eval refuses: {e}\n")
        return 2

    print(json.dumps(report, indent=2, default=str))
    print(f"\n(fresh HOME kept for inspection under {base})")
    if report["mode"] == "dry-run":
        print("ARTIFACTS " + ("OK" if report["artifacts_all_present"] else "MISSING SOME — see above"))
    else:
        print(f"auth_status={report['auth_status']}  "
             f"session.log gained a row: {report['session_log_gained_a_row']}  "
             f"(matches this session id: {report['session_log_new_row_matches_session_id']})  "
             f"cost=${report['cost']['usd_total']:.4f}")
    return 0 if (report.get("artifacts_all_present", True) and
                report.get("session_log_gained_a_row", True)) else 1


if __name__ == "__main__":
    sys.exit(main())
