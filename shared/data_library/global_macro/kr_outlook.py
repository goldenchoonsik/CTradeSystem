"""업종 수출 전망 원천 — 무역협회 수출산업경기전망지수(EBSI, KOSIS) + 한국은행 업종별 수출전망 BSI(ECOS). 2026-10-23.

  derived/kr_outlook/ebsi.parquet   장형: period(분기 시작일)·code·name·value — 전분기=100, '다음 분기' 수출 경기 전망(분기 직전 달 말 공표)
  derived/kr_outlook/bsi.parquet    장형: period(그 달 1일)·code·name·value  — 100 기준, '그 달' 수출 전망(전달 말 공표)
  raw/kosis/ebsi/<날짜>.json · raw/ecos/bsi_export/<날짜>.json   받은 날짜별 판 보관(공표 뒤 고쳐져도 그날 받은 값이 남음)
업종 대응(어느 품목이 업종의 몇 %인가)은 관계 원장 sector_ebsi·sector_bsi — hs 접두(또는 'rest' = 업종의 나머지)별 전년 세계 수출액(관세청)으로 가중.
쓸 수 있는 시점(보수적, 과거 재현용): EBSI 분기 Q = Q 첫 달 말부터, BSI 월 m = m 달 말부터 — 공표는 그 전 달 말이지만 날짜가 해마다 달라 한 달 늦춘다.
화면(현재 파악)은 공표된 최신값을 쓴다(latest=True).
kosis_search(키워드) = KOSIS 통계표 찾기(표 이름·ID·기간) — 새 지표를 찾을 때.
"""
import json
import os
import re
import urllib.parse
from datetime import date
from typing import Dict, List, Optional

import pandas as pd

from . import ledger, store
from . import reading as R
from .store import log

EBSI_TABLE = ("360", "DT_36005_A001")          # 무역협회 품목별 수출산업경기전망지수(분기, 2009 2/4~)
BSI_TABLE, BSI_CODE, BSI_START = "512Y008", "BM", "200909"     # 한국은행 업종별 BSI(전망) · 수출전망BSI · 2009.9~
KINDS = ("ebsi", "bsi")


