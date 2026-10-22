"""관계 원장 — reference/relations_ledger.json 하나가 모든 대응표(상대국·그룹·업종·품목·수입 시장)의 원본.

코드는 여기 접근자만 쓴다(모듈에 dict 리터럴로 적으면 shared/tests/test_global_macro.py 래칫이 실패).
엣지는 valid_from/valid_to 로 이력을 남긴다 — 바꿀 땐 옛 엣지를 valid_to 로 닫고 새 엣지를 열며 reason 을 적는다.
as_of 를 주면 그 날짜에 유효했던 대응표를 돌려준다(기본 = 지금 유효한 것). 원장은 사전등록 결과의 해시 대상(sha256()).
"""
import hashlib
import json
import os
from datetime import date
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference", "relations_ledger.json")
SCOPES = ("reading", "links", "share")


@lru_cache(maxsize=1)
def load() -> dict:
    with open(PATH, encoding="utf-8") as f:
        L = json.load(f)
    errs = validate(L)
    if errs:
        raise ValueError("relations_ledger.json 검증 실패:\n  " + "\n  ".join(errs))
    return L


def sha256() -> str:
    with open(PATH, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _date(s) -> Optional[date]:
    return date.fromisoformat(s) if s else None


def validate(L: dict) -> List[str]:
    """구조·참조 무결성 — 틀리면 메시지 목록(비어 있으면 통과)."""
    errs = []
    types, nodes, edges = L.get("edge_types", {}), L.get("nodes", {}), L.get("edges", [])
    seen = set()
    for i, e in enumerate(edges):
        tag = f"edges[{i}] {e.get('type')}:{e.get('from')}→{e.get('to')}"
        t = types.get(e.get("type"))
        if not t:
            errs.append(f"{tag}: 엣지 종류가 카탈로그에 없음"); continue
        if e.get("from") not in nodes.get(t["from"], {}):
            errs.append(f"{tag}: from 노드가 nodes.{t['from']} 에 없음")
        if t["to"] in nodes and e.get("to") not in nodes[t["to"]]:
            errs.append(f"{tag}: to 노드가 nodes.{t['to']} 에 없음")
        if not isinstance(e.get("to"), str) or not e["to"]:
            errs.append(f"{tag}: to 비어 있음")
        try:
            vf, vt = _date(e.get("valid_from")), _date(e.get("valid_to"))
        except (TypeError, ValueError):
            errs.append(f"{tag}: valid_from/valid_to 날짜 형식 오류"); continue
        if vf is None:
            errs.append(f"{tag}: valid_from 없음")
        if vt is not None and (vf is None or vt <= vf):
            errs.append(f"{tag}: valid_to 가 valid_from 이전")
        if vt is not None and not e.get("reason"):
            errs.append(f"{tag}: 닫힌 엣지엔 reason 필수")
        if e["type"] == "group_member" and e.get("scope") not in SCOPES:
            errs.append(f"{tag}: scope 는 {SCOPES} 중 하나")
        if e["type"] == "sector_hs" and e.get("mode") not in ("include", "exclude"):
            errs.append(f"{tag}: mode 는 include|exclude")
        key = (e["type"], e.get("from"), e.get("to"), e.get("scope"), e.get("mode"), e.get("valid_from"))
        if key in seen:
            errs.append(f"{tag}: 중복")
        seen.add(key)
    # 의미 검사(지금 유효한 것만)
    act = [e for e in edges if e.get("valid_to") is None]
    for e in act:
        if e["type"] == "sector_hs" and e["mode"] == "exclude":
            owners = [x["from"] for x in act if x["type"] == "sector_hs" and x["mode"] == "include" and x["from"] != e["from"] and x["to"].startswith(e["to"])]
            if not owners:
                errs.append(f"sector_hs {e['from']} exclude {e['to']}: 다른 업종의 include 에 없음(제외분이 갈 곳 없음)")
    need_cli = {e["from"] for e in act if e["type"] == "group_member" and e.get("scope") in ("reading", "links")}
    for g in nodes.get("group", {}):
        if g in need_cli and not any(e["type"] == "group_cli" and e["from"] == g for e in act):
            errs.append(f"group {g}: 판독·연결 강도에 쓰이는 그룹엔 group_cli 필수")
    for e in act:
        if e["type"] == "group_cli" and e["to"] not in {x["to"] for x in act if x["type"] == "country_tiva"}:
            errs.append(f"group_cli {e['from']}→{e['to']}: TiVA 코드가 어느 상대국에도 없음")
    for mk in nodes.get("import_market", {}):
        tot = nodes["import_market"][mk].get("total_code")
        if tot and not any(e["type"] == "market_partner" and e["from"] == mk and e["to"] == tot for e in act):
            errs.append(f"import_market {mk}: total_code {tot} 가 market_partner 에 없음")
    return errs


def edges(kind: str, as_of: Optional[str] = None, **attrs) -> List[dict]:
    """종류·속성으로 고른 엣지(원장 순서 유지). as_of=None 이면 지금 유효한 것."""
    d = _date(as_of) if as_of else None
    out = []
    for e in load()["edges"]:
        if e["type"] != kind or any(e.get(k) != v for k, v in attrs.items()):
            continue
        if d is None:
            if e.get("valid_to") is not None:
                continue
        elif not (_date(e["valid_from"]) <= d and (e.get("valid_to") is None or d < _date(e["valid_to"]))):
            continue
        out.append(e)
    return out


def _one(kind: str, as_of=None, **attrs) -> Dict[str, str]:
    out = {}
    for e in edges(kind, as_of, **attrs):
        if e["from"] in out:
            raise ValueError(f"{kind} {e['from']}: 같은 시점에 대응이 둘 ({out[e['from']]}, {e['to']})")
        out[e["from"]] = e["to"]
    return out


def nodes(layer: str) -> Dict[str, dict]:
    return load()["nodes"][layer]


# ---------------------------------------------------------------- 모듈별 대응표(옛 상수와 같은 모양)
def trade_partners(as_of=None) -> Dict[str, Tuple[str, str, Optional[str]]]:
    """ECOS 코드 → (이름, TiVA 코드, 선행지수 FRED id|None). trade.PARTNERS"""
    tiva, cli = _one("country_tiva", as_of), _one("country_cli", as_of)
    return {c: (n["name"], tiva[c], cli.get(c)) for c, n in nodes("country").items() if c in tiva}


def tiva_aggregates() -> List[str]:
    return list(nodes("tiva_aggregate"))


def aggregate_cli(as_of=None) -> Dict[str, str]:
    return _one("aggregate_cli", as_of)


def groups(scope: str, as_of=None) -> Dict[str, Tuple[List[str], str]]:
    """그룹 이름 → (ECOS 코드 목록, 선행지수 TiVA 코드|None). scope: reading|links|share(비중 전용 그룹은 선행지수 없음)"""
    if scope not in SCOPES:
        raise ValueError(f"scope {scope!r} — {SCOPES}")
    cli = _one("group_cli", as_of)
    out: Dict[str, list] = {}
    for e in edges("group_member", as_of, scope=scope):
        out.setdefault(e["from"], []).append(e["to"])
    return {g: (cs, cli.get(g)) for g, cs in out.items()}


def kr_sectors(as_of=None) -> Dict[str, Tuple[str, str, Optional[str], Optional[str]]]:
    """U코드 → (이름, KF49, FRED 업종 생산 키|None, KSIC2|None). kr_industry.SECTORS"""
    kf, ip, ks = _one("sector_kf49", as_of), _one("sector_us_ip", as_of), _one("sector_ksic", as_of)
    return {u: (n["name"], kf[u], ip.get(u), ks.get(u)) for u, n in nodes("kr_sector").items() if u in kf}


def ksic2_to_sector(as_of=None) -> Dict[str, str]:
    out = {}
    for u, v in _one("sector_ksic", as_of).items():
        if v in out:
            raise ValueError(f"KSIC {v} 가 두 업종({out[v]}, {u})에 대응")
        out[v] = u
    return out


def sector_hs(as_of=None) -> Dict[str, dict]:
    """U코드 → {name, include[], exclude[]}. reading.SECTOR_HS"""
    out: Dict[str, dict] = {}
    for e in edges("sector_hs", as_of):
        spec = out.setdefault(e["from"], {"name": nodes("kr_sector")[e["from"]]["name"], "include": [], "exclude": []})
        spec[e["mode"]].append(e["to"])
    return out


def sector_products(as_of=None) -> Dict[str, List[str]]:
    """U코드 → 수입 경쟁 품목 코드 목록. reading.SECTOR_US"""
    out: Dict[str, list] = {}
    for e in edges("sector_product", as_of):
        out.setdefault(e["from"], []).append(e["to"])
    return out


def products() -> Dict[str, str]:
    return {k: v["name"] for k, v in nodes("product").items()}


def market_partners(market: str, as_of=None) -> Dict[str, str]:
    """수입 시장 상대국 코드 → 표시 이름. us_imports.PARTNERS / eu_imports.PARTNERS"""
    if market not in nodes("import_market"):
        raise ValueError(f"import_market {market!r} 없음")
    return {e["to"]: e["name"] for e in edges("market_partner", as_of, **{"from": market})}


def market(market_key: str) -> dict:
    return nodes("import_market")[market_key]


def summary() -> dict:
    """화면·기록용 요약 — 노드 수·엣지 수·해시."""
    L = load()
    act = [e for e in L["edges"] if e.get("valid_to") is None]
    return {"fixed_at": L["fixed_at"], "sha256": sha256(), "nodes": {k: len(v) for k, v in L["nodes"].items()},
            "edges": {t: sum(1 for e in act if e["type"] == t) for t in L["edge_types"]},
            "closed": sum(1 for e in L["edges"] if e.get("valid_to")), "rules": L.get("rules", [])}


def graph_view(as_of: Optional[str] = None) -> dict:
    """화면 관계 그래프용 — 층별 노드 이름 + 지금(또는 as_of) 유효한 선언 엣지(필요한 속성만)."""
    L = load()
    out_nodes = {layer: {k: (v.get("name") or k) for k, v in nodes.items()} for layer, nodes in L["nodes"].items()}
    out_edges = []
    for t in L["edge_types"]:
        for e in edges(t, as_of):
            rec = {"type": t, "from": e["from"], "to": e["to"]}
            for k in ("scope", "mode", "name", "reason"):
                if e.get(k):
                    rec[k] = e[k]
            out_edges.append(rec)
    return {"nodes": out_nodes, "edges": out_edges}
