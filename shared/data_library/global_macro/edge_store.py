"""측정 엣지 저장소 — append-only. 선언 엣지(관계 원장)와 달리 '잰 값'(연결 강도·수요 판독·비중·점유·베타·주가 연결도)을 월별로 쌓는다.

  DataVault/staging/global_macro/edges/edges.parquet     열: t(월말)·type·src·dst·weight·state·k·evidence(JSON)·source·logged_at
키 (t, type, src, dst) 가 이미 있으면 다시 쓰지 않는다(재계산으로 과거 값이 바뀌어도 처음 기록이 남는다 — 전진 기록과 같은 원칙).
노드 id 규약: g:<상대국 그룹> · kr:exp(한국 수출) · kr:kospi · s:<U코드> · p:<품목> · k:<A종목> · c:<시장 US/EU/JP/CN> · mk:<us|eu>:<상대국 코드>.
판독 엔진은 이 저장소를 읽지 않는다(2026-10-22 범위 결정: 엔진의 저장소 읽기는 ICIO 승수와 함께 새 전진 트랙으로).
"""
import json
import os
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from . import store

COLS = ["t", "type", "src", "dst", "weight", "state", "k", "evidence", "source", "logged_at"]
KEY = ["t", "type", "src", "dst"]
UNITS: Dict[str, str] = {
    "link": "상대국 경기 → 한국 수출 최근 5년 상관(k = 선행 개월)", "demand": "6개월 뒤 수출 전년비 예측 변화(로그)", "export_share": "한국 총수출 비중(%)",
    "sector_reading": "업종 합성 점수 z(상태 = 우호/비우호, evidence D = 수요 충격)", "sector_exposure": "업종 품목 수출 중 상대국 비중(그 해, 0~1)",
    "equity_spillover": "그 시장 주가 → 코스피 주간 변동 몫(%, 5년 창)", "import_share": "수입 시장 점유(%, 12개월 합; evidence chg3y = 3년 변화 %p)",
    "stock_beta": "종목 업종 베타(36개월, 최신 판독 달에만)",
}
YEARLY = {"sector_exposure"}            # t = 그 해 12-31, 판독 t 는 (t−1 월의 전년) 비중을 쓴다
LATEST_ONLY = {"stock_beta"}


def path() -> str:
    return store.path("edges", "edges.parquet")


def read() -> pd.DataFrame:
    fp = path()
    if not os.path.exists(fp):
        return pd.DataFrame(columns=COLS)
    return pd.read_parquet(fp)


def append(rows: pd.DataFrame) -> int:
    """새 키만 덧붙인다. 반환 = 추가 건수. 기존 행은 절대 바꾸지 않는다."""
    if rows is None or not len(rows):
        return 0
    rows = rows.copy()
    rows["t"] = pd.to_datetime(rows["t"]).map(_me)                       # 저장 t 는 항상 월말
    for c in COLS:
        if c not in rows:
            rows[c] = None
    rows["logged_at"] = datetime.now().isoformat(timespec="seconds")
    rows = rows[COLS].drop_duplicates(KEY)
    old = read()
    if len(old):
        old["t"] = pd.to_datetime(old["t"])
        have = set(map(tuple, old[KEY].astype(str).values))
        rows = rows[[tuple(x) not in have for x in rows[KEY].astype(str).values]]
    if not len(rows):
        return 0
    out = pd.concat([old, rows], ignore_index=True) if len(old) else rows
    os.makedirs(os.path.dirname(path()), exist_ok=True)
    tmp = path() + ".tmp"
    out.to_parquet(tmp, index=False)
    os.replace(tmp, path())
    return int(len(rows))


def months() -> List[str]:
    df = read()
    if not len(df):
        return []
    m = df[df["type"] == "link"] if (df["type"] == "link").any() else df[~df["type"].isin(YEARLY | LATEST_ONLY)]   # 슬라이더 달 = 판독이 있는 달(2008-01~)
    return sorted({str(pd.Timestamp(t).date()) for t in m["t"].unique()})


def _me(t) -> pd.Timestamp:
    return (pd.Timestamp(t) + pd.offsets.MonthEnd(0)).normalize()


def at(t, types: Optional[List[str]] = None) -> pd.DataFrame:
    """월 t 에 '보이는' 엣지: 월별 종류는 t 와 같은 달, 연도 종류는 (t−1 월의 전년) 12-31 행, 최신 전용 종류는 t 가 그 종류의 마지막 달일 때만."""
    df = read()
    if not len(df):
        return df
    df["t"] = pd.to_datetime(df["t"])
    t = _me(t)
    if types:
        df = df[df["type"].isin(types)]
    yr = (t - pd.offsets.MonthEnd(1)).year - 1
    parts = [df[(~df["type"].isin(YEARLY | LATEST_ONLY)) & (df["t"] == t)],
             df[df["type"].isin(YEARLY) & (df["t"] == pd.Timestamp(f"{yr}-12-31"))]]
    lo = df[df["type"].isin(LATEST_ONLY)]
    if len(lo):
        parts.append(lo[lo["t"] == t])
    return pd.concat(parts, ignore_index=True)


def _ev(d: dict) -> str:
    return json.dumps({k: (None if v is None or (isinstance(v, float) and pd.isna(v)) else v) for k, v in d.items()}, ensure_ascii=False)


