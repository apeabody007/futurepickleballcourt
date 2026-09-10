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
CHECKLIST_PATH = ROOT / "checklist.json"
CHECKLIST = json.loads(CHECKLIST_PATH.read_text()) if CHECKLIST_PATH.exists() else None
WANTED_PATH = ROOT / "wanted.json"
WANTED = json.loads(WANTED_PATH.read_text()) if WANTED_PATH.exists() else {"wanted": []}
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


def checklist_rows(deals):
    """Pair each of Cuban's asks with what the signed agreements actually did."""
    if not CHECKLIST:
        return []
    scored = [score(d) for d in deals]
    by_id = {d["id"]: d for d in deals}
    rows = []
    for a in CHECKLIST["asks"]:
        k = a["clause"]
        spec = FLOOR["clauses"].get(k)
        if not spec:
            continue
        verdicts = [s[k] for s in scored]
        applicable = [v for v in verdicts if v != "n/a"]
        met = verdicts.count("meets")
        best = spec.get("strongest") or {}
        d = by_id.get(best.get("deal"))
        rows.append({
            "ask": a["ask"],
            "clause": k,
            "label": spec["label"],
            "met": met,
            "of": len(applicable),
            "unknown": verdicts.count("unknown"),
            "best_where": (f"{d['jurisdiction']['locality']}, {d['jurisdiction']['state']}" if d else None),
            "best_why": best.get("why"),
            "best_id": (d["id"] if d else None),
            "best_meets": (score(d)[k] == "meets") if d else False,
        })
    return rows


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


def render_prompt(deals):
    """A ready-to-paste review prompt. Cuban told people to ask an LLM the questions;
    this ships the questions."""
    lines = [
        "You are reviewing a proposed data center agreement on behalf of the community that would host it.",
        "You are not the developer's lawyer. Be concrete and quote the document.",
        "",
        "Attached or pasted below is the agreement under negotiation.",
        "Benchmark data on what other communities actually signed is at https://futurepickleballcourt.com/all.md",
        "",
        "Answer these, in order:",
        "",
        "1. For each of the terms below, quote the exact language in this agreement, or write NOT PRESENT.",
    ]
    for i, (name, spec) in enumerate(FLOOR["clauses"].items(), 1):
        lines.append(f"   {i}. {spec['label']}. A strong version: {spec['asks']}.")
    lines += [
        "",
        "2. For every term that is present, quote any language that softens it: good faith, commercially",
        "   reasonable, best efforts, sole discretion, subject to availability, as determined by the company,",
        "   or similar. For each one, say plainly whether it is an obligation or a preference. A term that",
        "   sounds protective and cannot be enforced is worse than no term, because it ends the conversation.",
        "",
        "3. Which terms here are weaker than what other communities have already signed? Name the comparison.",
        "",
        "4. Walk through what happens if this facility is built and then stops operating in year six.",
        "   Who pays to tear it down. Who pays for the grid capacity built to serve it. What happens to the",
        "   tax revenue the budget now depends on. Point to the clause that answers each, or say there is none.",
        "",
        "5. Who can enforce each promise, and how. If only the local government can sue, say so. If residents",
        "   are excluded as third party beneficiaries, say so.",
        "",
        "6. List the three changes that would most improve this deal for the community, in priority order,",
        "   with specific language to propose for each.",
        "",
        "Do not soften your answer to be agreeable. If the deal is good, say that too.",
        "",
        "",
        "OPTIONAL, IF YOU WANT THIS DEAL ADDED TO THE PUBLIC BENCHMARK",
        "",
        "Print the block below, filled in. Leave a line blank if the document does not answer it.",
        "Do not guess. A blank is useful; a guess is not.",
        "Then send it to deals@futurepickleballcourt.com with a link to the document.",
        "",
        "  PLACE:            (city or county, state)",
        "  OPERATOR:         (who will run it, if named)",
        "  DEVELOPER:        (who signed, if different)",
        "  AGREEMENT TYPE:   (development agreement, proffer, tax abatement, land sale, other)",
        "  DATE SIGNED:      (YYYY-MM-DD)",
        "  DOCUMENT URL:     (a public link anyone can open)",
        "  STATUS:           (signed, approved, proposed, withdrawn, terminated)",
    ]
    for name, spec in FLOOR["clauses"].items():
        lines.append(f"  {spec['label'].upper()}:".ljust(22)
                     + "(what the document says, and the section number)")
    lines += [
        "  SOFTENING LANGUAGE: (any good faith, reasonable efforts, or sole discretion wording, quoted)",
        "",
        "Accuracy matters more than completeness. Every line will be checked against the document",
        "before it is published, and anything that cannot be found there will be removed.",
    ]
    return "\n".join(lines) + "\n"


