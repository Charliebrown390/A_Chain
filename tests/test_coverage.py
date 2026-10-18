"""Regression guards for the A_Chain universe and relationship map. Run: python -m pytest -q tests"""
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
UNI = yaml.safe_load((ROOT / "data/universe.yaml").read_text())
EDGES = yaml.safe_load((ROOT / "data/edges.yaml").read_text())["edges"]
IDS = {c["id"] for c in UNI["companies"]}

MUST_HAVE = ["NVDA", "AMD", "AVGO", "2454.TW", "TSM", "ASML", "000660.KS", "MU",
             "GOOGL", "MSFT", "AMZN", "META", "ANET", "LITE"]


def test_must_have_tickers_present():
    missing = [t for t in MUST_HAVE if t not in IDS]
    assert not missing, f"must-have tickers missing from universe: {missing}"


def test_every_company_has_a_layer_and_role():
    layers = {l["n"] for l in UNI["layers"]}
    bad = [c["id"] for c in UNI["companies"] if c["layer"] not in layers or not c.get("role")]
    assert not bad, f"companies with bad layer or no role: {bad}"


def test_sourced_links_have_source_and_date():
    bad = []
    for e in EDGES:
        if e["conf"] in ("D", "E", "R"):
            if not (e.get("url") or e.get("xbrl")) or not (e.get("as_of") or e.get("xbrl")):
                bad.append(f"{e['from']}->{e['to']} ({e['type']}, {e['conf']})")
    assert not bad, f"disclosed/estimated/rumoured links missing source or as_of: {bad}"


def test_every_link_has_confidence_and_note():
    bad = [f"{e['from']}->{e['to']}" for e in EDGES if e.get("conf") not in ("D", "E", "I", "R") or not e.get("note")]
    assert not bad, f"links without a valid confidence tag or note: {bad}"


def test_links_point_at_known_companies():
    bad = [f"{e['from']}->{e['to']}" for e in EDGES if e["from"] not in IDS or e["to"] not in IDS]
    assert not bad, f"links to unknown companies: {bad}"


def test_tpu_chain_present():
    tpu = {(e["from"], e["to"]) for e in EDGES if "TPU" in (e.get("fam") or [])}
    for pair in [("AVGO", "GOOGL"), ("2454.TW", "GOOGL"), ("GOOGL", "Anthropic")]:
        assert pair in tpu, f"TPU link {pair} missing"


def test_market_data_covers_universe():
    p = ROOT / "site/data/market.json"
    if not p.exists():
        return
    m = json.loads(p.read_text())["companies"]
    listed = [c["id"] for c in UNI["companies"] if c.get("yf")]
    missing = [i for i in listed if i not in m or m[i].get("last") is None]
    assert len(missing) <= max(2, len(listed) // 10), f"too many listed companies without prices: {missing}"
