"""pricing.py — the price table a live eval run is costed with, and how one is loaded.

A live run that cannot price itself does not start. Before this module the only table was the
fleet's `scripts/concilium.py`, resolved as a SIBLING of the plugin directory — so a copy of this
plugin on its own, which is how it ships, could not price anything at all.

The package therefore carries `pricing.json` beside this file and uses it BY DEFAULT. The override
order is unchanged and still wins: `--pricing PATH`, then `$GEDAECHTNIS_PRICING_PY`, then the
packaged JSON. `load()` accepts either shape — a `.py` module exposing `PRICE`/`PRICE_ASOF`/
`price_usage`/`resolve_price_key`/`usage_from_model_usage` (the fleet's module, unchanged), or a
JSON table in the shape `pricing.json` documents.

**The copy carries its provenance and is guarded, not trusted.** `pricing.json`'s `provenance`
block names where the numbers came from and as of when; `--check <path to concilium.py>` compares
the two — the table, the aliases, the as-of date, and the USD a synthetic usage block computes to
under both implementations, which catches a formula drifting as well as a number. It exits 1 and
prints every difference. A comment asking a human to remember would not survive one price change.

    python3 eval/pricing.py --check ../scripts/concilium.py

stdlib only, Python 3.9+.
"""
from __future__ import annotations
import argparse, importlib.util, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGED = HERE / "pricing.json"
SCHEMA = "gedaechtnis-pricing/1"
# What any pricing source must expose, whichever shape it came in as.
PRICING_ATTRS = ("PRICE", "PRICE_ASOF", "price_usage", "resolve_price_key", "usage_from_model_usage")


class PricingError(Exception):
    """A price table that cannot be loaded or does not carry what a pricing call needs."""


class JsonPriceTable:
    """A `pricing.json` presented as the same five names the fleet's module exposes, so every
    caller is blind to which shape it got. The arithmetic below is the arithmetic in that module;
    `--check` is what keeps the two from drifting apart."""

    def __init__(self, doc: dict, source: Path):
        schema = doc.get("schema")
        if schema != SCHEMA:
            raise PricingError(f"{source}: schema {schema!r}, expected {SCHEMA!r}")
        self.source = source
        self.PRICE_ASOF = doc["price_asof"]
        self.PRICE = {name: (float(p["input"]), float(p["output"]),
                             float(p["cache_read_multiplier"]))
                      for name, p in doc["prices_usd_per_mtok"].items()}
        self.MODEL_ALIASES = dict(doc.get("model_aliases") or {})
        cw = doc.get("cache_write_multiplier") or {}
        self.CW_5M = float(cw.get("ephemeral_5m", 1.25))
        self.CW_1H = float(cw.get("ephemeral_1h", 2.0))
        self.provenance = doc.get("provenance") or {}
        if not self.PRICE:
            raise PricingError(f"{source}: the price table is empty")

    def price_usage(self, model, usage):
        """USD for one assistant message's usage block; None when the model is unpriced."""
        if model not in self.PRICE:
            return None
        pin, pout, rd = self.PRICE[model]
        i = usage.get("input_tokens", 0) or 0
        cw = usage.get("cache_creation_input_tokens", 0) or 0
        cr = usage.get("cache_read_input_tokens", 0) or 0
        o = usage.get("output_tokens", 0) or 0
        split = usage.get("cache_creation") or {}
        cw_1h = split.get("ephemeral_1h_input_tokens", 0) or 0
        cw_5m = split.get("ephemeral_5m_input_tokens", cw - cw_1h) or 0
        return {"input": i * pin / 1e6,
                "cache_write": (cw_5m * self.CW_5M + cw_1h * self.CW_1H) * pin / 1e6,
                "cache_read": cr * pin * rd / 1e6,
                "output": o * pout / 1e6}

    def resolve_price_key(self, name, canonical=None):
        """A KNOWN alias resolved to its priced key. An unknown model still refuses upstream."""
        for cand in (name, canonical, self.MODEL_ALIASES.get(name),
                     self.MODEL_ALIASES.get(canonical or "")):
            if cand and cand in self.PRICE:
                return cand
        return name

    def usage_from_model_usage(self, mu, frac_1h=0.0):
        """A `modelUsage` entry → the usage-block shape `price_usage` reads. The 5-minute/1-hour
        cache split is not in `modelUsage`; it comes from the session-level `usage.cache_creation`
        and is applied proportionally."""
        cw = mu.get("cacheCreationInputTokens", 0) or 0
        cw_1h = int(round(cw * frac_1h))
        return {"input_tokens": mu.get("inputTokens", 0) or 0,
                "cache_creation_input_tokens": cw,
                "cache_read_input_tokens": mu.get("cacheReadInputTokens", 0) or 0,
                "output_tokens": mu.get("outputTokens", 0) or 0,
                "cache_creation": {"ephemeral_1h_input_tokens": cw_1h,
                                   "ephemeral_5m_input_tokens": cw - cw_1h}}


