"""판독 엔진 채점(과거 재현) — 매달 그 시점 자료만으로 read_at(t) 를 돌리고 이후 실제 값으로 채점.
정의는 PREREGISTRATION_reading_engine.md (docs/research/20261022_global_topdown). 연구 스크립트 reading_walkforward.py 와 운영(화면)이 같이 쓴다.
"""
from datetime import datetime

import numpy as np
import pandas as pd

from . import reading as R


def mean_nw(v: pd.Series, lag: int):
    v = v.dropna()
    if len(v) < 24:
        return np.nan, np.nan, len(v)
    x = v.values
    m = x.mean()
    e = x - m
    s = (e @ e) / len(x)
    for L in range(1, lag + 1):
        s += 2 * (1 - L / (lag + 1)) * (e[L:] @ e[:-L]) / len(x)
    return m, m / np.sqrt(s / len(x)), len(x)


START, SPLIT = "2008-01-31", pd.Timestamp("2016-12-31")


def me(t, k=0):
    return (pd.Timestamp(t) + pd.offsets.MonthEnd(k)).normalize()


def run_engine(D):
    months = pd.date_range(START, me(datetime.now(), -1), freq="ME")
    cache, out = {}, []
    for t in months:
        out.append(R.read_at(D, t, cache))
    return out


def score_demand(D, hist):
    rows = []
    for r in hist:
        t = pd.Timestamp(r["t"])
        L, T = me(t, -1), me(t, R.H)
        for g, d in r["demand"].items():
            yT, yL, y4 = D.y[g].get(T), D.y[g].get(L), D.y[g].get(me(t, -4))
            if yT is None or pd.isna(yT):
                continue
            act = yT - yL
            win = D.y[g].loc[:L].dropna().iloc[-R.WINDOW:]
            rows.append({"t": t, "g": g, "state": r["links"][g]["state"], "pred": d["delta"], "act": act, "hit": np.sign(d["delta"]) == np.sign(act),
                         "base_mom": np.sign(yL - y4) == np.sign(act) if y4 is not None and not pd.isna(y4) else np.nan,
                         "base_mr": np.sign(win.mean() - yL) == np.sign(act)})
        tr = r.get("total")
        if tr:
            yT, yL, y4 = D.y_total.get(T), D.y_total.get(L), D.y_total.get(me(t, -4))
            if yT is not None and not pd.isna(yT) and yL is not None and not pd.isna(yL):
                act = yT - yL
                win = D.y_total.loc[:L].dropna().iloc[-R.WINDOW:]
                rows.append({"t": t, "g": "한국 총수출", "state": "-", "pred": tr["delta"], "act": act, "hit": np.sign(tr["delta"]) == np.sign(act),
                             "base_mom": np.sign(yL - y4) == np.sign(act), "base_mr": np.sign(win.mean() - yL) == np.sign(act)})
    return pd.DataFrame(rows)


def score_comp(D, hist):
    rows = []
    for r in hist:
        t = pd.Timestamp(r["t"])
        L2, T = me(t, -2), me(t, 10)
        for cmd, c in r["competition"].items():
            for nm, code in (("kr", "5800"), ("cn", "5700")):
                if nm not in c or c[nm]["state"] == "유지":
                    continue
                sh = D.us12[(cmd, code)] / D.us12[(cmd, "-")] * 100
                a, b = sh.get(T), sh.get(L2)
                if a is None or pd.isna(a):
                    continue
                rows.append({"t": t, "cmd": cmd, "who": nm, "state": c[nm]["state"], "hit": np.sign(a - b) == np.sign(c[nm]["delta"])})
    return pd.DataFrame(rows)


SECTOR_COLS = ["t", "s", "score", "score_D", "D", "C", "out", "hit", "hit_D", "has_C"]