def collect(history: Optional[pd.DataFrame], reading: dict, relations: dict, influence: dict) -> pd.DataFrame:
    """운영 산출물(판독 이력·reading.json·relations·influence)에서 측정 엣지 행을 만든다(값은 산출물 그대로, 해석 없음)."""
    rows = []
    if history is not None and len(history):
        for r in history.itertuples(index=False):
            if r.kind == "link":
                rows.append({"t": r.t, "type": "link", "src": f"g:{r.key}", "dst": "kr:exp", "weight": r.rho, "state": r.state, "k": r.k, "evidence": None, "source": "reading/history"})
                if isinstance(r.call, str):
                    rows.append({"t": r.t, "type": "demand", "src": f"g:{r.key}", "dst": "kr:exp", "weight": r.delta, "state": r.call, "k": None, "evidence": None, "source": "reading/history"})
            elif r.kind == "sector":
                rows.append({"t": r.t, "type": "sector_reading", "src": f"s:{r.key}", "dst": "kr:kospi", "weight": r.rho, "state": r.state, "k": None,
                             "evidence": _ev({"D": r.delta, "reading_D": r.call}), "source": "reading/history"})
    for g, ser in (relations or {}).get("export_shares", {}).items():
        for t, v in ser:
            if v is not None:
                rows.append({"t": t, "type": "export_share", "src": f"g:{g}", "dst": "kr:exp", "weight": v, "state": None, "k": None, "evidence": None, "source": "relations.export_shares"})
    for yr, secs in (reading or {}).get("exposure_by_year", {}).items():
        for s, gs in secs.items():
            for g, w in gs.items():
                rows.append({"t": f"{yr}-12-31", "type": "sector_exposure", "src": f"g:{g}", "dst": f"s:{s}", "weight": w, "state": None, "k": None, "evidence": None, "source": "reading.exposure_by_year"})
    seen_eq = {}
    for r in (influence or {}).get("rolling", []):              # 창 끝 날짜 → 그 달(마지막 창만)
        t = _me(r["date"])
        seen_eq[t] = r
    for t, r in seen_eq.items():
        for mk, v in (r.get("to_kr") or {}).items():
            if mk != "KR":
                rows.append({"t": t, "type": "equity_spillover", "src": f"c:{mk}", "dst": "kr:kospi", "weight": v, "state": None, "k": None, "evidence": None, "source": "influence.rolling"})
    for mk, key in (("us", "us_competition"), ("eu", "eu_competition")):
        for cmd, c in (relations or {}).get(key, {}).items():
            ser_items = c.get("series") or {}
            for partner, ser in (ser_items.items() if isinstance(ser_items, dict) else ser_items):   # 메모리에선 dict, JSON 에선 [[partner, rows]]
                vals = [(t, v) for t, v in ser if v is not None]
                for i, (t, v) in enumerate(vals):
                    prev = vals[i - 36][1] if i >= 36 else None
                    rows.append({"t": t, "type": "import_share", "src": f"mk:{mk}:{partner}", "dst": f"p:{cmd}", "weight": v, "state": None, "k": None,
                                 "evidence": _ev({"chg3y": None if prev is None else round(v - prev, 3)}), "source": f"relations.{key}"})
    t_latest = (reading or {}).get("t")
    if t_latest:
        for s, v in (reading or {}).get("stocks", {}).items():
            for st in v.get("stocks", []):
                rows.append({"t": t_latest, "type": "stock_beta", "src": f"s:{s}", "dst": f"k:{st['code']}", "weight": st.get("beta"), "state": None, "k": None,
                             "evidence": _ev({"name": st.get("name"), "mcap_bn": st.get("mcap_bn"), "ret_6m": st.get("ret_6m")}), "source": "reading.stocks"})
    return pd.DataFrame(rows, columns=[c for c in COLS if c != "logged_at"])


def sync(history: Optional[pd.DataFrame], reading: dict, relations: dict, influence: dict) -> dict:
    rows = collect(history, reading, relations, influence)
    n = append(rows)
    df = read()
    info = {"added": n, "rows": int(len(df)), "months": len(months()), "latest": months()[-1] if len(df) else None,
            "types": {k: int(v) for k, v in df["type"].value_counts().items()} if len(df) else {}, "units": UNITS}
    store.log(f"[edges] +{n} → {info['rows']}행, 월 {info['months']}개, 최신 {info['latest']}")
    return info


def payload(t: Optional[str] = None) -> dict:
    """화면 API: 월 t(없으면 최신)의 엣지 + 월 목록 + 단위."""
    ms = months()
    if not ms:
        return {"t": None, "months": [], "units": UNITS, "edges": []}
    t = str(_me(t).date()) if t else ms[-1]
    df = at(t)
    edges = [{"type": r.type, "src": r.src, "dst": r.dst, "weight": None if pd.isna(r.weight) else float(r.weight), "state": r.state if isinstance(r.state, str) else None,
              "k": None if r.k is None or pd.isna(r.k) else float(r.k), "evidence": json.loads(r.evidence) if isinstance(r.evidence, str) else None} for r in df.itertuples(index=False)]
    return {"t": t, "months": ms, "units": UNITS, "edges": edges}
