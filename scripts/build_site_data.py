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
TYPES = {"supply", "contract", "capital", "financing"}
KINDS = {"backstop", "guarantee", "collateral", "rent_back", "vendor_finance"}
STRENGTH = {"H", "M", "L"}
CONF = {"D", "E", "I", "R"}
FAMILIES = {"GPU", "TPU", "Trainium", "MTIA", "Maia", "OpenAI-XPU"}


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
    name = re.sub(r"^Customer([a-z])$", lambda m: "Customer" + m.group(1).upper(), name)
    name = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name)
    return name.strip()


NOT_CUSTOMER = re.compile(r"(SalesChannel|Geograph|Country|NonUs|UnitedStates|Region|Segment|Product)", re.I)


def months(start, end):
    if not start or not end:
        return None
    y1, m1, d1 = map(int, start.split("-")); y2, m2, d2 = map(int, end.split("-"))
    return max(1, round(((y2 - y1) * 12 + (m2 - m1) + (d2 - d1) / 30)))


WORDN = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5, "Six": 6, "Seven": 7, "Eight": 8, "Nine": 9, "Ten": 10}


def mat_bucket(name):
    """Map a LongTermDebtMaturities* concept to (sort key, offset in fiscal years or 'after N')."""
    n = name.replace("LongTermDebtMaturitiesRepaymentsOfPrincipal", "").replace("Rolling", "")
    if n == "RemainderOfFiscalYear":
        return (0, 0)
    if n in ("InNextTwelveMonths", "InNextTwelveMonth"):
        return (1, 1)
    m = re.fullmatch(r"InYear(\w+)", n)
    if m and m.group(1) in WORDN:
        return (WORDN[m.group(1)], WORDN[m.group(1)])
    m = re.fullmatch(r"AfterYear(\w+)", n)
    if m and m.group(1) in WORDN:
        return (WORDN[m.group(1)] + 0.5, f"after{WORDN[m.group(1)]}")
    if n == "Thereafter":
        return (99, "after")
    return None


def fy_end_month(rec):
    for f in rec.get("filings", []):
        if f.get("kind") == "annual":
            m = re.search(r"-(\d{4})(\d{2})(\d{2})\.htm", f.get("url", ""))
            if m:
                return int(m.group(2))
    return 12