def score_sectors(D, hist):
    """업종 판독 채점. 반환: (행 = 월 × 업종, IC DataFrame[composite, D] = 월별 업종 간 순위상관).
    composite = 사전등록 점수 z(D)+z(C), D = R5-D 수요 충격만(z(D); 옛 이력엔 score_D 가 없어 D 로 대신 — 순위는 같다)."""
    rows, ics = [], {}
    k = D.close["U001"]
    for r in hist:
        t = pd.Timestamp(r["t"])
        if not r.get("sectors") or t not in D.close.index:
            continue
        T = me(t, R.H)
        if T not in D.close.index:
            continue
        rk = np.log(k[T] / k[t])
        sub = []
        for s, v in r["sectors"].items():
            o = np.log(D.close.loc[T, s] / D.close.loc[t, s]) - rk
            if pd.isna(o):
                continue
            sd = v["score_D"] if v.get("score_D") is not None else v["D"]
            sub.append((s, v["score"], sd, o))
            rows.append({"t": t, "s": s, "score": v["score"], "score_D": sd, "D": v["D"], "C": v["C"], "out": o,
                         "hit": (o > 0) == (v["score"] > 0) if v["score"] != 0 else np.nan,
                         "hit_D": (o > 0) == (sd > 0) if sd != 0 else np.nan, "has_C": v["C"] is not None})
        if len(sub) >= 5:
            d = pd.DataFrame(sub, columns=["s", "score", "score_D", "out"])
            ics[t] = {"composite": d.score.rank().corr(d.out.rank()), "D": d.score_D.rank().corr(d.out.rank())}
    return pd.DataFrame(rows, columns=SECTOR_COLS), pd.DataFrame.from_dict(ics, orient="index", dtype=float).reindex(columns=["composite", "D"])


def summarize(res_d, res_c, res_s, ic):
    out = {}
    if len(res_d):
        for g in ["한국 총수출"] + [x for x in R.PARTNERS if x in set(res_d.g)]:
            d = res_d[res_d.g == g]
            if len(d) < 12:
                continue
            rec = {"n": len(d)}
            for per, m in (("all", slice(None)), ("early", d.t <= SPLIT), ("late", d.t > SPLIT)):
                dd = d[m]
                if len(dd):
                    rec[per] = {"n": len(dd), "hit": float(dd.hit.mean()), "base_mom": float(dd.base_mom.dropna().mean()) if dd.base_mom.notna().any() else None,
                                "base_mr": float(dd.base_mr.mean()), "corr": float(dd.pred.corr(dd.act)) if len(dd) > 5 else None}
            out.setdefault("demand", {})[g] = rec
        pooled = res_d[res_d.g != "한국 총수출"]
        out["demand_pooled"] = {per: {"n": len(dd), "hit": float(dd.hit.mean()), "base_mom": float(dd.base_mom.dropna().mean()), "base_mr": float(dd.base_mr.mean())}
                                for per, dd in (("all", pooled), ("early", pooled[pooled.t <= SPLIT]), ("late", pooled[pooled.t > SPLIT])) if len(dd)}
        out["demand_by_state"] = {st: {"n": int((pooled.state == st).sum()), "hit": float(pooled[pooled.state == st].hit.mean())} for st in ("강함", "약함") if (pooled.state == st).any()}
    if len(res_c):
        out["competition"] = {per: {"n": len(dd), "hit": float(dd.hit.mean())} for per, dd in (("all", res_c), ("early", res_c[res_c.t <= SPLIT]), ("late", res_c[res_c.t > SPLIT])) if len(dd)}
        out["competition_by"] = {f"{w}·{st}": {"n": int(((res_c.who == w) & (res_c.state == st)).sum()), "hit": float(res_c[(res_c.who == w) & (res_c.state == st)].hit.mean())}
                                 for w in ("kr", "cn") for st in ("확대", "잠식") if ((res_c.who == w) & (res_c.state == st)).any()}
    if len(res_s):
        out["sectors"] = {}
        for per, m_ic, m_s in (("all", ic.index == ic.index, res_s.t == res_s.t), ("early", ic.index <= SPLIT, res_s.t <= SPLIT), ("late", ic.index > SPLIT, res_s.t > SPLIT)):
            icm, t, n = mean_nw(ic["composite"][m_ic], R.H)
            ss = res_s[m_s]
            out["sectors"][per] = {"ic": icm, "t": t, "n_months": n, "hit": float(ss.hit.dropna().mean()) if len(ss) else None,
                                   "base_up": float((ss.out > 0).mean()) if len(ss) else None}
            # 기술 보충(판정 아님): 성분별 IC, 상위 2 − 하위 2 업종 6개월 차. 수요 충격만(score_D)은 R5-D 의 과거 재현(사후 선택).
            ic_D, t_D, _ = mean_nw(ic["D"][m_ic], R.H)
            g = ss.dropna(subset=["C"]).groupby("t")
            ic_c = pd.Series({t_: d["C"].rank().corr(d.out.rank()) for t_, d in g if len(d) >= 5}, dtype=float)
            ic_C, t_C, _ = mean_nw(ic_c, R.H)
            sp = _spread(ss, "score")
            spD = _spread(ss, "score_D")
            out["sectors"][per].update(ic_D=ic_D, t_D=t_D, ic_C=ic_C, t_C=t_C,
                                       spread_top2=float(sp.mean()) if len(sp) else None, spread_hit=float((sp > 0).mean()) if len(sp) else None,
                                       hit_D=float(ss.hit_D.dropna().mean()) if len(ss) and ss.hit_D.notna().any() else None,
                                       spread_top2_D=float(spD.mean()) if len(spD) else None)
    return out


