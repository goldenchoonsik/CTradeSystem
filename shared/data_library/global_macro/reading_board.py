"""시스템 판독 화면용 — 매달 판독 엔진을 2008~ 현재까지 그 시점 자료로 재현하고, 최신 판독(근거 사슬)·과거 성적·연결 상태 이력을 쓴다.

  derived/dashboard/reading.json   화면 '🧭 시스템 판독' 탭이 읽음
  reading/history.parquet          월 × 상대국·업종 판독 이력
"""
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

from . import reading as R
from . import reading_eval as E
from . import store


def _pct(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)


def evidence(D, latest: dict, expo: dict) -> dict:
    """최신 판독을 사람이 읽을 근거 문장으로."""
    out = {"links": [], "total": None, "sectors": [], "competition": []}
    for g, lk in latest["links"].items():
        d = latest["demand"].get(g)
        txt = f"연결 {lk['state']} — 최근 5년 '{g} 경기 → 한국 대{g} 수출' 상관 {lk['rho']:.2f}, {lk['k']}개월 선행"
        if d:
            txt += (f". {g} 선행지수 6개월 변화 {d['x_used']:+.2f}({d['x_month'][:7]}) → {d['target'][:7]} 수출 전년비 예측 {np.expm1(d['yhat']) * 100:+.0f}%"
                    f"(현재 {np.expm1(d['ylast']) * 100:+.0f}%) → {d['call']}")
        else:
            txt += ". 연결이 끊겨 수요 판독 안 함"
        out["links"].append({"g": g, "state": lk["state"], "rho": lk["rho"], "k": lk["k"], "call": d["call"] if d else None,
                             "delta": d["delta"] if d else None, "text": txt})
    tr = latest.get("total")
    if tr:
        out["total"] = {"call": tr["call"], "delta": tr["delta"], "text": f"활성 상대국 판독을 전년 수출 비중으로 합산 → 한국 총수출 전년비 예측 변화 {tr['delta']:+.2f}(로그) → {tr['call']}"}
    for s, v in sorted(latest.get("sectors", {}).items(), key=lambda kv: -kv[1]["score"]):
        ex = expo.get(s, {})
        top = sorted(ex.items(), key=lambda kv: -kv[1])[:3]
        txt = f"수출 노출 상위: " + ", ".join(f"{g} {w * 100:.0f}%" for g, w in top)
        txt += f" · 수요 충격 {v['D']:+.3f}"
        txt += f" · 미국 시장 한국 점유 변화 {v['C']:+.2f}%p" if v["C"] is not None else " · 미국 점유 자료 없음"
        out["sectors"].append({"s": s, "name": v["name"], "reading": v["reading"], "score": v["score"], "D": v["D"], "C": v["C"], "text": txt})
    from . import us_imports
    for cmd, c in latest.get("competition", {}).items():
        out["competition"].append({"cmd": cmd, "name": us_imports.PRODUCTS.get(cmd, cmd), "kr": c.get("kr"), "cn": c.get("cn"), "month": c.get("month")})
    return out


def build() -> dict:
    D = R.Data()
    hist = E.run_engine(D)
    res_d, res_c = E.score_demand(D, hist), E.score_comp(D, hist)
    res_s, ic = E.score_sectors(D, hist)
    S = E.summarize(res_d, res_c, res_s, ic)
    V = E.verdicts(S)
    latest = hist[-1]
    L = pd.Timestamp(latest["t"]) - pd.offsets.MonthEnd(1)
    expo = R.sector_exposure(D, L.year - 1) if D.items is not None else {}     # read_at 과 같은 해(t−1 월의 전년)
    # 상시 건강 지표: 최근 60개월(채점 가능한 것) 엔진 vs 평균 회귀 적중
    health = None
    if len(res_d):
        pooled = res_d[res_d.g != "한국 총수출"]
        recent = pooled[pooled.t > pooled.t.max() - pd.DateOffset(months=60)]
        health = {"from": str(recent.t.min().date()), "to": str(recent.t.max().date()), "n": len(recent),
                  "engine": float(recent.hit.mean()), "mean_reversion": float(recent.base_mr.mean()), "trend": float(recent.base_mom.dropna().mean())}
    # 연결 상태 이력(월 × 상대국)
    links_hist = {g: [[r["t"], r["links"][g]["state"], round(r["links"][g]["rho"], 3)] for r in hist if g in r["links"]] for g in R.PARTNERS}
    sec_hist = {s: [[r["t"], round(r["sectors"][s]["score"], 3)] for r in hist if s in r.get("sectors", {})] for s in R.SECTOR_HS}
    out = {"generated_at": datetime.now().isoformat(timespec="seconds"), "t": latest["t"], "verdicts": V, "summary": S, "health": health,
           "evidence": evidence(D, latest, expo), "links_history": links_hist, "sector_history": sec_hist,
           "has_items": D.items is not None, "note": "판독 = 그 시점 자료만으로 최근 5년 관계를 다시 재서 읽음. 성적 = 같은 방식으로 2008~ 매달 재현한 결과."}
    recs = []
    for r in hist:
        for g, lk in r["links"].items():
            d = r["demand"].get(g, {})
            recs.append({"t": r["t"], "kind": "link", "key": g, "state": lk["state"], "rho": lk["rho"], "k": lk["k"], "call": d.get("call"), "delta": d.get("delta")})
        for s, v in r.get("sectors", {}).items():
            recs.append({"t": r["t"], "kind": "sector", "key": s, "state": v["reading"], "rho": v["score"], "k": None, "call": None, "delta": v["D"]})
    os.makedirs(store.path("reading"), exist_ok=True)
    pd.DataFrame(recs).to_parquet(store.path("reading", "history.parquet"))
    from .dashboard import _clean
    os.makedirs(store.path("derived", "dashboard"), exist_ok=True)
    fp = store.path("derived", "dashboard", "reading.json")
    with open(fp + ".tmp", "w", encoding="utf-8") as f:
        json.dump(_clean(out), f, ensure_ascii=False, allow_nan=False, default=str)
    os.replace(fp + ".tmp", fp)
    store.log(f"[reading] {latest['t']} 판독 · 판정 {V}")
    return out
