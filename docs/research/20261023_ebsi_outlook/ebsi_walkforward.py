"""사전등록 E(PREREGISTRATION_ebsi.md) 과거 재현 — EBSI 업종 지수가 업종 주가(E1)·업종 수출(E2)을 앞서나.
참고(판정 아님): 한국은행 수출전망 BSI 로 같은 채점, E1 지평 곡선, '수요 충격만 + EBSI' 합성.

실행: .venv/bin/python docs/research/20261023_ebsi_outlook/ebsi_walkforward.py  → results_ebsi.json
"""
import json
import os

import numpy as np
import pandas as pd

from shared.data_library.global_macro import kr_outlook as O
from shared.data_library.global_macro import reading as R
from shared.data_library.global_macro import store
from shared.data_library.global_macro.reading_eval import SPLIT, me, mean_nw

HERE = os.path.dirname(os.path.abspath(__file__))
START = pd.Timestamp("2009-04-30")
WORLD = pd.read_parquet(store.path("derived", "trade", "kr_exp_item_world.parquet"))
CLOSE = pd.read_parquet(store.path("derived", "kr_industry", "sector_close.parquet"))
K = CLOSE["U001"]


def scores(kind: str) -> pd.DataFrame:
    """월말 t × 업종 → 그 시점에 쓸 수 있던 업종 지수(보수적 시점 규칙)."""
    vals, wcache, rows = O.load(kind), {}, []
    last = vals.period.max() + pd.offsets.MonthEnd(3)
    for t in pd.date_range(START, last, freq="ME"):
        yr = (t - pd.offsets.MonthEnd(1)).year - 1
        if yr not in wcache:
            wcache[yr] = O.sector_weights(WORLD, yr, kind)
        for s, x in O.sector_index(kind, t, vals, wcache[yr]).items():
            rows.append({"t": t, "s": s, "x": x["value"] - 100.0, "period": x["period"]})
    return pd.DataFrame(rows)


def zscore(d: pd.DataFrame, col: str) -> pd.Series:
    sd = d[col].std()
    return (d[col] - d[col].mean()) / sd if sd and sd > 0 else d[col] * 0


def e1(sc: pd.DataFrame, h: int = R.H, col: str = "x") -> dict:
    """업종 주가: 업종 간 z 점수 vs h 개월 초과수익(코스피 대비) — R5 채점과 같은 방식."""
    ics, rows = {}, []
    for t, d in sc.groupby("t"):
        T = me(t, h)
        if t not in CLOSE.index or T not in CLOSE.index:
            continue
        d = d[d.s.isin(CLOSE.columns)].copy()
        d["out"] = [np.log(CLOSE.loc[T, s] / CLOSE.loc[t, s]) - np.log(K[T] / K[t]) for s in d.s]
        d = d.dropna(subset=["out", col])
        if len(d) < 5:
            continue
        d["z"] = zscore(d, col)
        ics[t] = d.z.rank().corr(d.out.rank())
        rows.append(d.assign(t=t))
    if not rows:
        return {}
    ic, res = pd.Series(ics, dtype=float), pd.concat(rows)
    out = {}
    for per, m_ic, m_r in (("all", ic.index == ic.index, res.t == res.t), ("early", ic.index <= SPLIT, res.t <= SPLIT), ("late", ic.index > SPLIT, res.t > SPLIT)):
        icm, tt, n = mean_nw(ic[m_ic], h)
        rr = res[m_r]
        sp = pd.Series({t_: g.nlargest(2, "z").out.mean() - g.nsmallest(2, "z").out.mean() for t_, g in rr.groupby("t") if len(g) >= 5}, dtype=float)
        hit = ((rr.z > 0) == (rr.out > 0))[rr.z != 0]
        out[per] = {"ic": None if pd.isna(icm) else float(icm), "t": None if pd.isna(tt) else float(tt), "n_months": int(n),
                    "spread_top2": float(sp.mean()) if len(sp) else None, "hit": float(hit.mean()) if len(hit) else None,
                    "base_up": float((rr.out > 0).mean()) if len(rr) else None}
    return out


def verdict_e1(r: dict) -> str:
    if not r or any((r.get(p) or {}).get("ic") is None for p in ("early", "late", "all")):
        return "판정 불가"
    pos = r["early"]["ic"] > 0 and r["late"]["ic"] > 0
    return "실전 후보" if pos and (r["all"]["t"] or 0) >= 2 else "작게 실험" if pos else "버림"