def render_all_markdown(deals):
    """Every deal in one file, shaped for pasting into a chat window."""
    L = [f"# Data center agreements: what communities actually signed",
         "",
         f"Generated {date.today().isoformat()} from https://futurepickleballcourt.com",
         f"{len(deals)} agreements, scored against {len(FLOOR['clauses'])} terms. "
         "Every term below carries the document and section it came from.",
         "",
         "Verification levels: primary means a person read the executed document; press means every term "
         "traces to reporting; unverified means it came from a secondary summary and is waiting on a reader.",
         ""]
    for title, body in findings(deals):
        L += [f"**{title}** {body}", ""]
    L += ["---", ""]
    for d in deals:
        j = d["jurisdiction"]
        s = score(d)
        L += [f"## {j['locality']}, {j['state']}" + (f" ({short_name(d)})" if short_name(d) else ""), ""]
        L.append(f"- Project: {d['project']}")
        if d.get("operator"):
            L.append(f"- Operator: {d['operator']}")
        if d.get("developer"):
            L.append(f"- Developer: {d['developer']}")
        L.append(f"- Agreement: {d['agreement_type'].replace('_', ' ')}, {d['status']}"
                 + (f", signed {d['dates']['signed']}" if d["dates"].get("signed")
                    else f", approved {d['dates']['approved']}" if d["dates"].get("approved") else ""))
        sc = d.get("scale") or {}
        bits = [f"{sc[k]:,} {u}" if isinstance(sc.get(k), (int, float)) else None
                for k, u in (("mw", "MW"), ("acres", "acres"), ("sqft", "sq ft"), ("capex_usd", "USD"))]
        bits = [x for x in bits if x]
        if bits:
            L.append(f"- Scale: {', '.join(bits)}")
        if sc.get("notes"):
            L.append(f"  - {sc['notes']}")
        L.append(f"- Verification: {d['verification']}")
        L.append("- Documents:")
        for x in d["documents"]:
            L.append(f"  - [{x['kind']}] {x['title']}: {x['url']}")
        L.append("")
        for name, spec in FLOOR["clauses"].items():
            c = d["terms"][name]
            verdict = {"meets": "MEETS", "falls_short": "FALLS SHORT",
                       "unknown": "UNKNOWN", "n/a": "NOT APPLICABLE"}[s[name]]
            L.append(f"### {spec['label']}: {verdict}")
            L.append(f"Floor: {spec['asks']}")
            if c.get("hedge"):
                L.append(f"SOFTENING LANGUAGE: \"{c['hedge']}\"")
            if c.get("notes"):
                L.append(c["notes"])
            for x in (c.get("sources") or []):
                if isinstance(x, dict):
                    q = f' "{x["quote"]}"' if x.get("quote") else ""
                    L.append(f"- {x.get('where') or 'source'}:{q} {x['url']}")
                else:
                    L.append(f"- {x}")
            L.append("")
        if d.get("criticisms"):
            L.append("### Reported criticisms")
            for x in d["criticisms"]:
                L.append(f"- {x['summary']} ({x['source']})")
            L.append("")
        L += ["---", ""]
    return "\n".join(L)