def credit_facts(rec):
    """Latest company-wide credit facts across the filings pulled for one company."""
    latest = {}
    for f in rec.get("filings", []):
        for x in f.get("facts", []):
            name = x["concept"].split(":", 1)[-1]
            if x["dims"] or not (x.get("unit") or "").lower().startswith("usd"):
                continue
            if "Concentration" in name or "RemainingPerformance" in name:
                continue
            key = name
            cur = latest.get(key)
            if cur is None or (x["end"] or "") > (cur["end"] or ""):
                latest[key] = {"v": x["value"], "end": x["end"], "form": f["form"], "url": f["url"]}
    out = {}
    # maturity ladder: all LongTermDebtMaturities* facts sharing the most recent end date, labelled by fiscal year
    mats = {k: v for k, v in latest.items() if k.startswith("LongTermDebtMaturitiesRepaymentsOfPrincipal")}
    if mats:
        end = max(v["end"] for v in mats.values())
        y, mth = int(end[:4]), int(end[5:7])
        fym = fy_end_month(rec)
        fy = y if mth <= fym else y + 1          # fiscal year containing the period end
        cal = fym == 12
        rows = []
        for k, v in mats.items():
            b = mat_bucket(k)
            if b and v["end"] == end:
                rows.append((b, v["v"]))
        rows.sort(key=lambda t: t[0][0])
        has_rem = any(b[1] == 0 for b, _ in rows)
        ladder = []
        for (key, off), val in rows:
            if off == 0:
                lab = f"Rest of {fy}" if cal else f"Rest of FY{fy}"
            elif isinstance(off, int):
                yr = fy + off if has_rem or mth == fym else fy + off - 1
                lab = str(yr) if cal else f"FY{yr}"
            else:
                last = max([o for (_, o), _ in rows if isinstance(o, int)] or [0])
                yr = (fy + last if has_rem or mth == fym else fy + last - 1) + 1
                lab = f"{yr}+" if cal else f"FY{yr}+"
            ladder.append((lab, round(val / 1e9, 2)))
        if ladder:
            any_v = next(v for v in mats.values() if v["end"] == end)
            out["maturities"] = {"end": end, "form": any_v["form"], "url": any_v["url"], "ladder": ladder}
    def pick(*names):
        for n in names:
            for k, v in latest.items():
                if k == n:
                    return {"usd_bn": round(v["v"] / 1e9, 2), "end": v["end"], "form": v["form"], "url": v["url"], "concept": k}
        return None
    nyc = [(k, v) for k, v in latest.items() if "NotYetCommenced" in k and v["v"] > 1e8]
    if nyc:
        k, v = max(nyc, key=lambda kv: kv[1]["v"])
        out["leases_not_commenced"] = {"usd_bn": round(v["v"] / 1e9, 2), "end": v["end"], "form": v["form"], "url": v["url"], "concept": k}
    out["purchase_obligations"] = pick("UnrecordedUnconditionalPurchaseObligationBalanceSheetAmount", "PurchaseObligation")
    out["customer_prepayments"] = pick("ContractWithCustomerLiability")
    if not out["customer_prepayments"]:
        c1, c2 = pick("ContractWithCustomerLiabilityCurrent"), pick("ContractWithCustomerLiabilityNoncurrent")
        if c1:
            out["customer_prepayments"] = {**c1, "usd_bn": round(c1["usd_bn"] + (c2["usd_bn"] if c2 and c2["end"] == c1["end"] else 0), 2)}
    # Guarantee maximum-exposure tags proved unreliable (e.g. a mis-tagged NVIDIA value), so guarantees are curated
    # by hand in data/funding.yaml instead of shown from XBRL.
    out["op_lease_payments"] = pick("LesseeOperatingLeaseLiabilityPaymentsDue")
    out["fin_lease_payments"] = pick("FinanceLeaseLiabilityPaymentsDue")
    return {k: v for k, v in out.items() if v}


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
        out[cid] = {"cik": rec.get("cik"), "filings": groups, "rpo": rpo, "credit": credit_facts(rec)}
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
        if e.get("conf") in ("D", "E", "R") and not (e.get("url") or e.get("xbrl")):
            errs.append(f"{tag}: conf {e['conf']} needs a url or an xbrl reference")
        if e.get("conf") in ("D", "E", "R") and not (e.get("as_of") or e.get("xbrl")):
            errs.append(f"{tag}: conf {e['conf']} needs as_of")
        bad = set(e.get("fam") or []) - FAMILIES
        if bad:
            errs.append(f"{tag}: unknown fam {sorted(bad)}")
        if e.get("type") == "financing" and e.get("kind") not in KINDS:
            errs.append(f"{tag}: financing edge needs kind in {sorted(KINDS)}")
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

    fp = ROOT / "data/funding.yaml"
    funding = (yaml.safe_load(fp.read_text()) or {}).get("funding", {}) if fp.exists() else {}
    for cid, f in funding.items():
        if cid not in idset:
            errs.append(f"funding.yaml: unknown company id {cid}")
            continue
        for it in f.get("items", []) + f.get("signals", []):
            if not it.get("url"):
                errs.append(f"funding.yaml {cid}: item without url: {it.get('note') or it.get('text')}")
    for c in uni["companies"]:
        if c["id"] in funding:
            f = dict(funding[c["id"]])
            if isinstance(f.get("maturities"), dict):   # keep YAML order (JS would re-sort numeric keys)
                f["maturities"] = [[str(k), v] for k, v in f["maturities"].items()]
            c["funding"] = f
    pp = ROOT / "data/programmes.yaml"
    progs = yaml.safe_load(pp.read_text()) if pp.exists() else {}
    for k, pr in (progs.get("programmes") or {}).items():
        if k not in FAMILIES:
            errs.append(f"programmes.yaml: unknown programme {k}")
        if pr.get("owner") not in idset:
            errs.append(f"programmes.yaml {k}: unknown owner {pr.get('owner')}")
        for g in (pr.get("generations") or []) + (pr.get("users") or []) + (pr.get("volume") or []):
            if not g.get("url"):
                errs.append(f"programmes.yaml {k}: item without url: {g.get('name') or g.get('who') or g.get('note')}")
    for x in progs.get("exposure") or []:
        if x.get("id") not in idset or x.get("prog") not in FAMILIES or not x.get("url"):
            errs.append(f"programmes.yaml exposure: bad entry {x}")
    for w in uni.get("watchlist") or []:
        if w["id"] in idset:
            errs.append(f"watchlist id {w['id']} is also in the universe")
    tp = ROOT / "data/theses.yaml"
    theses = (yaml.safe_load(tp.read_text()) or {}).get("theses", {}) if tp.exists() else {}
    for c in uni["companies"]:
        if c["id"] in theses:
            c["thesis"] = theses[c["id"]]
    for k in theses:
        if k not in idset:
            errs.append(f"theses.yaml: unknown company id {k}")
    if errs:
        print("Validation failed:\n  " + "\n  ".join(errs))
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (OUT / "universe.json").write_text(json.dumps({"built": stamp, **uni}, indent=1))
    (OUT / "edges.json").write_text(json.dumps({"built": stamp, "edges": edges}, indent=1))
    (OUT / "programmes.json").write_text(json.dumps({"built": stamp, **(progs or {})}, indent=1))
    (OUT / "disclosures.json").write_text(json.dumps({"built": stamp, "sec_pulled": generated, "companies": disc}, indent=1))
    n_conf = {k: sum(1 for e in edges if e["conf"] == k) for k in "DEIR"}
    print(f"OK: {len(ids)} companies, {len(edges)} edges {n_conf}, {merged} XBRL merges, "
          f"{sum(len(g['rows']) for v in disc.values() for g in v['filings'])} disclosed concentration rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
