"""_stub_client.py — the one place both eval harnesses call a `claude` command and parse its
reply. stdlib only; shared so `recall_bench` and `memory_eval` do not each grow a slightly
different parser for `stub_claude.py`'s `USAGE_JSON:` contract.

Not a public entry point: nothing outside `eval/` imports this, and it is not registered with the
plugin. It exists purely to keep the two run.py scripts from duplicating (and silently diverging
on) the same 20 lines.
"""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path

USAGE_PREFIX = "USAGE_JSON: "


def call_claude(claude_cmd: Path, model: str, prompt: str, answers_path: Path | None = None,
                *, effort: str | None = None, env: dict | None = None, timeout: int = 30) -> tuple[str, dict]:
    """Run `claude_cmd --model MODEL [--effort EFFORT] PROMPT`, return (answer_text, usage_dict).

    `answers_path`, when given, is passed via GEDAECHTNIS_STUB_ANSWERS in the child's environment
    — the stub reads it from there when `--answers` is not passed on the command line. A real
    (non-stub) `--claude` simply ignores an environment variable it has never heard of.

    Parsing contract: the LAST stdout line starting with `USAGE_JSON: ` is usage; everything
    before it, rejoined, is the answer. A `--claude` that never emits that line (a real `claude -p`
    run without `--output-format json`, say) yields the whole stdout as the answer and an EMPTY
    usage dict — never a crash — so a future live arm degrades to "tokens unmeasured", not a
    thrown exception baked into every harness step.
    """
    args = [sys.executable, str(claude_cmd)] if str(claude_cmd).endswith(".py") else [str(claude_cmd)]
    args += ["--model", model]
    if effort:
        args += ["--effort", effort]
    args += [prompt]
    child_env = dict(env or {})
    if answers_path:
        child_env["GEDAECHTNIS_STUB_ANSWERS"] = str(answers_path)
    p = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       timeout=timeout, env=child_env)
    if p.returncode != 0:
        raise RuntimeError(f"{claude_cmd} exited {p.returncode}: {p.stderr.strip()}")
    lines = p.stdout.splitlines()
    usage_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith(USAGE_PREFIX):
            usage_idx = i
            break
    if usage_idx is None:
        return p.stdout.strip(), {}
    usage_line = lines[usage_idx][len(USAGE_PREFIX):]
    try:
        usage = json.loads(usage_line)
    except json.JSONDecodeError:
        usage = {}
    answer = "\n".join(lines[:usage_idx]).strip()
    return answer, usage


def usage_tokens(usage: dict) -> int:
    """input + output tokens, 0 for a usage dict that carries neither (an unmeasured live call)."""
    return int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
