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
"""
from __future__ import annotations
import argparse, json, os, sys
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="A deterministic stand-in for `claude -p`, for eval-harness dry runs.")
    ap.add_argument("prompt", nargs="*", help="the prompt (or omit and pipe it on stdin)")
    ap.add_argument("-p", "--print", dest="print_mode", action="store_true",
                    help="accepted for shape-compatibility with real `claude -p`; this stub is always non-interactive")
    ap.add_argument("--model", default=None, help="required — a bare launch is never a routing decision")
    ap.add_argument("--effort", default=None, help="accepted, unused: the stub does not reason at any effort level")
    ap.add_argument("--answers", default=None,
                    help="path to the prompt-substring -> answer JSON file (default: $GEDAECHTNIS_STUB_ANSWERS)")
    a = ap.parse_args(argv)

    if not a.model:
        return refuse("--model is required (every real launch in this fleet pins one explicitly; "
                       "this stub enforces the same discipline so a harness cannot silently drift).")

    if a.prompt:
        prompt = " ".join(a.prompt)
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
    print(answer)
    print(USAGE_PREFIX + json.dumps(usage))
    return 0


if __name__ == "__main__":
    sys.exit(main())
