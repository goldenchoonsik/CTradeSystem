"""판독의 지평 곡선(국소투영) — PREREGISTRATION_horizon.md(2026-10-22, sha 434f3100…) 정의 그대로.

같은 판독(2008~ 매달 재현)을 지평 h=1..12 마다 누적 결과로 채점해 정보가 어느 지평에 남는지 잰다.
운영 채점 지평(reading.H=6)·전진 트랙 D·CE 는 이 결과로 바꾸지 않는다(사전등록 규칙).
"""
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import reading as R
from . import reading_eval as E

HORIZONS: List[int] = list(range(1, 13))
T_MIN, MIN_RUN = 2.0, 2                      # 통과 문턱·연속 지평 수
TARGET = (3, 12)                             # 목표 지평
PERIODS = ("all", "early", "late")


def _per(t: pd.Series) -> Dict[str, pd.Series]:
    return {"all": t == t, "early": t <= E.SPLIT, "late": t > E.SPLIT}


def demand_curve(D, hist: list, horizons: List[int] = HORIZONS, total: bool = False) -> pd.DataFrame:
    """R2(상대국, 활성 연결만) 또는 R3(총수출) 의 지평별 적중 vs 평균 회귀 기준선. 행 = 지평 × 구간."""
    rows = []
    for r in hist:
        t = pd.Timestamp(r["t"])
        L = E.me(t, -1)
        items = [("한국 총수출", r.get("total"), D.y_total)] if total else [(g, d, D.y[g]) for g, d in r.get("demand", {}).items()]
        for g, d, y in items:
            if not d:
                continue
            yL = y.get(L)
            if yL is None or pd.isna(yL):
                continue
            win = y.loc[:L].dropna().iloc[-R.WINDOW:]
            mr = np.sign(win.mean() - yL)
            for h in horizons:
                yT = y.get(E.me(t, h))
                if yT is None or pd.isna(yT):
                    continue
                act = np.sign(yT - yL)
                rows.append({"t": t, "g": g, "h": h, "hit": float(np.sign(d["delta"]) == act), "mr": float(mr == act)})
    df = pd.DataFrame(rows, columns=["t", "g", "h", "hit", "mr"])
    out = []
    for h in horizons:
        m = df[df.h == h].groupby("t")[["hit", "mr"]].mean()          # 월별 상대국 평균 → 월 시계열
        m["diff"] = m.hit - m.mr
        for per, mask in _per(m.index.to_series()).items():
            mm = m[mask.values]
            dm, td, n = E.mean_nw(mm["diff"], h)
            hm, th, _ = E.mean_nw(mm["hit"] - 0.5, h)
            out.append({"h": h, "per": per, "n_months": int(n), "hit": float(mm.hit.mean()) if len(mm) else np.nan,
                        "mr": float(mm.mr.mean()) if len(mm) else np.nan, "diff": dm, "t": td, "t_hit": th})
    return pd.DataFrame(out)


def sector_curve(D, hist: list, horizons: List[int] = HORIZONS) -> pd.DataFrame:
    """R5 업종 판독의 지평별 IC·상위2−하위2(누적) + 한계 IC(t+h−1→t+h). 점수 composite(주)·D(보충). 행 = 지평 × 점수 × 구간."""
    k = D.close["U001"]
    ics: Dict[int, dict] = {h: {} for h in horizons}
    for r in hist:
        t = pd.Timestamp(r["t"])
        if not r.get("sectors") or t not in D.close.index:
            continue
        for h in horizons:
            T, T1 = E.me(t, h), E.me(t, h - 1)
            if T not in D.close.index or T1 not in D.close.index:
                continue
            rec = []
            for s, v in r["sectors"].items():
                if s not in D.close.columns:
                    continue
                o = np.log(D.close.loc[T, s] / D.close.loc[t, s]) - np.log(k[T] / k[t])
                om = np.log(D.close.loc[T, s] / D.close.loc[T1, s]) - np.log(k[T] / k[T1])
                if pd.isna(o) or pd.isna(om):
                    continue
                sd = v["score_D"] if v.get("score_D") is not None else v["D"]
                rec.append((s, v["score"], sd, o, om))
            if len(rec) < 5:
                continue
            d = pd.DataFrame(rec, columns=["s", "composite", "D", "out", "out_m"])
            ics[h][t] = {}
            for col in ("composite", "D"):
                ics[h][t][f"ic_{col}"] = d[col].rank().corr(d.out.rank())
                ics[h][t][f"icm_{col}"] = d[col].rank().corr(d.out_m.rank())
                ics[h][t][f"sp_{col}"] = d.nlargest(2, col).out.mean() - d.nsmallest(2, col).out.mean()
    out = []
    for h in horizons:
        m = pd.DataFrame.from_dict(ics[h], orient="index", dtype=float).sort_index()
        for col in ("composite", "D"):
            for per, mask in _per(m.index.to_series()).items():
                mm = m[mask.values] if len(m) else m
                ic, t_, n = E.mean_nw(mm[f"ic_{col}"], h) if len(mm) else (np.nan, np.nan, 0)
                icm, tm, _ = E.mean_nw(mm[f"icm_{col}"], 1) if len(mm) else (np.nan, np.nan, 0)
                out.append({"h": h, "score": col, "per": per, "n_months": int(n), "ic": ic, "t": t_,
                            "spread": float(mm[f"sp_{col}"].mean()) if len(mm) else np.nan, "ic_marginal": icm, "t_marginal": tm})
    return pd.DataFrame(out)


