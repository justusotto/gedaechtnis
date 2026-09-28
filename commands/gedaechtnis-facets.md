---
description: List a region's facets — the rule files beside its Boot file — and what loads each one.
allowed-tools: Bash(python3:*)
---

Run this, and nothing else, with the region the user named (for example `Studio/Cards`):

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/tools/facets.py" "<Region>"
```

If the user named no region, use the one this session's boot facts block lists under "Facet"; if
there is none, say that this session's region has no facets and stop. If `${CLAUDE_PLUGIN_ROOT}`
is unset, the script sits at `tools/facets.py` inside the gedaechtnis plugin directory.

Show the output unchanged. Each line is one facet: its size and the tools, Bash patterns, paths and
row ids that load it. A facet is loaded once per session, the first time one of those matches, and
every load is one row in `facets.log` — so whether it loaded is a log read, not a guess.
A line marked `any region` is a `scope: always` facet: it loads from any session whose call
matches, not only from sessions working in its own folder (list the shared folder with `Global`).