def _spread(ss: pd.DataFrame, col: str) -> pd.Series:
    """월별 상위 2 − 하위 2 업종의 6개월 초과수익 차."""
    return pd.Series({t_: d.nlargest(2, col).out.mean() - d.nsmallest(2, col).out.mean() for t_, d in ss.groupby("t") if len(d) >= 5}, dtype=float)


def forward(res_s: pd.DataFrame, ic: pd.DataFrame, n_readings: int = 0) -> dict:
    """R5-D 전진 확인(PREREGISTRATION_reading_engine_D.md). 입력은 전진 기록(forward_log)으로 만든 채점 행·IC 만.
    상태: 대기(채점 가능한 달 0) → 확인 중 n/24 → 24개월에 1차 판정(확정·기각·보류) → 보류면 36개월에 최종(확정·기각)."""
    out = {"from": str(R.FORWARD_FROM.date()), "min_months": R.FORWARD_MIN_MONTHS, "max_months": R.FORWARD_MAX_MONTHS,
           "n_readings": int(n_readings), "n_months": 0, "status": "대기", "ic_D": None, "t_D": None, "ic_composite": None, "t_composite": None,
           "spread_top2_D": None, "hit_D": None, "first_scorable": str(me(R.FORWARD_FROM, R.H + 1).date())}
    if res_s is None or not len(res_s):
        return out
    ss = res_s[res_s.t >= R.FORWARD_FROM]
    icf = ic[ic.index >= R.FORWARD_FROM] if ic is not None and len(ic) else pd.DataFrame(columns=["composite", "D"])
    n = int(len(icf))
    out["n_months"] = n
    if n == 0:
        return out
    def _m(v):
        v = v.dropna()
        return (float(v.mean()) if len(v) else None, float(mean_nw(v, R.H)[1]) if len(v) >= 24 else None)
    out["ic_D"], out["t_D"] = _m(icf["D"])
    out["ic_composite"], out["t_composite"] = _m(icf["composite"])
    spD = _spread(ss, "score_D")
    out["spread_top2_D"] = float(spD.mean()) if len(spD) else None
    out["hit_D"] = float(ss.hit_D.dropna().mean()) if ss.hit_D.notna().any() else None
    if n < R.FORWARD_MIN_MONTHS:
        out["status"] = f"확인 중 {n}/{R.FORWARD_MIN_MONTHS}"
        return out
    icD, icC, sp = out["ic_D"] or 0.0, out["ic_composite"] or 0.0, out["spread_top2_D"] or 0.0
    if icD <= 0:
        out["status"] = "기각"
    elif sp > 0 and icD >= icC:
        out["status"] = "확정"
    elif n < R.FORWARD_MAX_MONTHS:
        out["status"] = f"보류 {n}/{R.FORWARD_MAX_MONTHS}"
    else:
        out["status"] = "확정" if sp > 0 else "기각"
    return out


def verdicts(S):
    v = {}
    dp = S.get("demand_pooled", {})
    def fund(rec):
        if not rec or "early" not in rec or "late" not in rec:
            return "판정 불가"
        ok = [rec[p]["hit"] >= max(0.5, rec[p]["base_mom"] or 0) + 0.05 and rec[p]["hit"] >= 0.5 + 0.05 for p in ("early", "late")]
        half = [rec[p]["hit"] > 0.5 for p in ("early", "late")]
        return "판독 유효" if all(ok) and all(half) else "부분 유효" if any(ok) and all(half) else "무효"
    v["R2 수요(상대국 풀링)"] = fund(dp)
    v["R3 총수출"] = fund(S.get("demand", {}).get("한국 총수출"))
    c = S.get("competition", {})
    v["R4 경쟁 지속"] = ("유효" if all(c.get(p, {}).get("hit", 0) >= 0.6 for p in ("early", "late")) else "무효") if c else "판정 불가"
    s = S.get("sectors", {})
    if s and "early" in s and "late" in s:
        pos = all((s[p]["ic"] or 0) > 0 for p in ("early", "late"))
        v["R5 업종(가격)"] = "실전 후보" if pos and (s["all"]["ic"] or 0) > 0 and (s["all"]["t"] or 0) >= 2 else "작게 실험" if pos else "버림"
    else:
        v["R5 업종(가격)"] = "판정 불가(품목 자료 대기)"
    f = S.get("forward")
    if f:
        v["R5-D 수요 충격만(전진 확인)"] = f["status"]
    return v