def load_json_table(path: Path) -> JsonPriceTable:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise PricingError(f"{path} is not a readable JSON price table: {e}") from e
    try:
        return JsonPriceTable(doc, Path(path))
    except KeyError as e:
        raise PricingError(f"{path} is missing the required key {e}") from e


def load_py_module(path: Path):
    spec = importlib.util.spec_from_file_location("_gedaechtnis_pricing", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    missing = [a for a in PRICING_ATTRS if not hasattr(mod, a)]
    if missing:
        raise PricingError(f"{path} is not a usable pricing module: missing {', '.join(missing)}")
    return mod


def load(path):
    """A `.py` module or a `.json` table at `path`, as the same five-name interface."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise PricingError(f"no price table at {p}")
    return load_py_module(p) if p.suffix == ".py" else load_json_table(p)


# --------------------------------------------------------------------- the drift guard ----
# One usage block, exercised against every priced model under both implementations. A number that
# moved, an alias that was added on one side only, a formula that was "tidied" — each shows up as a
# printed difference and exit 1. Synthetic, deliberately asymmetric so no two components can cancel.
PROBE_MODEL_USAGE = {"inputTokens": 1_100_000, "outputTokens": 70_000,
                     "cacheCreationInputTokens": 300_000, "cacheReadInputTokens": 5_000_000}
PROBE_FRAC_1H = 0.4


def differences(packaged, fleet) -> list[str]:
    """Every way the packaged table and the fleet module disagree, in plain words."""
    out = []
    if packaged.PRICE_ASOF != fleet.PRICE_ASOF:
        out.append(f"price_asof: packaged {packaged.PRICE_ASOF!r}, module {fleet.PRICE_ASOF!r}")
    for name in sorted(set(packaged.PRICE) | set(fleet.PRICE)):
        a, b = packaged.PRICE.get(name), fleet.PRICE.get(name)
        if a is None:
            out.append(f"{name}: priced in the module ({b}), absent from pricing.json")
        elif b is None:
            out.append(f"{name}: priced in pricing.json ({a}), absent from the module")
        elif tuple(float(x) for x in a) != tuple(float(x) for x in b):
            out.append(f"{name}: pricing.json {a} vs module {b}")
    f_aliases = dict(getattr(fleet, "MODEL_ALIASES", {}) or {})
    for alias in sorted(set(packaged.MODEL_ALIASES) | set(f_aliases)):
        a, b = packaged.MODEL_ALIASES.get(alias), f_aliases.get(alias)
        if a != b:
            out.append(f"alias {alias!r}: pricing.json {a!r} vs module {b!r}")
    # and the arithmetic, not only the inputs to it
    for name in sorted(set(packaged.PRICE) & set(fleet.PRICE)):
        pu = packaged.price_usage(name, packaged.usage_from_model_usage(PROBE_MODEL_USAGE,
                                                                       PROBE_FRAC_1H))
        fu = fleet.price_usage(name, fleet.usage_from_model_usage(PROBE_MODEL_USAGE, PROBE_FRAC_1H))
        for k in ("input", "cache_write", "cache_read", "output"):
            if abs(pu[k] - fu[k]) > 1e-9:
                out.append(f"{name}: probe {k} prices to {pu[k]:.6f} here and {fu[k]:.6f} in the "
                           f"module — the FORMULA drifted, not just a number")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="the packaged price table, and its drift guard")
    ap.add_argument("--check", metavar="CONCILIUM_PY",
                    help="compare pricing.json against that module; exit 1 on any difference")
    ap.add_argument("--table", default=str(PACKAGED),
                    help="the packaged table to check (default: the one beside this file)")
    ap.add_argument("--show", action="store_true", help="print the packaged table and exit")
    a = ap.parse_args(argv)
    try:
        packaged = load_json_table(Path(a.table))
    except PricingError as e:
        print(f"pricing: {e}", file=sys.stderr)
        return 2
    if a.show:
        print(json.dumps({"price_asof": packaged.PRICE_ASOF, "price": packaged.PRICE,
                          "aliases": packaged.MODEL_ALIASES,
                          "provenance": packaged.provenance}, indent=2))
        return 0
    if not a.check:
        ap.error("nothing to do: pass --check PATH or --show")
    try:
        fleet = load_py_module(Path(a.check).expanduser())
    except (PricingError, OSError) as e:
        print(f"pricing --check: {e}", file=sys.stderr)
        return 2
    diffs = differences(packaged, fleet)
    if diffs:
        print(f"PRICE DRIFT — {a.table} and {a.check} disagree:", file=sys.stderr)
        for d in diffs:
            print(f"  - {d}", file=sys.stderr)
        print("Re-derive the packaged table from the module (and update `copied_on`), or fix the "
              "module. They are not allowed to differ.", file=sys.stderr)
        return 1
    print(f"pricing: {a.table} agrees with {a.check} "
          f"({len(packaged.PRICE)} models, as of {packaged.PRICE_ASOF})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
