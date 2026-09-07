#!/usr/bin/env python3
"""Validate every deal in deals/ against schema/deal.schema.json, score each
clause against schema/floor.json, and regenerate BENCHMARK.md and docs/.

Standard library only, so a contributor needs nothing but python3.

    python3 scripts/build.py          # validate + regenerate outputs
    python3 scripts/build.py --check  # validate + fail if outputs are stale (CI)
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schema" / "deal.schema.json").read_text())
FLOOR = json.loads((ROOT / "schema" / "floor.json").read_text())
DEALS_DIR = ROOT / "deals"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
URL_RE = re.compile(r"^https?://\S+$")


# ---------------------------------------------------------------- validation
# A small validator that covers the subset of JSON Schema this repo uses:
# type, enum, required, additionalProperties, pattern, format (date, uri),
# minItems, minLength, maxLength, items, $ref into $defs, oneOf.

def _resolve(node):
    if "$ref" in node:
        ref = node["$ref"]
        assert ref.startswith("#/$defs/"), ref
        return SCHEMA["$defs"][ref.split("/")[-1]]
    return node


def _type_ok(value, t):
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }[t]


def validate(value, node, path, errors):
    node = _resolve(node)
    if "oneOf" in node:
        matches = 0
        for alt in node["oneOf"]:
            sub = []
            validate(value, alt, path, sub)
            if not sub:
                matches += 1
        if matches != 1:
            errors.append(f"{path}: must match exactly one allowed shape (matched {matches})")
        return
    if "enum" in node and value not in node["enum"]:
        errors.append(f"{path}: {value!r} not one of {node['enum']}")
        return
    if "type" in node:
        types = node["type"] if isinstance(node["type"], list) else [node["type"]]
        if not any(_type_ok(value, t) for t in types):
            errors.append(f"{path}: expected {types}, got {type(value).__name__}")
            return
    if isinstance(value, str):
        if "pattern" in node and not re.match(node["pattern"], value):
            errors.append(f"{path}: {value!r} does not match {node['pattern']}")
        if node.get("format") == "date" and not DATE_RE.match(value):
            errors.append(f"{path}: {value!r} is not YYYY-MM-DD")
        if node.get("format") == "uri" and not URL_RE.match(value):
            errors.append(f"{path}: {value!r} is not a URL")
        if "minLength" in node and len(value) < node["minLength"]:
            errors.append(f"{path}: shorter than {node['minLength']}")
        if "maxLength" in node and len(value) > node["maxLength"]:
            errors.append(f"{path}: longer than {node['maxLength']}")
    if isinstance(value, list):
        if "minItems" in node and len(value) < node["minItems"]:
            errors.append(f"{path}: needs at least {node['minItems']} item(s)")
        if "items" in node:
            for i, item in enumerate(value):
                validate(item, node["items"], f"{path}[{i}]", errors)
    if isinstance(value, dict):
        props = node.get("properties", {})
        for key in node.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required field {key!r}")
        if node.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errors.append(f"{path}: unexpected field {key!r}")
        for key, sub in props.items():
            if key in value:
                validate(value[key], sub, f"{path}.{key}", errors)


def check_repo_rules(deal, errors):
    """Rules that are about honesty, not shape."""
    for name, clause in deal["terms"].items():
        if clause.get("present") is not None and not clause.get("sources"):
            errors.append(f"terms.{name}: present is {clause['present']} but sources is empty. Cite it or set present to null.")
    if deal["verification"] == "primary" and not any(d["kind"] == "primary" for d in deal["documents"]):
        errors.append("verification is 'primary' but no document has kind 'primary'")
    if deal["last_reviewed"] > date.today().isoformat():
        errors.append("last_reviewed is in the future")


def check_floor(deals):
    """The floor may point at a strongest example; make sure it exists."""
    ids = {d["id"] for d in deals}
    bad = []
    for name, spec in FLOOR["clauses"].items():
        s = spec.get("strongest")
        if s and s.get("deal") not in ids:
            bad.append(f"floor.json: {name}.strongest names unknown deal {s.get('deal')!r}")
    return bad


def load_deals():
    deals, failed = [], False
    for path in sorted(DEALS_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            deal = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            print(f"FAIL {path.name}: invalid JSON: {e}")
            failed = True
            continue
        errors = []
        validate(deal, SCHEMA, "$", errors)
        if not errors:
            check_repo_rules(deal, errors)
        if deal.get("id") and deal["id"] != path.stem:
            errors.append(f"id {deal['id']!r} must match filename {path.stem!r}")
        if errors:
            failed = True
            print(f"FAIL {path.name}")
            for e in errors:
                print(f"   {e}")
        else:
            deals.append(deal)
    return deals, failed


# ------------------------------------------------------------------- scoring
def _check(clause, field, op, arg):
    v = clause.get(field)
    if op == "eq":
        return v == arg
    if op == "in":
        return v in arg
    if op == "contains":
        return isinstance(v, list) and arg in v
    if op == "not_null":
        return v is not None
    raise ValueError(op)


def score(deal):
    """Return {clause: 'meets' | 'falls_short' | 'unknown' | 'n/a'} for one deal.

    A clause falls short only when a known value fails a check. If nothing
    fails but some checked field is still null, we do not know yet.
    """
    out = {}
    for name, spec in FLOOR["clauses"].items():
        clause = deal["terms"][name]
        if clause.get("not_applicable"):
            out[name] = "n/a"
            continue
        if clause.get("present") is False:
            out[name] = "falls_short"
            continue
        rules = [(r[0], r[1], r[2] if len(r) > 2 else None) for r in spec["rule"]]
        # null, an empty list, and the literal "unknown" all mean nobody knows yet
        unknown = lambda v: v is None or v == [] or v == "unknown"
        failed = any(not unknown(clause.get(f)) and not _check(clause, f, op, arg)
                     for f, op, arg in rules)
        missing = any(unknown(clause.get(f)) for f, _, _ in rules)
        out[name] = "falls_short" if failed else ("unknown" if missing else "meets")
    return out


def findings(deals):
    """Sentences computed from the deal files, so they cannot drift from the data."""
    n = len(deals)
    if not n:
        return []
    out = []

    def count(test):
        return sum(1 for d in deals if test(d))

    bond = count(lambda d: d["terms"]["decommissioning"].get("instrument")
                 in ("bond", "escrow", "letter_of_credit"))
    if bond == 0:
        out.append(("Nobody posts a teardown bond.",
                    f"Not one of the {n} agreements requires a bond, escrow, or letter of credit "
                    "to pay for demolition and site restoration if the operator walks away. "
                    "If a campus goes dark, the land is the community's problem."))

    seat = count(lambda d: d["terms"]["community_fund"].get("community_seat") is True)
    funded = count(lambda d: d["terms"]["community_fund"].get("present") is True)
    if seat == 0 and funded:
        out.append(("Community money, decided without the community.",
                    f"{funded} of the {n} deals set up money for the community. In none of them does a "
                    "resident or community organization hold a seat on the body that decides how it is spent."))

    oper = count(lambda d: "operation" in (d["terms"]["clawbacks"].get("triggers") or []))
    if oper == 0:
        out.append(("Clawbacks cover broken promises, not abandonment.",
                    "Where money can be taken back, the trigger is a missed job or investment target. "
                    f"In none of the {n} deals does the money come back simply because the facility stops running."))

    mb = [d for d in deals if d["terms"]["grid_costs"].get("minimum_bill") is True]
    if mb:
        from_state = [d for d in mb if d["terms"]["grid_costs"].get("governed_by")]
        if len(from_state) == len(mb):
            places = ", ".join(sorted(d["jurisdiction"]["locality"] for d in mb))
            out.append(("The one real protection came from regulators, not negotiators.",
                        f"{len(mb)} of the {n} deals carry a minimum electric bill that survives the tenant leaving "
                        f"({places}). Every one of them comes from a state utility commission, not from anything "
                        "the city or county negotiated."))

    nda = count(lambda d: d["terms"]["transparency"].get("nda") is True)
    if nda:
        out.append(("Secrecy is normal.",
                    f"{nda} of the {n} communities signed a nondisclosure agreement. Some barred officials from "
                    "saying that talks were happening at all, and one required the city to destroy its own notes."))

    hedged = [(d, k) for d in deals for k in FLOOR["clauses"]
              if d["terms"][k].get("hedge")]
    if hedged:
        out.append(("Present is not the same as binding.",
                    f"{len(hedged)} terms across these agreements exist but are softened by language like "
                    "good faith, commercially reasonable, or sole discretion. A term with an escape hatch "
                    "reads as a protection and functions as a preference."))

    return out


def strongest_examples(deals):
    """Pair each clause with the deal the floor names as the strongest real example."""
    by_id = {d["id"]: d for d in deals}
    out = []
    for name, spec in FLOOR["clauses"].items():
        s = spec.get("strongest")
        if not s or s["deal"] not in by_id:
            continue
        d = by_id[s["deal"]]
        clause = d["terms"][name]
        cites = [x for x in (clause.get("sources") or []) if isinstance(x, dict)]
        out.append({
            "clause": name,
            "label": spec["label"],
            "asks": spec["asks"],
            "why": s["why"],
            "deal_id": d["id"],
            "where": f"{d['jurisdiction']['locality']}, {d['jurisdiction']['state']}",
            "who": short_name(d),
            "meets": score(d)[name] == "meets",
            "cite": (cites[0].get("where") if cites else None),
            "quote": next((c.get("quote") for c in cites if c.get("quote")), None),
            "url": (cites[0]["url"] if cites else (d["documents"][0]["url"] if d["documents"] else None)),
        })
    return out


# ------------------------------------------------------------------ rendering
MARK = {"meets": "✅", "falls_short": "❌", "unknown": "❔", "n/a": "➖"}


def deal_label(deal):
    j = deal["jurisdiction"]
    who = short_name(deal)
    return f"{j['locality']}, {j['state']}" + (f" ({who})" if who else "")


def short_name(deal):
    """A short label for the row. Blank when it would just echo the place name."""
    who = deal.get("short_name") or deal.get("operator") or deal.get("developer")
    if not who:
        return ""
    who = who.split(" (")[0].split(",")[0].strip()
    place = deal["jurisdiction"]["locality"].lower()
    if who.lower() in place or place.replace("city of ", "").replace("village of ", "") in who.lower():
        return ""
    return who


def render_markdown(deals):
    clauses = FLOOR["clauses"]
    lines = [
        "# Benchmark",
        "",
        "Generated by `scripts/build.py`. Do not edit by hand; edit the deal files and rebuild.",
        "",
        "✅ meets the floor · ❌ falls short · ❔ not enough information yet · ➖ not applicable (for example, no incentives were given, so there is nothing to claw back)",
        "",
        "| Deal | " + " | ".join(c["label"] for c in clauses.values()) + " | Verified |",
        "|---|" + "---|" * (len(clauses) + 1),
    ]
    for deal in deals:
        s = score(deal)
        cells = [MARK[s[name]] for name in clauses]
        lines.append(
            f"| [{deal_label(deal)}](deals/{deal['id']}.json) | "
            + " | ".join(cells)
            + f" | {deal['verification']} |"
        )
    f = findings(deals)
    if f:
        lines += ["", "## What the deals show", ""]
        for title, body in f:
            lines += [f"**{title}** {body}", ""]
    se = strongest_examples(deals)
    if se:
        lines += ["", "## The strongest terms anyone has actually signed", "",
                  FLOOR.get("strongest_note", ""), ""]
        for e in se:
            lines.append(f"**{e['label']}** ({e['where']}{', ' + e['who'] if e['who'] else ''}). {e['why']}"
                         + (f" [{e['cite']}]({e['url']})" if e.get("cite") and e.get("url") else ""))
            lines.append("")
    lines += ["", "## What the floor asks for", ""]
    for name, c in clauses.items():
        lines.append(f"- **{c['label']}**: {c['asks']}")
    lines += [
        "",
        "The floor lives in [`schema/floor.json`](schema/floor.json). If you think it is set wrong, open a pull request that changes it and say why.",
        "",
    ]
    return "\n".join(lines)


def render_html(deals):
    template = (ROOT / "docs" / "template.html").read_text()
    payload = json.dumps({
        "generated": date.today().isoformat(),
        "floor": FLOOR["clauses"],
        "findings": [{"title": t, "body": x} for t, x in findings(deals)],
        "strongest": strongest_examples(deals),
        "strongest_note": FLOOR.get("strongest_note", ""),
        "deals": [dict(deal, score=score(deal), _label=short_name(deal)) for deal in deals],
    }, ensure_ascii=False)
    # </script> inside JSON would end the tag early.
    payload = payload.replace("</", "<\\/")
    return template.replace("/*DATA*/", payload)


def main():
    check = "--check" in sys.argv
    deals, failed = load_deals()
    if failed:
        sys.exit(1)
    for problem in check_floor(deals):
        print(problem)
        failed = True
    if failed:
        sys.exit(1)
    outputs = {
        ROOT / "BENCHMARK.md": render_markdown(deals),
        ROOT / "docs" / "index.html": render_html(deals),
    }
    stale = []
    for path, content in outputs.items():
        if check:
            if not path.exists() or path.read_text() != content:
                stale.append(path.relative_to(ROOT))
        else:
            path.write_text(content)
    if check and stale:
        print("Outputs are stale, run `python3 scripts/build.py` and commit:")
        for p in stale:
            print(f"   {p}")
        sys.exit(1)
    print(f"OK: {len(deals)} deal(s) valid" + ("" if check else ", outputs regenerated"))


if __name__ == "__main__":
    main()
