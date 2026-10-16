"""Validate the hand-curated map and compile it to JSON for the website.

Inputs : data/universe.yaml, data/edges.yaml, data/raw/sec_concentration.json (optional)
Outputs: site/data/universe.json, site/data/edges.json, site/data/disclosures.json

Run: python scripts/build_site_data.py        (exits non-zero if validation fails)
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "site/data"
TYPES = {"supply", "contract", "capital"}
STRENGTH = {"H", "M", "L"}
CONF = {"D", "E", "I"}


def benchmark(dims):
    b = " ".join(v for k, v in dims.items() if "Benchmark" in k).lower()
    if "receivable" in b:
        return "receivables"
    if "revenue" in b or "sales" in b:
        return "revenue"
    if "purchase" in b or "cost" in b:
        return "purchases"
    return "other"


def customer_member(dims):
    for k, v in dims.items():
        if re.search(r"(MajorCustomer|Customer|Counterpart)", k, re.I):
            return v
    return None


def clean_member(m):
    """nvda:CustomerAMember -> Customer A ; anet:MicrosoftCorporationMember -> Microsoft Corporation"""
    name = m.split(":", 1)[-1]
    name = re.sub(r"Member$", "", name)
    name = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name)
    return name.strip()


NOT_CUSTOMER = re.compile(r"(SalesChannel|Geograph|Country|NonUs|UnitedStates|Region|Segment|Product)", re.I)


def months(start, end):
    if not start or not end:
        return None
    y1, m1, d1 = map(int, start.split("-")); y2, m2, d2 = map(int, end.split("-"))
    return max(1, round(((y2 - y1) * 12 + (m2 - m1) + (d2 - d1) / 30)))


def load_disclosures():
    """Per company: for each filing, the customer-concentration rows for that filing's own
    latest period (longest span at the latest end date), so comparatives are dropped."""
    p = ROOT / "data/raw/sec_concentration.json"
    if not p.exists():
        return {}, None
    raw = json.loads(p.read_text())
    out = {}
    for cid, rec in raw.get("companies", {}).items():
        groups, rpo = [], None
        for f in rec.get("filings", []):
            rows = []
            for fact in f.get("facts", []):
                c = fact["concept"]
                if "Concentration" in c:
                    m = customer_member(fact["dims"])
                    if not m or NOT_CUSTOMER.search(m):
                        continue
                    v = fact["value"]
                    rows.append({
                        "customer": clean_member(m), "member": m, "basis": benchmark(fact["dims"]),
                        "pct": round(v * 100 if v <= 1.5 else v, 1),
                        "start": fact.get("start"), "end": fact.get("end"),
                    })
                elif c.endswith(":RevenueRemainingPerformanceObligation") and not fact["dims"]:
                    if rpo is None or (fact["end"] or "") > (rpo["end"] or ""):
                        rpo = {"usd_bn": round(fact["value"] / 1e9, 2), "end": fact["end"], "form": f["form"], "url": f["url"]}
            keep = []
            for basis in ("revenue", "receivables"):
                rs = [r for r in rows if r["basis"] == basis]
                if not rs:
                    continue
                end = max(r["end"] or "" for r in rs)
                rs = [r for r in rs if (r["end"] or "") == end]
                span = max(months(r["start"], r["end"]) or 0 for r in rs)
                rs = [r for r in rs if (months(r["start"], r["end"]) or 0) == span]
                seen = set()
                for r in sorted(rs, key=lambda r: -r["pct"]):
                    if r["customer"] in seen:
                        continue
                    seen.add(r["customer"])
                    r["months"] = span or None
                    keep.append(r)
            if keep:
                groups.append({"form": f["form"], "kind": f["kind"], "filed": f["filed"], "url": f["url"], "rows": keep})
        groups.sort(key=lambda g: g["kind"] != "annual")
        out[cid] = {"cik": rec.get("cik"), "filings": groups, "rpo": rpo}
    return out, raw.get("generated")


def main():
    uni = yaml.safe_load((ROOT / "data/universe.yaml").read_text())
    edges = yaml.safe_load((ROOT / "data/edges.yaml").read_text())["edges"]
    ids = [c["id"] for c in uni["companies"]]
    errs = []
    if len(ids) != len(set(ids)):
        errs.append("duplicate company ids")
    idset = set(ids)
    layers = {l["n"] for l in uni["layers"]}
    for c in uni["companies"]:
        if c["layer"] not in layers:
            errs.append(f"{c['id']}: bad layer {c['layer']}")
    seen = set()
    for i, e in enumerate(edges):
        tag = f"edge {i} {e.get('from')}->{e.get('to')}"
        for k in ("from", "to", "type", "strength", "conf"):
            if k not in e:
                errs.append(f"{tag}: missing {k}")
        if e.get("from") not in idset or e.get("to") not in idset:
            errs.append(f"{tag}: unknown company id")
        if e.get("type") not in TYPES:
            errs.append(f"{tag}: bad type {e.get('type')}")
        if e.get("strength") not in STRENGTH:
            errs.append(f"{tag}: bad strength")
        if e.get("conf") not in CONF:
            errs.append(f"{tag}: bad conf")
        if e.get("conf") in ("D", "E") and not (e.get("url") or e.get("xbrl")):
            errs.append(f"{tag}: conf {e['conf']} needs a url or an xbrl reference")
        key = (e.get("from"), e.get("to"), e.get("type"))
        if key in seen:
            errs.append(f"{tag}: duplicate edge")
        seen.add(key)
    linked = {e["from"] for e in edges} | {e["to"] for e in edges}
    for c in ids:
        if c not in linked:
            errs.append(f"{c}: has no edges")
    if errs:
        print("Validation failed:\n  " + "\n  ".join(errs))
        return 1

    disc, generated = load_disclosures()
    merged = 0
    for e in edges:
        x = e.get("xbrl")
        if not x:
            continue
        hit = [(g, r) for g in disc.get(x["company"], {}).get("filings", []) for r in g["rows"]
               if r["basis"] == "revenue" and x["member"].lower().replace(" ", "") in r["member"].lower()]
        if hit:
            g, r = hit[0]
            e["pct"], e["as_of"] = r["pct"], r["end"]
            e.setdefault("src", f"{x['company']} {g['form']} (XBRL)")
            e["url"] = g["url"]
            if e["conf"] != "D" and "Customer" in r["customer"] and len(r["customer"]) <= 11:
                e["note"] = f"{e.get('note', '')} · % is for anonymised '{r['customer']}'; mapping to this company is an estimate".strip(" ·")
            merged += 1

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (OUT / "universe.json").write_text(json.dumps({"built": stamp, **uni}, indent=1))
    (OUT / "edges.json").write_text(json.dumps({"built": stamp, "edges": edges}, indent=1))
    (OUT / "disclosures.json").write_text(json.dumps({"built": stamp, "sec_pulled": generated, "companies": disc}, indent=1))
    n_conf = {k: sum(1 for e in edges if e["conf"] == k) for k in "DEI"}
    print(f"OK: {len(ids)} companies, {len(edges)} edges {n_conf}, {merged} XBRL merges, "
          f"{sum(len(g['rows']) for v in disc.values() for g in v['filings'])} disclosed concentration rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
