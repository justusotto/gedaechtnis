#!/bin/bash
# live_two_writers.sh — the LIVE half of DESIGN §5.4: two real `claude -p` sessions appending to
# one region's Errata at the same time, five rounds, in a throwaway vault.
#
#   *** THIS SCRIPT IS NOT RUN BY ANY TEST, AND WAS NOT RUN WHEN IT WAS WRITTEN (2026-09-09). ***
#
# It is out of scope for WP4, which builds and proves the doors with the no-model simulator
# (`gedaechtnis/tests/sim_two_writers.py`, run in CI). This file exists so the live proof is a
# script somebody executes rather than a paragraph somebody re-derives — but it SPENDS MONEY on
# two model sessions per round, so it stays a deliberate act. Run it by hand, read the report it
# writes, and record the numbers where the arc can find them.
#
# Why a live proof at all, when the simulator is green: the simulator drives the hooks with hook
# JSON this repo composes. It cannot show that Claude Code itself sends PostToolUse after a denied
# PreToolUse, that a model reads a D2 "retry" deny as an instruction to retry rather than to give
# up or route around it with a shell redirect, or that the two hook events arrive in the order the
# mutex assumes. Only two real sessions show that. A green harness is a claim about the harness
# until the thing it models has been run once (Global/Patterns-verification).
#
# Safety: a throwaway HOME and a throwaway vault, both under a mktemp -d root. It never reads or
# writes ~/Atlas, ~/.claude, or any lane repo. Model and effort are PINNED (the platform default
# effort is `high`, and a settings-file default is never a routing decision — Global/Errata).
#
# Usage:  bash gedaechtnis/eval/live_two_writers.sh [rounds]     # default 5
# bash 3.2 compatible: no associative arrays, no ${x^^}, no GNU-only flags.

set -uo pipefail

ROUNDS="${1:-5}"
PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(mktemp -d /tmp/gedaechtnis-live-XXXXXX)"
HOME_DIR="$ROOT/home"
VAULT="$ROOT/Vault"
STATE="$ROOT/state"
REPO="$ROOT/repo"
REGION="Notes"
TARGET="$VAULT/$REGION/Errata.md"
REPORT="$ROOT/report.txt"

echo "live_two_writers: root=$ROOT rounds=$ROUNDS"

mkdir -p "$HOME_DIR" "$VAULT/$REGION" "$VAULT/Global" "$STATE" "$REPO"
git init -q "$VAULT"
git -C "$VAULT" config user.email "live@local"
git -C "$VAULT" config user.name "live"
printf '```fleet-roster\nlane: LIVE\nrepo: repo\n```\n' > "$VAULT/Global/fleet-roster.md"
printf '# Errata\n\n- seed entry\n' > "$TARGET"
git -C "$VAULT" add -A
git -C "$VAULT" commit -q -m "seed"
printf 'lane: LIVE\npath: %s/\npath: Global/\n' "$REGION" > "$REPO/.atlas-lane"

# The plugin, wired into the throwaway HOME exactly as a real install wires it.
mkdir -p "$HOME_DIR/.claude"
cp "$PLUGIN_ROOT/hooks/hooks.json" "$HOME_DIR/.claude/gedaechtnis-hooks.json"
cat > "$HOME_DIR/.claude/settings.json" <<EOF
{ "hooks": {} }
EOF
echo "NOTE: wire hooks.json into \$HOME_DIR/.claude/settings.json for the install shape you are testing"
echo "      (CLAUDE_PLUGIN_ROOT must resolve to $PLUGIN_ROOT)."

run_session () {   # $1 = worker id, $2 = round
  HOME="$HOME_DIR" \
  CLAUDE_PLUGIN_ROOT="$PLUGIN_ROOT" \
  GEDAECHTNIS_VAULT="$VAULT" \
  GEDAECHTNIS_STATE_DIR="$STATE" \
  GEDAECHTNIS_FLEET_ROSTER="$VAULT/Global/fleet-roster.md" \
  claude -p \
    --model sonnet --effort low \
    --setting-sources project \
    "Append exactly three new entries to $TARGET, each on its own line, each beginning
'- w$1 r$2 ' followed by a short sentence. Use the Edit tool, never Write. If an edit is
refused because another session is editing the file, retry the same edit. Then stop." \
    < /dev/null > "$ROOT/w$1-r$2.log" 2>&1
}

: > "$REPORT"
r=1
while [ "$r" -le "$ROUNDS" ]; do
  echo "round $r"
  run_session 0 "$r" &
  P0=$!
  run_session 1 "$r" &
  P1=$!
  wait $P0; wait $P1
  r=$((r + 1))
done

# ---- assertions, per DESIGN §5.4: 30 entries, no duplicates, path-limited commits, no hook errors
TOTAL=$(grep -c '^- w[01] r' "$TARGET")
UNIQ=$(grep '^- w[01] r' "$TARGET" | sort | uniq | wc -l | tr -d ' ')
EXPECT=$((ROUNDS * 2 * 3))
COMMITS=$(git -C "$VAULT" log --oneline | wc -l | tr -d ' ')
BROAD=$(git -C "$VAULT" log --format=%s | grep -c 'add -A' || true)
ERRS=0
[ -f "$STATE/hook-errors.log" ] && ERRS=$(wc -l < "$STATE/hook-errors.log" | tr -d ' ')

{
  echo "entries:        $TOTAL (expected $EXPECT)"
  echo "unique:         $UNIQ"
  echo "vault commits:  $COMMITS"
  echo "hook errors:    $ERRS   (must be 0)"
  echo "root:           $ROOT"
} | tee -a "$REPORT"

RC=0
[ "$TOTAL" -eq "$EXPECT" ] || { echo "FAIL: expected $EXPECT entries, found $TOTAL"; RC=1; }
[ "$UNIQ" -eq "$TOTAL" ]   || { echo "FAIL: duplicate entries"; RC=1; }
[ "$ERRS" -eq 0 ]          || { echo "FAIL: $ERRS hook-error line(s) — see $STATE/hook-errors.log"; RC=1; }
[ "$BROAD" -eq 0 ]         || { echo "FAIL: a commit subject mentions a broad add"; RC=1; }
[ "$RC" -eq 0 ] && echo "PASS — $TOTAL entries, none lost, none duplicated"
exit "$RC"
