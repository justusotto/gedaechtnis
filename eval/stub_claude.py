#!/usr/bin/env python3
"""stub_claude.py — a deterministic stand-in for `claude -p`, for harness dry runs. stdlib only.

    python3 stub_claude.py --model MODEL "the prompt"
    echo "the prompt" | python3 stub_claude.py --model MODEL

Every eval harness in this folder drives its steps through a `claude` COMMAND rather than a
hard-coded call, so the same harness runs against this stub today and against a real `claude -p`
later with no code change — only `--claude` changes. This file is that stub's whole job: read a
prompt, refuse if it is not told what model it is (pinning `--model` is a fleet-wide rule, not a
convenience — see Global/Patterns.md "A settings-file model default is silent routing authority"),
and return a CANNED, deterministic answer so a harness can be proven end-to-end with **no model
call and no network** — the eval skeletons this ships with are explicitly deferred until a real
arm is deliberately wired in (DESIGN.md §8, R7).

**How answers are chosen.** `GEDAECHTNIS_STUB_ANSWERS` (or `--answers`) names a JSON file mapping a
PROMPT SUBSTRING to the answer text to return. The FIRST key (in the file's own order — JSON object
order is preserved) that is a substring of the received prompt wins; a harness that wants a
specific fact to win only when it is genuinely present in the prompt puts that fact's key before
any looser fallback key. No match -> a fixed, clearly-marked default. This is deliberately dumber
than a real model: it cannot reason, paraphrase or infer — which is exactly what makes it useless
for judging quality and exactly right for proving a harness's PLUMBING (does the right context
reach the prompt; is the answer scored correctly; are tokens counted) independent of any model.

**Usage accounting.** Real usage numbers do not exist here, so the last line of stdout is always
`USAGE_JSON: {...}` — an approximate token count (prompt/answer length over 4, the same rough
heuristic used across this fleet's cost dashboards) plus the model name, so a harness's token
accounting code has something real to parse and sum. A caller splits stdout on the LAST line
starting with that prefix; everything before it is the answer.

**`--output-format json` (added for the WP8 live path).** With it, stdout is ONE JSON object in the
shape a real `claude -p --output-format json` returns — `result`, `usage`, `modelUsage`,
`num_turns`, `session_id`, `total_cost_usd` — and NOTHING else, so a harness that prices a live run
from `modelUsage` can be proven end-to-end without a model call. Default (text) behaviour is
untouched: the answer, then the `USAGE_JSON:` line.

The stub also accepts, and records, the launch flags a real live call carries (`--effort`,
`--add-dir`, `--allowedTools`, `--tools`, `--setting-sources`, `--permission-prompts`,
`--max-turns`, `--max-budget-usd`). They change nothing here; they exist so the stub can stand in
for the real binary on the exact argv the harness builds, and so a test can read back what
actually reached the child under `stub.model` / `stub.effort` in the JSON output. `stub.stdin_extra`
carries whatever was left on stdin after the prompt was taken from argv — the honest way to prove a
caller passed `stdin=DEVNULL` rather than leaking its own stdin into the prompt.
"""
from __future__ import annotations
import argparse, hashlib, json, os, sys
from pathlib import Path


DEFAULT_ANSWER = "(stub: no configured answer for this prompt)"
USAGE_PREFIX = "USAGE_JSON: "


def refuse(sentence: str) -> int:
    print(f"stub_claude refuses: {sentence}", file=sys.stderr)
    return 2