DEAL_CSS = """
:root { --paper:#efe9dc; --paper-2:#e6dfd0; --ink:#17140f; --ink-2:#4a443b; --ink-3:#8a8274;
  --rule:#17140f; --hair:rgba(23,20,15,.28); --stamp:#c4281e; --met:#1e6b3d;
  --serif:"Instrument Serif","Iowan Old Style",Georgia,serif; --mono:"Courier Prime","Courier New",monospace; }
*{box-sizing:border-box} html{background:var(--paper)}
body{margin:0;color:var(--ink);background:var(--paper);font-family:var(--serif);font-size:19px;line-height:1.45;-webkit-font-smoothing:antialiased}
body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;opacity:.5;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 .06 0'/></filter><rect width='160' height='160' filter='url(%23n)'/></svg>")}
a{color:inherit;text-decoration-color:var(--hair);text-underline-offset:3px} a:hover{text-decoration-color:var(--ink)}
.page{position:relative;z-index:1;max-width:920px;margin:0 auto;padding:0 28px 80px}
.mast{display:flex;justify-content:space-between;align-items:baseline;gap:16px;padding:18px 0 10px;border-bottom:2px solid var(--rule);
  font-family:var(--mono);font-size:12.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-2)}
.mast b{color:var(--ink)} .mast a{color:var(--ink-2)}
h1{font-weight:400;font-size:clamp(38px,6vw,64px);line-height:1;letter-spacing:-.015em;margin:34px 0 10px}
.who{font-family:var(--mono);font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-2);margin:0 0 18px}
.facts{font-family:var(--mono);font-size:13.5px;line-height:1.7;color:var(--ink-2);margin:0 0 6px;padding-bottom:16px;border-bottom:1px solid var(--hair)}
.facts b{color:var(--ink);font-weight:400} .facts a{color:var(--ink)}
h2{font-family:var(--mono);font-weight:700;font-size:12.5px;letter-spacing:.14em;text-transform:uppercase;margin:38px 0 12px;padding-top:10px;border-top:2px solid var(--rule)}
.strip{display:flex;flex-wrap:wrap;gap:6px;margin:18px 0 4px}
.st{display:inline-block;font-family:var(--mono);font-weight:700;font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;line-height:1;
  padding:5px 6px 4px;border:1.5px solid var(--ink);color:var(--ink);min-width:52px;text-align:center}
.st.meets{background:var(--met);border-color:var(--met);color:var(--paper)}
.st.falls_short{border-color:var(--stamp);color:var(--stamp)}
.st.unknown{border-style:dashed;border-color:var(--ink-3);color:var(--ink-3);font-weight:400}
.st.na{border:0;color:var(--ink-3);font-weight:400}
.sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
.chip{display:flex;flex-direction:column;align-items:center;gap:4px;min-width:78px}
.chip span.n{font-family:var(--mono);font-size:9.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);text-align:center;line-height:1.2}
.clause{padding:16px 0 18px;border-bottom:1px solid var(--hair)}
.clause h3{display:flex;align-items:center;gap:12px;margin:0 0 6px;font-family:var(--mono);font-weight:700;font-size:13px;letter-spacing:.1em;text-transform:uppercase}
.clause .asks{font-family:var(--mono);font-size:12px;color:var(--ink-3);margin:0 0 8px;line-height:1.5}
.clause p{margin:0 0 7px;font-size:17.5px;line-height:1.45}
.clause .data{font-family:var(--mono);font-size:13px;color:var(--ink-2);line-height:1.6}
.hedge{margin:8px 0 0;padding:8px 11px;border-left:3px solid var(--stamp);background:color-mix(in srgb,var(--stamp) 6%,transparent);font-size:16px}
.hedge b{font-family:var(--mono);font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--stamp);display:block;margin-bottom:2px}
ol.src{margin:8px 0 0;padding-left:22px;font-family:var(--mono);font-size:12.5px;line-height:1.65;color:var(--ink-2)}
ol.src a{color:var(--ink);word-break:break-word} ol.src .q{font-style:italic}
.crit p{margin:0 0 8px;font-size:17px}
.crit a{font-family:var(--mono);font-size:12px}
.other{font-family:var(--mono);font-size:12.5px;line-height:1.9}
.other a{color:var(--ink-2)} .other a:hover{color:var(--ink)}
footer{margin-top:44px;padding-top:14px;border-top:2px solid var(--rule);font-family:var(--mono);font-size:12.5px;line-height:1.7;color:var(--ink-2)}
footer a{color:var(--ink)}
@media (max-width:700px){body{font-size:17px}.page{padding:0 18px 60px}.chip{min-width:64px}}
"""

VERDICT_WORD = {"meets": "Met", "falls_short": "Short", "unknown": "?", "n/a": "n/a"}
VERDICT_SAID = {"meets": "meets the bar", "falls_short": "falls short",
                "unknown": "not known", "n/a": "does not apply"}


