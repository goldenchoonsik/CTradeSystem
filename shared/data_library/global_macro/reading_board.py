"""시스템 판독 화면용 — 매달 판독 엔진을 2008~ 현재까지 그 시점 자료로 재현하고, 최신 판독(근거 사슬)·과거 성적·연결 상태 이력을 쓴다.

  derived/dashboard/reading.json   화면 '🧭 시스템 판독' 탭이 읽음
  reading/history.parquet          월 × 상대국·업종 판독 이력(매번 재계산)
  reading/forward_log.jsonl        FORWARD_FROM 이후 판독을 처음 생성된 그대로 보관(append-only) — R5-D 전진 확인은 이것만 채점
"""
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

from . import reading as R
from . import reading_eval as E
from . import reading_ext as X
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
        out["sectors"].append({"s": s, "name": v["name"], "reading": v["reading"], "score": v["score"], "D": v["D"], "C": v["C"], "text": txt,
                               "score_D": v.get("score_D"), "reading_D": v.get("reading_D"), "score_CE": v.get("score_CE"), "reading_CE": v.get("reading_CE"), "C_EU": v.get("C_EU")})
    from . import us_imports
    for cmd, c in latest.get("competition", {}).items():
        out["competition"].append({"cmd": cmd, "name": us_imports.PRODUCTS.get(cmd, cmd), "kr": c.get("kr"), "cn": c.get("cn"), "month": c.get("month")})
    out["competition_eu"] = [{"cmd": cmd, "name": us_imports.PRODUCTS.get(cmd, cmd), "kr": c.get("kr"), "cn": c.get("cn"), "month": c.get("month")}
                             for cmd, c in latest.get("competition_eu", {}).items()]
    return out


def forward_log_path() -> str:
    return store.path("reading", "forward_log.jsonl")