def load_answers(path) -> dict:
    """A missing or malformed answers file is not an error: it just means every prompt gets the
    default answer, which is itself a useful, honest state for a harness step nobody has wired an
    answer for yet."""
    if not path:
        return {}
    try:
        data = json.loads(Path(os.path.expanduser(str(path))).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def pick_answer(prompt: str, answers: dict) -> str:
    for key, answer in answers.items():
        if key and key in prompt:
            return str(answer)
    return DEFAULT_ANSWER


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def result_object(a, prompt: str, answer: str, usage: dict, stdin_extra: str) -> dict:
    """The `--output-format json` shape. Field names follow the real CLI's result object (`result`,
    `usage`, `modelUsage`, `num_turns`, `session_id`, `total_cost_usd`) because a harness prices a
    live run from `modelUsage` — a stub whose block is shaped differently would prove nothing about
    the code that reads a real one. `total_cost_usd` is 0.0 and says so: this stub never spends, and
    a harness that trusts a stub's cost figure instead of pricing the tokens itself is the bug.

    The `stub` block is the stub's own honesty channel: what model/effort actually reached the
    child process, and what was left on stdin."""
    in_tok, out_tok = usage["input_tokens"], usage["output_tokens"]
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": answer,
        "session_id": "stub-" + hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:16],
        "num_turns": 1,
        "total_cost_usd": 0.0,
        "permission_denials": [],
        "usage": {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
        },
        "modelUsage": {
            a.model: {
                "inputTokens": in_tok,
                "outputTokens": out_tok,
                "cacheCreationInputTokens": 0,
                "cacheReadInputTokens": 0,
                "canonicalModel": a.model,
            }
        },
        "stub": {
            "model": a.model,
            "effort": a.effort,
            "setting_sources": a.setting_sources,
            "permission_prompts": a.permission_prompts,
            "max_turns": a.max_turns,
            "max_budget_usd": a.max_budget_usd,
            "add_dir": list(a.add_dir),
            "allowed_tools": list(a.allowed_tools),
            "tools": list(a.tools),
            "stdin_extra": stdin_extra,
            "note": "approximate — this is a stub, not a real usage report",
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="A deterministic stand-in for `claude -p`, for eval-harness dry runs.")
    ap.add_argument("prompt", nargs="*", help="the prompt (or omit and pipe it on stdin)")
    ap.add_argument("-p", "--print", dest="print_mode", action="store_true",
                    help="accepted for shape-compatibility with real `claude -p`; this stub is always non-interactive")
    ap.add_argument("--model", default=None, help="required — a bare launch is never a routing decision")
    ap.add_argument("--effort", default=None, help="accepted, unused: the stub does not reason at any effort level")
    ap.add_argument("--answers", default=None,
                    help="path to the prompt-substring -> answer JSON file (default: $GEDAECHTNIS_STUB_ANSWERS)")
    ap.add_argument("--output-format", dest="output_format", default="text", choices=("text", "json"),
                    help="`json` returns ONE result object in the shape real `claude -p` returns")
    # Accepted for argv-shape compatibility with a real live call; recorded, never acted on. The
    # variadic ones are declared variadic on purpose: a real `claude` swallows a trailing positional
    # prompt with them, which is exactly why the harness puts the prompt after the boolean `-p`.
    ap.add_argument("--add-dir", dest="add_dir", nargs="*", default=[])
    ap.add_argument("--allowedTools", dest="allowed_tools", nargs="*", default=[])
    ap.add_argument("--tools", nargs="*", default=[])
    ap.add_argument("--setting-sources", dest="setting_sources", default=None)
    ap.add_argument("--permission-prompts", dest="permission_prompts", default=None)
    ap.add_argument("--max-turns", dest="max_turns", default=None)
    ap.add_argument("--max-budget-usd", dest="max_budget_usd", default=None)
    a = ap.parse_args(argv)

    if not a.model:
        return refuse("--model is required (every real launch in this fleet pins one explicitly; "
                       "this stub enforces the same discipline so a harness cannot silently drift).")

    stdin_extra = ""
    if a.prompt:
        prompt = " ".join(a.prompt)
        # Whatever is left on stdin was NOT used for the prompt. Reading it is how a caller's
        # `stdin=DEVNULL` becomes checkable: DEVNULL reads as "", an inherited pipe reads as its
        # content. A terminal is never read — that would block.
        if a.output_format == "json" and not sys.stdin.isatty():
            try:
                stdin_extra = sys.stdin.read()
            except OSError:
                stdin_extra = ""
    else:
        prompt = sys.stdin.read() if not sys.stdin.isatty() else ""
    if not prompt.strip():
        return refuse("no prompt given (pass it as an argument, or pipe it on stdin).")

    answers = load_answers(a.answers or os.environ.get("GEDAECHTNIS_STUB_ANSWERS"))
    answer = pick_answer(prompt, answers)

    usage = {
        "model": a.model,
        "input_tokens": approx_tokens(prompt),
        "output_tokens": approx_tokens(answer),
        "note": "approximate — this is a stub, not a real usage report",
    }
    if a.output_format == "json":
        print(json.dumps(result_object(a, prompt, answer, usage, stdin_extra), indent=2))
        return 0
    print(answer)
    print(USAGE_PREFIX + json.dumps(usage))
    return 0


if __name__ == "__main__":
    sys.exit(main())