def esc(x):
    return (str("" if x is None else x).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _cls(v):
    return "na" if v == "n/a" else v


def _facts_for(key, c):
    """Same per-clause detail the main page shows, rendered server side."""
    def usd(n):
        return None if n is None else "$" + format(n, ",")
    def yn(v):
        return None if v is None else ("yes" if v else "no")
    pairs = {
        "community_fund": [("amount", usd(c.get("amount_usd"))), ("cadence", c.get("cadence")),
                           ("scales with project", yn(c.get("scales_with_project"))),
                           ("community seat", yn(c.get("community_seat")))],
        "clawbacks": [("triggers", ", ".join(c.get("triggers") or []) or None),
                      ("proportional", yn(c.get("proportional")))],
        "decommissioning": [("instrument", c.get("instrument")), ("amount", usd(c.get("amount_usd")))],
        "grid_costs": [("interconnect paid by", c.get("who_pays_interconnect")),
                       ("minimum bill", yn(c.get("minimum_bill"))),
                       ("term", f"{c['term_years']} yrs" if c.get("term_years") else None),
                       ("governed by", c.get("governed_by"))],
        "water": [("cap", f"{c['limit_gpd']:,} gal/day" if c.get("limit_gpd") else None),
                  ("reporting", yn(c.get("reporting"))),
                  ("recycling required", yn(c.get("recycling_required"))), ("cooling", c.get("cooling"))],
        "noise": [("limit", f"{c['limit_dba']} dBA" if c.get("limit_dba") else None),
                  ("measured at", c.get("measured_at")),
                  ("setback", f"{c['setback_ft']} ft" if c.get("setback_ft") else None)],
        "jobs": [("permanent", c.get("promised_permanent")), ("construction", c.get("promised_construction")),
                 ("local hire", yn(c.get("local_hire"))), ("prevailing wage", yn(c.get("prevailing_wage"))),
                 ("enforceable", yn(c.get("enforceable")))],
        "local_contracting": [("local subcontracting", yn(c.get("local_subcontracting"))),
                              ("road repair", yn(c.get("road_repair"))),
                              ("infrastructure", usd(c.get("infrastructure_contribution_usd")))],
        "transparency": [("NDA", yn(c.get("nda"))), ("agreement public", yn(c.get("agreement_public"))),
                         ("dashboard", yn(c.get("public_dashboard"))), ("audit", c.get("audit_cadence")),
                         ("independent", yn(c.get("audit_independent")))],
        "tax": [("abatement", f"{c['abatement_pct']}%" if c.get("abatement_pct") is not None else None),
                ("years", c.get("duration_years")), ("PILOT", yn(c.get("pilot"))),
                ("forgone", usd(c.get("estimated_forgone_usd"))), ("but-for test", yn(c.get("but_for_test")))],
    }.get(key, [])
    out = [f"{k}: {str(v).replace('_', ' ')}" for k, v in pairs if v not in (None, "", [])]
    return " · ".join(out)


def render_deal_page(deal, deals):
    j, s = deal["jurisdiction"], score(deal)
    who = short_name(deal)
    place = f"{j['locality']}, {j['state']}"
    title = f"{place} data center agreement: what was actually signed"
    met = sum(1 for v in s.values() if v == "meets")
    short_n = sum(1 for v in s.values() if v == "falls_short")
    desc = (f"{place}: {deal['project']}. Scored against ten terms a good data center agreement should "
            f"include. {met} met, {short_n} fell short. Every term cited to the document.")
    url = f"https://futurepickleballcourt.com/deals/{deal['id']}.html"

    h = [f'<!doctype html><html lang="en"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         f'<title>{esc(title)}</title>',
         f'<meta name="description" content="{esc(desc)}">',
         f'<link rel="canonical" href="{esc(url)}">',
         f'<meta property="og:title" content="{esc(place)}: what was actually signed">',
         f'<meta property="og:description" content="{esc(desc)}">',
         '<meta property="og:type" content="article">',
         f'<meta property="og:url" content="{esc(url)}">',
         '<meta property="og:image" content="https://futurepickleballcourt.com/og.png">',
         '<meta name="twitter:card" content="summary_large_image">',
         '<link rel="icon" href="/favicon.svg" type="image/svg+xml">',
         '<link rel="icon" href="/favicon-32.png" sizes="32x32" type="image/png">',
         '<link rel="apple-touch-icon" href="/apple-touch-icon.png">',
         '<link rel="preconnect" href="https://fonts.googleapis.com">',
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
         '<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Courier+Prime:ital,wght@0,400;0,700;1,400&display=swap" rel="stylesheet">',
         "<style>" + DEAL_CSS.strip() + "</style>",
         '</head><body><div class="page">',
         '<div class="mast"><div><b>Exhibit A</b> &nbsp;·&nbsp; <a href="/">All 15 agreements</a></div>'
         f'<div>futurepickleballcourt.com &nbsp;·&nbsp; {date.today().isoformat()}</div></div>',
         f'<h1>{esc(place)}</h1>',
         f'<p class="who">{esc(who) if who else esc(deal["project"])}</p>']

    when = (f"signed {deal['dates']['signed']}" if deal["dates"].get("signed")
            else f"approved {deal['dates']['approved']}" if deal["dates"].get("approved") else deal["status"])
    facts = [f"<b>{esc(deal['project'])}</b>",
             f"{esc(deal['agreement_type'].replace('_', ' '))}, {esc(deal['status'])}, {esc(when)}"]
    if deal.get("operator"):
        facts.append(f"Operator: {esc(deal['operator'])}")
    if deal.get("developer"):
        facts.append(f"Developer: {esc(deal['developer'])}")
    sc = deal.get("scale") or {}
    bits = []
    for k, u in (("mw", "MW"), ("acres", "acres"), ("sqft", "sq ft"), ("capex_usd", "USD")):
        if sc.get(k):
            bits.append(f"{format(sc[k], ',')} {u}")
    if bits:
        facts.append("Scale: " + esc(" · ".join(bits)))
    if sc.get("notes"):
        facts.append(f"<span style='color:var(--ink-3)'>{esc(sc['notes'])}</span>")
    facts.append("Verification: " + {"primary": "read from the signed document",
                                     "press": "from news reports",
                                     "unverified": "not yet verified"}.get(deal["verification"], deal["verification"]))
    h.append('<p class="facts">' + "<br>".join(facts) + "</p>")

    h.append('<h2>How it scores</h2><div class="strip">')
    for name, spec in FLOOR["clauses"].items():
        v = s[name]
        h.append(f'<span class="chip"><span class="st {_cls(v)}">{VERDICT_WORD[v]}</span>'
                 f'<span class="n">{esc(spec["label"])}</span></span>')
    h.append("</div>")

    h.append("<h2>Documents</h2><ol class=\"src\">")
    for d in deal["documents"]:
        h.append(f'<li><a href="{esc(d["url"])}" rel="noopener">{esc(d["title"])}</a> ({esc(d["kind"].replace("_", " "))})</li>')
    h.append("</ol>")

    h.append("<h2>Term by term</h2>")
    for name, spec in FLOOR["clauses"].items():
        c, v = deal["terms"][name], s[name]
        h.append('<div class="clause">')
        h.append(f'<h3><span class="st {_cls(v)}">{VERDICT_WORD[v]}</span>{esc(spec["label"])}'
                 f'<span class="sr"> {VERDICT_SAID[v]}</span></h3>')
        h.append(f'<p class="asks">A strong version: {esc(spec["asks"])}</p>')
        if c.get("not_applicable"):
            h.append("<p>Not applicable to this deal.</p>")
        elif c.get("present") is False:
            h.append("<p>Not addressed in the agreement.</p>")
        f = _facts_for(name, c)
        if f:
            h.append(f'<p class="data">{esc(f)}</p>')
        if c.get("notes"):
            h.append(f"<p>{esc(c['notes'])}</p>")
        if c.get("hedge"):
            h.append(f'<p class="hedge"><b>Softening language</b>{esc(c["hedge"])}</p>')
        srcs = c.get("sources") or []
        if srcs:
            h.append('<ol class="src">')
            for x in srcs:
                if isinstance(x, dict):
                    where = f"<b>{esc(x['where'])}.</b> " if x.get("where") else ""
                    quote = f' <span class="q">&ldquo;{esc(x["quote"])}&rdquo;</span>' if x.get("quote") else ""
                    h.append(f'<li>{where}<a href="{esc(x["url"])}" rel="noopener">{esc(x["url"])}</a>{quote}</li>')
                else:
                    h.append(f'<li><a href="{esc(x)}" rel="noopener">{esc(x)}</a></li>')
            h.append("</ol>")
        h.append("</div>")

    if deal.get("criticisms"):
        h.append('<h2>Reported criticisms</h2><div class="crit">')
        for x in deal["criticisms"]:
            h.append(f'<p>{esc(x["summary"])} <a href="{esc(x["source"])}" rel="noopener">source</a></p>')
        h.append("</div>")

    others = [d for d in deals if d["id"] != deal["id"]]
    h.append('<h2>The other agreements</h2><div class="other">')
    h.append(" &nbsp;·&nbsp; ".join(
        f'<a href="{o["id"]}.html">{esc(o["jurisdiction"]["locality"])}, {esc(o["jurisdiction"]["state"])}</a>'
        for o in others))
    h.append("</div>")

    h.append(f'<footer>Last reviewed {esc(deal["last_reviewed"])}. '
             'Prepared by <a href="https://aaronpeabody.dev">Aaron Peabody</a>.<br>'
             'Data CC BY 4.0 · <a href="/">See all fifteen agreements side by side</a> · '
             '<a href="https://github.com/apeabody007/futurepickleballcourt">Source and corrections</a><br>'
             'Something wrong here? Email deals@futurepickleballcourt.com with the document.'
             "</footer></div></body></html>")
    return "\n".join(h) + "\n"


SHORT_LBL = {"community_fund": "Fund", "clawbacks": "Claw", "decommissioning": "Decom",
             "grid_costs": "Grid", "water": "Water", "noise": "Noise", "jobs": "Jobs",
             "local_contracting": "Local", "transparency": "Transp", "tax": "Tax"}
HEAD_LBL = {"community_fund": "Community fund", "clawbacks": "Claw&shy;backs",
            "decommissioning": "Decommis&shy;sioning", "grid_costs": "Grid costs",
            "water": "Water", "noise": "Noise", "jobs": "Jobs",
            "local_contracting": "Local con&shy;tracting", "transparency": "Transpar&shy;ency",
            "tax": "Tax breaks"}
STAMP_WORD = {"meets": "Met", "falls_short": "Short", "unknown": "?", "n/a": "n/a"}
VERIF_SHORT = {"primary": "document", "press": "press", "unverified": "unverified"}


def render_matrix(deals):
    """The scorecard, in the HTML. JavaScript only attaches behaviour to it."""
    keys = list(FLOOR["clauses"])
    h = ['<colgroup><col class="c-name">' + "<col>" * len(keys) + '<col class="c-ver"></colgroup>']
    h.append('<thead><tr><th scope="col">Deal</th>'
             + "".join(f'<th scope="col">{HEAD_LBL.get(k, esc(FLOOR["clauses"][k]["label"]))}</th>' for k in keys)
             + '<th scope="col">Source</th></tr></thead><tbody>')
    for i, d in enumerate(deals):
        j, s = d["jurisdiction"], score(d)
        place = f"{j['locality']}, {j['state']}"
        who = short_name(d) or d["project"].split(",")[0]
        h.append(f'<tr class="deal" id="{esc(d["id"])}" data-i="{i}" tabindex="0" role="button"'
                 f' aria-expanded="false" aria-controls="sheet"'
                 f' aria-label="{esc(place)}. Show the terms of this agreement."'
                 f' style="animation-delay:{i * 60}ms">'
                 f'<td><span class="name"><span class="idx">A-{i + 1}</span>{esc(place)}</span>'
                 f'<span class="who"><span class="idx"></span>{esc(who)}</span></td>')
        for k in keys:
            v = s[k]
            said = f'{FLOOR["clauses"][k]["label"]}: {v.replace("_", " ")}'
            h.append(f'<td><span class="lbl" aria-hidden="true">{SHORT_LBL[k]}</span>'
                     f'<span class="st {_cls(v)}" aria-hidden="true">{STAMP_WORD[v]}</span>'
                     f'<span class="sr">{esc(said)}</span></td>')
        h.append(f'<td>{esc(VERIF_SHORT.get(d["verification"], d["verification"]))}</td></tr>')
    h.append("</tbody>")
    return "".join(h)


def render_findings_cards(deals):
    return "".join(f'<div class="finding"><h3>{esc(t)}</h3><p>{esc(body)}</p></div>'
                   for t, body in findings(deals))


def render_checklist_html(deals):
    rows = checklist_rows(deals)
    if not CHECKLIST or not rows:
        return "", "", ""
    src = CHECKLIST["source"]
    note = (f'{esc(CHECKLIST["note"])} <a href="{esc(src["url"])}" rel="noopener">{esc(src["what"])}</a>. '
            f'{esc(src["rule"])}')
    body = ['<div class="ckhead"><div>What he asked for</div><div>Clears it</div><div>Best on record</div></div>']
    for r in rows:
        if r["best_where"]:
            best = f'<a href="#{esc(r["best_id"])}">{esc(r["best_where"])}</a>'
            if not r["best_meets"]:
                best += '<span class="tag">closest, still short</span>'
        else:
            best = "none"
        zero = " zero" if r["met"] == 0 else ""
        body.append(f'<div class="ckrow"><div class="ask">{esc(r["ask"])}</div>'
                    f'<div class="cnt{zero}"><b>{r["met"]}</b> of {r["of"]}</div>'
                    f'<div class="best">{best}</div></div>')
    gaps = "<br><br>".join(f'<b>Not scored: {esc(x["ask"])}.</b> {esc(x["why"])}'
                           for x in CHECKLIST["not_scored"])
    gaps += "<br><br>" + esc(src["note"])
    return note, "".join(body), gaps


def render_strongest_html(deals):
    out = []
    for e in strongest_examples(deals):
        src = ""
        if e.get("cite") and e.get("url"):
            q = f' &ldquo;{esc(e["quote"])}&rdquo;' if e.get("quote") else ""
            src = (f'<div class="src">{esc(e["cite"])}{q} &middot; '
                   f'<a href="{esc(e["url"])}" rel="noopener">document</a></div>')
        elif e.get("url"):
            src = f'<div class="src"><a href="{esc(e["url"])}" rel="noopener">document</a></div>'
        lang = ""
        if e.get("language"):
            lang = (f'<blockquote class="clause-text">{esc(e["language"])}'
                    f'<cite>{esc(e["where"])}{", " + esc(e["who"]) if e.get("who") else ""}</cite></blockquote>')
        who = f', {esc(e["who"])}' if e.get("who") else ""
        out.append(f'<div class="strongrow"><div>{esc(e["label"])}'
                   f'<span class="place">{esc(e["where"])}{who}</span></div>'
                   f'<div><p>{esc(e["why"])}</p>{lang}{src}</div></div>')
    return "".join(out)


def render_wanted_html():
    return "".join(f'<li><b>{esc(w["place"])}.</b> {esc(w["what"])} '
                   f'<span class="meta">Needed: {esc(w["document"])}</span></li>'
                   for w in WANTED.get("wanted", []))


def render_floor_html():
    return "".join(f'<li><b>{esc(c["label"])}.</b> {esc(c["asks"])}</li>'
                   for c in FLOOR["clauses"].values())


def head_to_head(deals):
    """Best and worst on record, so a reader sees the spread instead of a wall of red.

    Ranked by clauses met, then by how few are unknown, so a deal is not
    rewarded for being unreadable. Deals with too little information to judge
    are excluded from the worst slot.
    """
    if len(deals) < 2:
        return None
    scored = []
    for d in deals:
        s = score(d)
        vals = list(s.values())
        applicable = [v for v in vals if v != "n/a"]
        scored.append({
            "deal": d, "s": s,
            "met": vals.count("meets"),
            "short": vals.count("falls_short"),
            "unknown": vals.count("unknown"),
            "applicable": len(applicable),
            "rate": (vals.count("meets") / len(applicable)) if applicable else 0.0,
        })
    judged = [x for x in scored if x["unknown"] <= 3]
    if len(judged) < 2:
        judged = scored
    best = max(judged, key=lambda x: (x["rate"], x["met"], -x["unknown"]))
    worst = min(judged, key=lambda x: (x["rate"], x["met"], -x["short"]))
    if best["deal"]["id"] == worst["deal"]["id"]:
        return None

    def side(x):
        d = x["deal"]
        j = d["jurisdiction"]
        return {
            "id": d["id"],
            "where": f"{j['locality']}, {j['state']}",
            "who": short_name(d),
            "met": x["met"], "short": x["short"], "unknown": x["unknown"],
            "applicable": x["applicable"],
            "verification": d["verification"],
            "scores": x["s"],
        }
    return {
        "best": side(best), "worst": side(worst),
        "clauses": [{"key": k, "label": v["label"]} for k, v in FLOOR["clauses"].items()],
    }


def render_head_to_head(deals):
    h2h = head_to_head(deals)
    if not h2h:
        return ""
    b, w = h2h["best"], h2h["worst"]
    out = ['<div class="h2h">']
    out.append('<div class="h2hhead"><div></div>'
               f'<div class="side ok"><a href="#{esc(b["id"])}">{esc(b["where"])}</a>'
               f'<span>{esc(b["who"])}</span><b>{b["met"]} of {b["applicable"]} met</b></div>'
               f'<div class="side bad"><a href="#{esc(w["id"])}">{esc(w["where"])}</a>'
               f'<span>{esc(w["who"])}</span><b>{w["met"]} of {w["applicable"]} met</b></div></div>')
    for c in h2h["clauses"]:
        k = c["key"]
        bv, wv = b["scores"][k], w["scores"][k]
        out.append(f'<div class="h2hrow"><div class="term">{esc(c["label"])}</div>'
                   f'<div><span class="st {_cls(bv)}">{STAMP_WORD[bv]}</span></div>'
                   f'<div><span class="st {_cls(wv)}">{STAMP_WORD[wv]}</span></div></div>')
    out.append("</div>")
    return "".join(out)


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
    cr = checklist_rows(deals)
    if cr and CHECKLIST:
        lines += ["", f"## {CHECKLIST['title']}", "", CHECKLIST["note"], ""]
        lines += ["| What he asked for | Signed agreements that clear it | Best on record |", "|---|---|---|"]
        for r in cr:
            best = (f"{r['best_where']}" + ("" if r["best_meets"] else " (closest, still short)")) if r["best_where"] else "none"
            lines.append(f"| {r['ask']} | {r['met']} of {r['of']} | {best} |")
        lines.append("")
        for x in CHECKLIST["not_scored"]:
            lines.append(f"**Not scored: {x['ask']}.** {x['why']}")
            lines.append("")

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
    ck_note, ck_rows, ck_gaps = render_checklist_html(deals)
    for token, html in (
        ("<!--FINDINGS-->", render_findings_cards(deals)),
        ("<!--MATRIX-->", render_matrix(deals)),
        ("<!--CHECKLIST_NOTE-->", ck_note),
        ("<!--CHECKLIST_ROWS-->", ck_rows),
        ("<!--CHECKLIST_GAPS-->", ck_gaps),
        ("<!--STRONGEST-->", render_strongest_html(deals)),
        ("<!--HEAD2HEAD-->", render_head_to_head(deals)),
        ("<!--WANTED-->", render_wanted_html()),
        ("<!--WANTED_NOTE-->", esc(WANTED.get("note", ""))),
        ("<!--FLOOR-->", render_floor_html()),
        ("<!--PROMPT-->", esc(render_prompt(deals))),
        ("<!--COUNT-->", f"{len(deals)} deals &middot; {len(FLOOR['clauses'])} terms"),
        ("<!--GENERATED-->", date.today().isoformat()),
    ):
        template = template.replace(token, html)
    payload = json.dumps({
        "generated": date.today().isoformat(),
        "floor": FLOOR["clauses"],
        "findings": [{"title": t, "body": x} for t, x in findings(deals)],
        "strongest": strongest_examples(deals),
        "checklist": (dict(CHECKLIST, rows=checklist_rows(deals)) if CHECKLIST else None),
        "strongest_note": FLOOR.get("strongest_note", ""),
        "prompt": render_prompt(deals),
        "wanted": WANTED.get("wanted", []),
        "wanted_note": WANTED.get("note", ""),
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
        ROOT / "docs" / "all.md": render_all_markdown(deals),
        ROOT / "docs" / "robots.txt": (
            "User-agent: *\n"
            "Allow: /\n\n"
            "Sitemap: https://futurepickleballcourt.com/sitemap.xml\n"
        ),
        ROOT / "docs" / "sitemap.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "".join(
                f"  <url><loc>https://futurepickleballcourt.com{path}</loc>"
                f"<lastmod>{date.today().isoformat()}</lastmod>"
                f"<changefreq>weekly</changefreq><priority>{pri}</priority></url>\n"
                for path, pri in ([("/", "1.0"), ("/all.md", "0.6"), ("/prompt.txt", "0.6")]
                                  + [(f"/deals/{d['id']}.html", "0.8") for d in deals])
            )
            + "</urlset>\n"
        ),
        ROOT / "docs" / "prompt.txt": render_prompt(deals),
        ROOT / "docs" / "all.json": json.dumps({
            "generated": date.today().isoformat(),
            "source": "https://futurepickleballcourt.com",
            "license": "CC BY 4.0",
            "floor": FLOOR["clauses"],
            "findings": [{"title": t, "body": x} for t, x in findings(deals)],
            "strongest": strongest_examples(deals),
        "checklist": (dict(CHECKLIST, rows=checklist_rows(deals)) if CHECKLIST else None),
            "deals": [dict(d, score=score(d)) for d in deals],
        }, indent=2, ensure_ascii=False) + "\n",
    }
    for d in deals:
        outputs[ROOT / "docs" / "deals" / f"{d['id']}.html"] = render_deal_page(d, deals)

    stale = []
    for path, content in outputs.items():
        if check:
            if not path.exists() or path.read_text() != content:
                stale.append(path.relative_to(ROOT))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    if check and stale:
        print("Outputs are stale, run `python3 scripts/build.py` and commit:")
        for p in stale:
            print(f"   {p}")
        sys.exit(1)
    print(f"OK: {len(deals)} deal(s) valid" + ("" if check else ", outputs regenerated"))


if __name__ == "__main__":
    main()