def forward_log_read() -> list:
    fp = forward_log_path()
    if not os.path.exists(fp):
        return []
    with open(fp, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def forward_log_append(hist: list) -> int:
    """FORWARD_FROM 이후 판독 중 아직 없는 달을 처음 생성된 그대로 덧붙인다(수정·덮어쓰기 없음). 반환 = 추가한 건수."""
    have = {e["t"] for e in forward_log_read()}
    new = 0
    os.makedirs(os.path.dirname(forward_log_path()), exist_ok=True)
    with open(forward_log_path(), "a", encoding="utf-8") as f:
        for r in hist:
            if pd.Timestamp(r["t"]) < R.FORWARD_FROM or r["t"] in have or not r.get("sectors"):
                continue
            keep = {s: {k: v.get(k) for k in ("name", "score", "score_D", "D", "C", "reading", "reading_D", "score_CE", "C_EU", "reading_CE")} for s, v in r["sectors"].items()}
            ev = {"t": r["t"], "logged_at": datetime.now().isoformat(timespec="seconds"),
                  "demand": {g: {"call": d["call"], "delta": d["delta"]} for g, d in r.get("demand", {}).items()},
                  "total": (r.get("total") or {}).get("call"), "sectors": keep}
            f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
            have.add(r["t"])
            new += 1
    return new


def forward_status(D) -> dict:
    """전진 기록만으로 트랙별 채점 — 로그에 적힌 점수를 그대로 쓰고, 이후 실제 업종 수익으로만 채점한다. {'D': …, 'CE': …}"""
    entries = forward_log_read()
    res_f, ic_f = E.score_sectors(D, [{"t": e["t"], "sectors": e["sectors"]} for e in entries])
    n_ce = sum(1 for e in entries if any(v.get("score_CE") is not None for v in e["sectors"].values()))
    return {"D": E.forward(res_f, ic_f, n_readings=len(entries), track="D"), "CE": E.forward(res_f, ic_f, n_readings=n_ce, track="CE")}


def build() -> dict:
    D = R.Data()
    hist = E.run_engine(D)
    res_d, res_c = E.score_demand(D, hist), E.score_comp(D, hist)
    res_s, ic = E.score_sectors(D, hist)
    S = E.summarize(res_d, res_c, res_s, ic)
    eu12 = X.load12("eu_imports")
    for r in hist:                                           # R5-CE 필드는 고정일 이후 판독에만(원안 이력은 그대로)
        if pd.Timestamp(r["t"]) >= R.FORWARD_FROM:
            X.attach_ce(r, eu12)
    n_new = forward_log_append(hist)
    fw = forward_status(D)
    S["forward"], S["forward_ce"] = fw["D"], fw["CE"]
    S["forward"]["new_this_run"] = n_new
    V = E.verdicts(S)
    latest = hist[-1]
    L = pd.Timestamp(latest["t"]) - pd.offsets.MonthEnd(1)
    expo = R.sector_exposure(D, L.year - 1) if D.items is not None else {}     # read_at 과 같은 해(t−1 월의 전년)
    if "competition_eu" not in latest:                                             # 최신 판독이 고정일 이전이면 표시용으로만 EU 경쟁을 붙인다(기록 아님)
        X.attach_ce(latest, eu12)
    # 상시 건강 지표: 최근 60개월(채점 가능한 것) 엔진 vs 평균 회귀 적중
    health = None
    if len(res_d):
        pooled = res_d[res_d.g != "한국 총수출"]
        recent = pooled[pooled.t > pooled.t.max() - pd.DateOffset(months=60)]
        health = {"from": str(recent.t.min().date()), "to": str(recent.t.max().date()), "n": len(recent),
                  "engine": float(recent.hit.mean()), "mean_reversion": float(recent.base_mr.mean()), "trend": float(recent.base_mom.dropna().mean())}
    # 연결 상태 이력(월 × 상대국)
    links_hist = {g: [[r["t"], r["links"][g]["state"], round(r["links"][g]["rho"], 3)] for r in hist if g in r["links"]] for g in R.PARTNERS}
    sec_hist = {s: [[r["t"], round(r["sectors"][s]["score"], 3), round(r["sectors"][s]["score_D"], 3)] for r in hist if s in r.get("sectors", {})] for s in R.SECTOR_HS}
    # 관계 그래프 탭용: 상대국별 수요 판독 이력(월 × 가속/감속), 업종 수출 노출(연도별 — 판독 t 는 t−1 월의 전년 비중을 씀)
    demand_hist = {g: [[r["t"], r["demand"][g]["call"], round(r["demand"][g]["delta"], 4)] for r in hist if g in r.get("demand", {})] for g in R.PARTNERS}
    expo_by_year = {}
    if D.items is not None:
        years = sorted({(pd.Timestamp(r["t"]) - pd.offsets.MonthEnd(1)).year - 1 for r in hist})
        for yr in years:
            ex = R.sector_exposure(D, yr)
            if ex:
                expo_by_year[str(yr)] = {s: {g: round(w, 4) for g, w in v.items() if w > 0} for s, v in ex.items()}
    try:                                                                       # L3 종목 후보(현재 파악) — 업종 대응·가격 자료 없으면 비움
        from . import stocks as _st
        stock_cands = _st.candidates(latest.get("sectors") or {})
    except Exception as e:   # noqa: BLE001
        store.log(f"[reading] 종목 후보 실패(무시): {e}")
        stock_cands = {}
    out = {"generated_at": datetime.now().isoformat(timespec="seconds"), "t": latest["t"], "verdicts": V, "summary": S, "health": health,
           "forward": S["forward"], "forward_ce": S["forward_ce"], "stocks": stock_cands,
           "evidence": evidence(D, latest, expo), "links_history": links_hist, "sector_history": sec_hist,
           "demand_history": demand_hist, "exposure_by_year": expo_by_year,
           "has_items": D.items is not None, "note": "판독 = 그 시점 자료만으로 최근 5년 관계를 다시 재서 읽음. 성적 = 같은 방식으로 2008~ 매달 재현한 결과."}
    recs = []
    for r in hist:
        for g, lk in r["links"].items():
            d = r["demand"].get(g, {})
            recs.append({"t": r["t"], "kind": "link", "key": g, "state": lk["state"], "rho": lk["rho"], "k": lk["k"], "call": d.get("call"), "delta": d.get("delta")})
        for s, v in r.get("sectors", {}).items():
            recs.append({"t": r["t"], "kind": "sector", "key": s, "state": v["reading"], "rho": v["score"], "k": None, "call": v["reading_D"], "delta": v["D"]})
    os.makedirs(store.path("reading"), exist_ok=True)
    pd.DataFrame(recs).to_parquet(store.path("reading", "history.parquet"))
    from .dashboard import _clean
    os.makedirs(store.path("derived", "dashboard"), exist_ok=True)
    fp = store.path("derived", "dashboard", "reading.json")
    with open(fp + ".tmp", "w", encoding="utf-8") as f:
        json.dump(_clean(out), f, ensure_ascii=False, allow_nan=False, default=str)
    os.replace(fp + ".tmp", fp)
    refresh_stage1(_clean(out))
    store.log(f"[reading] {latest['t']} 판독 · 판정 {V} · 전진 기록 +{n_new}")
    return out


def refresh_stage1(reading: dict) -> bool:
    """화면은 stage1.json 안의 'reading' 을 읽는다 — '시스템 판독' 단계만 돌려도 화면이 바뀌도록 그 부분만 바꿔 넣는다."""
    fp = store.path("derived", "dashboard", "stage1.json")
    if not os.path.exists(fp):
        return False
    with open(fp, encoding="utf-8") as f:
        d = json.load(f)
    d["reading"] = reading
    with open(fp + ".tmp", "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, allow_nan=False, default=str)
    os.replace(fp + ".tmp", fp)
    return True
