"""사전등록 L(PREREGISTRATION_leaders.md) — 업종 판독(합성 점수) → 업종 대장주(그 시점 코스피 시총 상위 2) 6개월 초과수익.

  PYTHONPATH=. .venv/bin/python docs/research/20261023_sector_leaders/leaders_test.py  → results_leaders.json
"""
import json
import os

import numpy as np
import pandas as pd

from shared.data_library.global_macro import kr_industry, stocks, store
from shared.data_library.global_macro import reading as R
from shared.data_library.global_macro.reading_eval import SPLIT, mean_nw
from shared.data_library.paths import data_vault_path

HERE = os.path.dirname(os.path.abspath(__file__))
START, H = pd.Timestamp("2013-03-31"), R.H


def month_end_mcap(codes) -> pd.DataFrame:
    g = pd.read_parquet(data_vault_path("staging", "kr_equity", "derived", "gold_1d.parquet"), columns=["code", "date", "market_cap"],
                        filters=[("code", "in", list(codes))])
    g["t"] = pd.to_datetime(g.date).dt.to_period("M").dt.to_timestamp(how="end").dt.normalize()
    return g.sort_values("date").groupby(["t", "code"]).market_cap.last().unstack("code")


def ic_stats(rows: pd.DataFrame, x: str, y: str) -> dict:
    ics = {t: g[x].rank().corr(g[y].rank()) for t, g in rows.groupby("t") if g[[x, y]].dropna().shape[0] >= 5}
    ic = pd.Series(ics, dtype=float)
    out = {}
    for per, m_ic, m_r in (("all", ic.index == ic.index, rows.t == rows.t), ("early", ic.index <= SPLIT, rows.t <= SPLIT), ("late", ic.index > SPLIT, rows.t > SPLIT)):
        icm, tt, n = mean_nw(ic[m_ic], H)
        rr = rows[m_r]
        sp = pd.Series({t: g.nlargest(2, x)[y].mean() - g.nsmallest(2, x)[y].mean() for t, g in rr.groupby("t") if len(g) >= 5}, dtype=float)
        out[per] = {"ic": None if pd.isna(icm) else float(icm), "t": None if pd.isna(tt) else float(tt), "n_months": int(n),
                    "spread_top2": float(sp.mean()) if len(sp) else None, "spread_hit": float((sp > 0).mean()) if len(sp) else None}
    return out


def verdict(r: dict) -> str:
    if any((r.get(p) or {}).get("ic") is None for p in ("all", "early", "late")):
        return "판정 불가"
    pos = r["early"]["ic"] > 0 and r["late"]["ic"] > 0
    return "실전 후보" if pos and (r["all"]["t"] or 0) >= 2 else "작게 실험" if pos else "버림"


def main():
    ks = pd.read_parquet(store.path("derived", "kr_industry", "company_ksic.parquet"))
    ks = ks[ks.sector.isin(R.SECTOR_HS) & (ks.market == "KOSPI")]
    sm = stocks.monthly_close()
    ks = ks[ks.stock_code.isin(sm.columns)].set_index("stock_code")
    mc = month_end_mcap(ks.index)
    sc = pd.read_parquet(store.path("derived", "kr_industry", "sector_close.parquet"))
    mk = sc[kr_industry.MARKET]
    hist = pd.read_parquet(store.path("reading", "history.parquet"))
    sec = hist[hist.kind == "sector"].copy()
    sec["t"] = pd.to_datetime(sec.t)
    score = sec.pivot(index="t", columns="key", values="rho")
    dsh = sec.pivot(index="t", columns="key", values="delta")
    rows, picks = [], {}
    for t in [t for t in sm.index if t >= START and t in score.index]:
        T = t + pd.offsets.MonthEnd(H)
        if T not in sm.index or T not in sc.index or t not in mc.index:
            continue
        mk6 = np.log(mk[T] / mk[t])
        for s in R.SECTOR_HS:
            if s not in score.columns or pd.isna(score.loc[t, s]) or s not in sc.columns:
                continue
            cs = [c for c in ks.index[ks.sector == s] if c in mc.columns]
            cap = mc.loc[t, cs].dropna().sort_values(ascending=False)
            r6 = np.log(sm.loc[T, cap.index] / sm.loc[t, cap.index]).dropna()
            ranked = [c for c in cap.index if c in r6.index]               # 시총 순, t+6 종가 있는 것만
            if len(ranked) < 3:
                continue
            sec6 = float(np.log(sc.loc[T, s] / sc.loc[t, s]) - mk6)
            rec = {"t": t, "s": s, "score": float(score.loc[t, s]), "D": float(dsh.loc[t, s]), "sector_ex": sec6}
            for n in (1, 2, 3):
                rec[f"lead{n}_ex"] = float(r6[ranked[:n]].mean() - mk6)
            rec["lead2_vs_sector"] = rec["lead2_ex"] - sec6
            rows.append(rec)
            if t == max(score.index[score.index <= sm.index.max() - pd.offsets.MonthEnd(H)]):
                picks[R.SECTOR_HS[s]["name"]] = [ks.loc[c, "name"] for c in ranked[:2]]
    df = pd.DataFrame(rows)
    res = {"prereg_sha256": open(os.path.join(HERE, "PREREGISTRATION_leaders.sha256")).read().split()[0],
           "months": [str(df.t.min().date()), str(df.t.max().date())], "n_rows": len(df), "last_picks": picks,
           "L1": ic_stats(df, "score", "lead2_ex")}
    res["L1_verdict"] = verdict(res["L1"])
    res["ref"] = {"sector_index_same_period": ic_stats(df, "score", "sector_ex"),
                  "D_leaders2": ic_stats(df, "D", "lead2_ex"), "D_sector_index": ic_stats(df, "D", "sector_ex"),
                  "leaders1": ic_stats(df, "score", "lead1_ex"), "leaders3": ic_stats(df, "score", "lead3_ex"),
                  "lead2_minus_sector_mean": {per: float(d.lead2_vs_sector.mean()) for per, d in (("all", df), ("early", df[df.t <= SPLIT]), ("late", df[df.t > SPLIT]))},
                  "lead2_corr_with_sector": float(df.lead2_ex.corr(df.sector_ex))}
    # 대장주 이력(시점마다 누가 대장주였나 — 업종별 상위 2 가 바뀐 횟수)
    with open(os.path.join(HERE, "results_leaders.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    print(json.dumps({"L1_verdict": res["L1_verdict"], "months": res["months"], "n": res["n_rows"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