def sector_growth() -> pd.DataFrame:
    """월말 × 업종 → y = 최근 3개월 합의 전년 동기 대비 로그 증가율(업종 세계 수출)."""
    out = {}
    for s, spec in R.SECTOR_HS.items():
        sub = WORLD.loc[WORLD.hs4.map(lambda h, spec=spec: R._hs_in(h, spec)).astype(bool)]
        m = sub.groupby("month")["exp"].sum().sort_index()
        s3 = m.rolling(3, min_periods=3).sum()
        out[s] = np.log(s3 / s3.shift(12))
    return pd.DataFrame(out)


def e2(sc: pd.DataFrame, Y: pd.DataFrame) -> dict:
    rows = []
    for _, r in sc.iterrows():
        t, s = r.t, r.s
        if s not in Y or r.x == 0:
            continue
        y = Y[s]
        y0, y3, ym3 = y.get(t), y.get(me(t, 3)), y.get(me(t, -3))
        if any(v is None or pd.isna(v) for v in (y0, y3)):
            continue
        win = y.loc[:t].dropna().iloc[-60:]
        act = np.sign(y3 - y0)
        if act == 0:
            continue
        rows.append({"t": t, "s": s, "hit": np.sign(r.x) == act,
                     "mr": (np.sign(win.mean() - y0) == act) if len(win) >= 36 else np.nan,
                     "mom": (np.sign(y0 - ym3) == act) if ym3 is not None and not pd.isna(ym3) else np.nan})
    d = pd.DataFrame(rows)
    for c in ("hit", "mr", "mom"):        # NaN 섞인 object 열의 numpy bool 평균은 논리합이 돼 1/n 이 나온다 → float 로
        d[c] = d[c].astype(float)
    out = {}
    for per, m in (("all", d.t == d.t), ("early", d.t <= SPLIT), ("late", d.t > SPLIT)):
        dd = d[m]
        out[per] = {"n": len(dd), "hit": float(dd.hit.mean()), "base_mr": float(dd.mr.dropna().mean()), "base_mom": float(dd.mom.dropna().mean())}
    out["by_sector"] = {R.SECTOR_HS[s]["name"]: {"n": len(g), "hit": float(g.hit.mean()), "base_mr": float(g.mr.dropna().mean())} for s, g in d.groupby("s")}
    return out


def verdict_e2(r: dict) -> str:
    ok = [r[p]["hit"] >= max(0.5, r[p]["base_mr"], r[p]["base_mom"]) + 0.05 for p in ("early", "late")]
    return "판독 유효" if all(ok) else "부분 유효" if any(ok) else "무효"


def with_demand(sc: pd.DataFrame) -> pd.DataFrame:
    """참고: R5-D 수요 충격(D)과 EBSI 의 합성 z — 판독 이력(reading/history.parquet)의 D 를 쓴다."""
    h = pd.read_parquet(store.path("reading", "history.parquet"))
    h = h[h.kind == "sector"][["t", "key", "delta"]].rename(columns={"key": "s", "delta": "D"})
    h["t"] = pd.to_datetime(h.t)
    m = sc.merge(h, on=["t", "s"], how="inner")
    parts = []
    for t, d in m.groupby("t"):
        d = d.copy()
        d["combo"] = zscore(d, "D") + zscore(d, "x")
        d["Donly"] = d["D"]
        parts.append(d)
    return pd.concat(parts)


def main():
    Y = sector_growth()
    res = {"preregistration_sha256": open(os.path.join(HERE, "PREREGISTRATION_ebsi.sha256")).read().split()[0]}
    for kind in O.KINDS:
        sc = scores(kind)
        r1, r2 = e1(sc), e2(sc, Y)
        res[kind] = {"E1": r1, "E1_verdict": verdict_e1(r1), "E2": r2, "E2_verdict": verdict_e2(r2),
                     "months": [str(sc.t.min().date()), str(sc.t.max().date())], "sectors": int(sc.s.nunique()),
                     "horizon_ic": {h: (e1(sc, h)["all"] or {}).get("ic") for h in range(1, 13)}}
        if kind == "ebsi":
            wd = with_demand(sc)
            res[kind]["ref_combo_D_plus_EBSI"] = e1(wd, col="combo")
            res[kind]["ref_D_only_same_sectors"] = e1(wd, col="Donly")
    with open(os.path.join(HERE, "results_ebsi.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    print(json.dumps({k: {kk: res[k][kk] for kk in ("E1_verdict", "E2_verdict", "months", "sectors")} for k in O.KINDS}, ensure_ascii=False))


if __name__ == "__main__":
    main()