def _kosis_json(text: str):
    """KOSIS 응답은 키에 따옴표가 없는 경우가 있다(getMeta·검색) → 따옴표를 붙여 읽는다."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(re.sub(r'([{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', text))


def kosis_search(word: str, n: int = 30) -> List[dict]:
    """KOSIS 통계표 검색 — [{org, tbl, name, stat, start, end}]."""
    k = store.key_env("KOSIS_KEY")
    q = {"method": "getList", "apiKey": k, "searchNm": word, "format": "json", "resultCount": str(n)}
    rows = _kosis_json(store.get("https://kosis.kr/openapi/statisticsSearch.do?" + urllib.parse.urlencode(q), timeout=60).decode("utf-8"))
    if not isinstance(rows, list):
        raise RuntimeError(f"KOSIS 검색 응답 이상: {str(rows)[:200]}")
    return [{"org": r.get("ORG_ID"), "tbl": r.get("TBL_ID"), "name": r.get("TBL_NM"), "stat": r.get("STAT_NM"),
             "start": r.get("STRT_PRD_DE"), "end": r.get("END_PRD_DE")} for r in rows]


def _quarter_start(prd: str) -> pd.Timestamp:
    """KOSIS 분기 PRD_DE 'YYYY0q' → 분기 첫날."""
    return pd.Timestamp(year=int(prd[:4]), month=3 * (int(prd[4:6]) - 1) + 1, day=1)


def fetch_ebsi() -> dict:
    org, tbl = EBSI_TABLE
    q = {"method": "getList", "apiKey": store.key_env("KOSIS_KEY"), "itmId": "ALL", "objL1": "ALL", "format": "json", "jsonVD": "Y",
         "prdSe": "Q", "newEstPrdCnt": "200", "orgId": org, "tblId": tbl}       # objL2 를 넣으면 err 21(이 표는 분류 1단)
    raw = store.get("https://kosis.kr/openapi/Param/statisticsParameterData.do?" + urllib.parse.urlencode(q), timeout=120)
    store.save_raw(f"kosis/ebsi/{date.today():%Y%m%d}.json", raw)
    rows = _kosis_json(raw.decode("utf-8"))
    if not isinstance(rows, list):
        raise RuntimeError(f"KOSIS EBSI 응답 이상: {str(rows)[:200]}")
    df = pd.DataFrame([{"period": _quarter_start(r["PRD_DE"]), "code": r["C1"], "name": r["C1_NM"], "value": float(r["DT"])}
                       for r in rows if r.get("DT") not in (None, "", "-")]).sort_values(["code", "period"])
    _write("ebsi", df)
    return {"rows": len(df), "items": int(df.code.nunique()), "first": str(df.period.min().date()), "last": str(df.period.max().date())}


def fetch_bsi() -> dict:
    k = store.key_env("ECOS_KEY")
    end = f"{date.today():%Y%m}"
    recs, raws = [], {}
    for code, n in ledger.nodes("bsi_industry").items():
        u = f"https://ecos.bok.or.kr/api/StatisticSearch/{k}/json/kr/1/1000/{BSI_TABLE}/M/{BSI_START}/{end}/{BSI_CODE}/{code}"
        j = json.loads(store.get(u, timeout=60).decode("utf-8"))
        rows = (j.get("StatisticSearch") or {}).get("row")
        if not rows:
            raise RuntimeError(f"ECOS BSI {code} 응답 이상: {str(j)[:200]}")
        raws[code] = rows
        recs += [{"period": pd.Timestamp(f"{r['TIME'][:4]}-{r['TIME'][4:6]}-01"), "code": code, "name": n["name"], "value": float(r["DATA_VALUE"])}
                 for r in rows if r.get("DATA_VALUE") not in (None, "")]
    store.save_raw(f"ecos/bsi_export/{date.today():%Y%m%d}.json", json.dumps(raws, ensure_ascii=False).encode("utf-8"))
    df = pd.DataFrame(recs).sort_values(["code", "period"])
    _write("bsi", df)
    return {"rows": len(df), "items": int(df.code.nunique()), "first": str(df.period.min().date()), "last": str(df.period.max().date())}


def _write(kind: str, df: pd.DataFrame) -> None:
    d = store.path("derived", "kr_outlook")
    os.makedirs(d, exist_ok=True)
    df.reset_index(drop=True).to_parquet(os.path.join(d, f"{kind}.parquet"))


def load(kind: str) -> Optional[pd.DataFrame]:
    try:
        return pd.read_parquet(store.path("derived", "kr_outlook", f"{kind}.parquet"))
    except Exception:
        return None


def usable_from(kind: str, period: pd.Timestamp) -> pd.Timestamp:
    """그 기간 값을 과거 재현에서 쓸 수 있게 되는 월말(보수적) — EBSI 분기 첫 달 말, BSI 그 달 말."""
    return (pd.Timestamp(period) + pd.offsets.MonthEnd(0)).normalize()


def _rows(df: pd.DataFrame, pred) -> pd.DataFrame:
    """hs4 조건으로 행 고르기 — 빈 표에서 map 결과가 object 형이면 pandas 가 열 이름 목록으로 읽으므로 bool 로 고정."""
    return df.loc[df.hs4.map(pred).astype(bool)] if len(df) else df


def sector_weights(world: pd.DataFrame, year: int, kind: str) -> Dict[str, Dict[str, float]]:
    """업종 → {원천 코드: 그 해 세계 수출액(달러)} — 원장 hs 접두로 업종 HS 를 나눈다('rest' = 명시 품목 뒤 나머지)."""
    w = world[world.month.dt.year == year]
    out = {}
    for s, items in ledger.sector_outlook(kind).items():
        spec = R.SECTOR_HS.get(s)
        if not spec:
            continue
        sub = _rows(w, lambda h, spec=spec: R._hs_in(h, spec))
        tot = float(sub["exp"].sum())
        ws = {}
        for code, _, hs in items:
            if hs != "rest":
                ws[code] = float(_rows(sub, lambda h, hs=hs: any(h.startswith(p) for p in hs))["exp"].sum())
        # 나머지 = 명시 접두의 합집합을 뺀 것 — 분류 개편 전후 품목(예: 플라스틱 ↔ 플라스틱·고무·가죽)이 같은 HS 를 맡아도 두 번 빼지 않는다
        union = sorted({p for _, _, hs in items if hs != "rest" for p in hs})
        explicit = float(_rows(sub, lambda h: any(h.startswith(p) for p in union))["exp"].sum())
        for code, _, hs in items:
            if hs == "rest":
                ws[code] = max(0.0, tot - explicit)
        out[s] = ws
    return out


def sector_index(kind: str, t, values: pd.DataFrame, weights: Dict[str, Dict[str, float]], latest: bool = False) -> Dict[str, dict]:
    """시점 t(월말)의 업종 지수 = 쓸 수 있는 최신 기간에 값이 있는 품목의 가중 평균(가중치 재정규화).
    latest=True 면 공표된 최신 기간(화면용), 아니면 usable_from ≤ t 인 최신 기간(과거 재현용). 반환 업종 → {value, period, prev, parts}."""
    t = pd.Timestamp(t)
    v = values if latest else values[values.period.map(lambda p: usable_from(kind, p) <= t)]
    if v.empty:
        return {}
    names = {c: n for its in ledger.sector_outlook(kind).values() for c, n, _ in its}
    piv = v.pivot_table(index="period", columns="code", values="value").sort_index()
    out = {}
    for s, ws in weights.items():
        cols = [c for c in ws if c in piv.columns and ws[c] > 0]
        if not cols:
            continue
        sub = piv[cols].dropna(how="all")
        if sub.empty:
            continue
        def at(i):
            row = sub.iloc[i].dropna()
            tw = sum(ws[c] for c in row.index)
            return (float(sum(ws[c] * row[c] for c in row.index) / tw), row, tw) if tw > 0 else (None, row, 0)
        val, row, tw = at(-1)
        prev = at(-2)[0] if len(sub) >= 2 else None
        out[s] = {"value": val, "period": str(sub.index[-1].date()), "prev": prev,
                  "parts": [{"code": c, "name": names.get(c, c), "value": float(row[c]), "weight": ws[c] / tw} for c in row.index]}
    return out


def run() -> bool:
    info = {}
    for kind, fn in (("ebsi", fetch_ebsi), ("bsi", fetch_bsi)):
        try:
            info[kind] = fn()
        except Exception as e:   # noqa: BLE001
            info[kind] = {"error": str(e)[:300]}
    os.makedirs(store.path("quality"), exist_ok=True)
    with open(store.path("quality", "kr_outlook_status.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    log(f"[kr_outlook] {info}")
    return all("error" not in v for v in info.values())
