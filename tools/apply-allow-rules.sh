#!/bin/bash
# apply-allow-rules.sh [--dry-run | --apply] [--repo DIR ...] — merge the plugin's narrow allow and
# deny rules (rules/allow-rules.json) into every repository's Claude Code settings (PERMDESIGN-1).
# --dry-run is the default and writes nothing; --apply writes, with a dated backup beside each file.
# The whole rule is in allow_rules.py.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$here/allow_rules.py" "$@"
