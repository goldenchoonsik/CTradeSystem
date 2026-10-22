"""한국 품목 × 국가별 수출(관세청 공공데이터, OPEN_DATA_KEY) — 업종의 상대국 노출·품목별 수출 추세 판독용.

  nitemtrade(품목별 국가별): HS 2자리 장을 넣으면 그 아래 HS4 행이 국가·월별로 옴(1회 1년, 국가 1개)
  Itemtrade(품목별): 국가 구분 없는 세계 합계 — 상대국 비중의 분모
  raw/customs_nitem/<장>/<국가>_<연도>.xml · raw/customs_item/<장>/<연도>.xml  (지난 해는 한 번 받으면 유지, 올해만 다시)
  derived/trade/kr_exp_item.parquet        장형: month·country·hs4·exp(달러)
  derived/trade/kr_exp_item_world.parquet  장형: month·hs4·exp
"""
import os
import re
import time
from datetime import date
from typing import Dict, List

import pandas as pd

from . import store
from .store import log

BASE = "https://apis.data.go.kr/1220000/"
CHAPTERS: Dict[str, str] = {"27": "광물성 연료(정유)", "29": "유기화학", "30": "의약품", "33": "화장품", "39": "플라스틱", "40": "고무·타이어",
                            "72": "철강", "73": "철강제품", "84": "기계·컴퓨터", "85": "전기·전자(반도체·배터리·휴대폰)", "87": "자동차", "89": "선박",
                            "90": "광학·정밀(디스플레이)"}
COUNTRIES: List[str] = ["CN", "HK", "US", "JP", "VN", "TW", "DE", "IN", "MX", "SG", "MY", "ID", "TH", "PH", "AU", "CA", "NL", "GB", "FR", "IT",
                        "PL", "TR", "BR", "SA", "AE", "RU"]
START_YEAR = 2006          # 판독 엔진 재현이 2008~ 이고 노출 가중치는 전년 자료 → 2006 부터면 충분(요청 수 절약; 일 한도 주의)
SLEEP = 0.05
WORKERS = 4                # 동시 요청 수(공공데이터포털 개발계정 한도 안에서)


def _get(ep: str, params: dict) -> str:
    from urllib.parse import urlencode
    q = dict(params, serviceKey=store.key_env("OPEN_DATA_KEY"))
    raw = store.get(BASE + ep + "?" + urlencode(q), timeout=120).decode("utf-8")
    code = re.search(r"<resultCode>(\d+)</resultCode>", raw)
    if not code or code.group(1) != "00":
        raise RuntimeError(raw[:200])
    return raw


def parse_items(xml: str, with_country: bool) -> List[dict]:
    out = []
    for it in re.findall(r"<item>(.*?)</item>", xml, re.S):
        d = dict(re.findall(r"<(\w+)>([^<]*)</\1>", it))
        y = d.get("year", "")
        if not re.match(r"^\d{4}\.\d{2}$", y):                 # '총계' 행 제외
            continue
        hs = (d.get("hsCd") or d.get("hsCode") or "")[:4]
        if not hs.isdigit():
            continue
        rec = {"month": pd.Timestamp(y.replace(".", "-") + "-01") + pd.offsets.MonthEnd(0), "hs4": hs, "exp": float(d.get("expDlr") or 0)}
        if with_country:
            rec["country"] = d.get("statCd")
        out.append(rec)
    return out


def _cached(rel: str, fetch) -> str:
    fp = store.path("raw", rel)
    this_year = str(date.today().year)
    if os.path.exists(fp) and this_year not in os.path.basename(fp):
        return open(fp, encoding="utf-8").read()
    xml = fetch()
    store.save_raw(rel, xml.encode("utf-8"))
    time.sleep(SLEEP)
    return xml


def fetch(years=None) -> dict:
    from concurrent.futures import ThreadPoolExecutor
    years = years or list(range(START_YEAR, date.today().year + 1))
    fail, n = [], 0

    def one(job):
        kind, ch, y, c = job
        p = {"strtYymm": f"{y}01", "endYymm": f"{y}12", "hsSgn": ch}
        try:
            if kind == "item":
                _cached(f"customs_item/{ch}/{y}.xml", lambda: _get("Itemtrade/getItemtradeList", p))
            else:
                _cached(f"customs_nitem/{ch}/{c}_{y}.xml", lambda: _get("nitemtrade/getNitemtradeList", dict(p, cntyCd=c)))
            return None
        except Exception as e:   # noqa: BLE001
            return f"{kind} {ch} {c or ''} {y} {str(e)[:60]}"

    for ch in CHAPTERS:
        jobs = [("item", ch, y, None) for y in years] + [("nitem", ch, y, c) for y in years for c in COUNTRIES]
        with ThreadPoolExecutor(WORKERS) as ex:
            errs = [e for e in ex.map(one, jobs) if e]
        fail += errs
        n += len(jobs)
        log(f"[kr_trade_items] {ch}장 완료 (누적 {n}, 실패 {len(fail)})")
    return {"requests": n, "failed": fail[:20], "n_failed": len(fail)}


def build() -> dict:
    rows, world = [], []
    for ch in CHAPTERS:
        d = store.path("raw", "customs_nitem", ch)
        for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            rows += parse_items(open(os.path.join(d, fn), encoding="utf-8").read(), True)
        d2 = store.path("raw", "customs_item", ch)
        for fn in sorted(os.listdir(d2)) if os.path.isdir(d2) else []:
            world += parse_items(open(os.path.join(d2, fn), encoding="utf-8").read(), False)
    a = pd.DataFrame(rows).groupby(["month", "country", "hs4"], as_index=False)["exp"].sum()
    w = pd.DataFrame(world).groupby(["month", "hs4"], as_index=False)["exp"].sum()
    os.makedirs(store.path("derived", "trade"), exist_ok=True)
    a.to_parquet(store.path("derived", "trade", "kr_exp_item.parquet"))
    w.to_parquet(store.path("derived", "trade", "kr_exp_item_world.parquet"))
    return {"rows": len(a), "world_rows": len(w), "months": [str(a.month.min().date()), str(a.month.max().date())]}


def run() -> bool:
    info = fetch()
    info.update(build())
    log(f"[kr_trade_items] {info}")
    return info["n_failed"] == 0