def _runs(passed: List[int]) -> List[List[int]]:
    runs, cur = [], []
    for h in sorted(passed):
        if cur and h == cur[-1] + 1:
            cur.append(h)
        else:
            if cur:
                runs.append(cur)
            cur = [h]
    if cur:
        runs.append(cur)
    return runs


def judge(curve: pd.DataFrame, value: str, horizons: List[int] = HORIZONS) -> dict:
    """사전등록 판정: 통과(전체 구간 value>0 & t≥2) → 2개 이상 연속 구간 → (a) 목표 지평 정합 / (b) 단기 정보 / (c) 없음. 전후반 부호 불일치면 '불안정'."""
    a = curve[curve.per == "all"].set_index("h")
    passed = [h for h in horizons if h in a.index and a.loc[h, value] > 0 and a.loc[h, "t"] >= T_MIN]
    runs = [r for r in _runs(passed) if len(r) >= MIN_RUN]
    best = max(runs, key=len) if runs else []
    if not best:
        return {"passed": passed, "run": [], "verdict": "지평 정보 없음", "h_star": None, "stable": None}
    tgt = [h for h in best if TARGET[0] <= h <= TARGET[1]]
    if tgt:
        h_star = max(tgt, key=lambda h: a.loc[h, value])
        verdict = "목표 지평 정합"
    else:
        h_star, verdict = max(best, key=lambda h: a.loc[h, value]), "단기 정보"
    e = curve[(curve.per == "early") & (curve.h == h_star)][value]
    l = curve[(curve.per == "late") & (curve.h == h_star)][value]
    stable = bool(len(e) and len(l) and np.sign(e.iloc[0]) == np.sign(l.iloc[0]) and e.iloc[0] > 0)
    return {"passed": passed, "run": best, "verdict": verdict + ("" if stable else " (불안정: 전후반 부호 다름)"), "h_star": int(h_star), "stable": stable}


def _rows(df: pd.DataFrame) -> list:
    return [{k: (None if isinstance(v, float) and np.isnan(v) else (float(v) if isinstance(v, (np.floating, float)) else (int(v) if isinstance(v, (np.integer,)) else v)))
             for k, v in r.items()} for r in df.to_dict("records")]


def curves(D, hist: list, horizons: List[int] = HORIZONS) -> dict:
    """화면·결과 파일용 묶음: 곡선 표(행 목록)와 판정."""
    dem = demand_curve(D, hist, horizons)
    tot = demand_curve(D, hist, horizons, total=True)
    sec = sector_curve(D, hist, horizons)
    out = {"horizons": horizons, "demand": _rows(dem), "total": _rows(tot), "sectors": _rows(sec),
           "judgment": {"R2 수요": judge(dem, "diff", horizons), "R3 총수출": judge(tot, "diff", horizons),
                        "R5 합성(주)": judge(sec[sec.score == "composite"], "ic", horizons), "R5-D 수요만(보충)": judge(sec[sec.score == "D"], "ic", horizons)},
           "rule": f"통과 = 전체 구간 값>0 & NW t≥{T_MIN:g}; 유효 구간 = {MIN_RUN}개 이상 연속; 목표 지평 {TARGET[0]}~{TARGET[1]}개월. 운영 채점 지평 6개월·전진 트랙은 불변."}
    return out
