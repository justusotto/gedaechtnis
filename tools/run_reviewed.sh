#!/bin/bash
# run_reviewed.sh SCRIPT [ARGS ...] — run a Python script only when its exact bytes are in the
# reviewed ledger (PERMDESIGN-1). Otherwise it refuses, printing the sha and the ledger path.
# The whole rule is in reviewed.py; this wrapper exists so one narrow allow rule names it.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -I "$here/reviewed.py" run -- "$@"
