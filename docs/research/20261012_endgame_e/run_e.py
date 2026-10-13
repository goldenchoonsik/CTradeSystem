"""E — 투자자별·프로그램 수급 판정 (PREREGISTRATION.md 그대로). 개발 합격 규칙만 봉인 구간을 1회 연다.

  .venv/bin/python docs/research/20261012_endgame_e/run_e.py
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "docs", "research", "20261011_vol_spike"))
import compare_algos  # noqa: E402
from evaluate_events import tick_size  # noqa: E402

STAGE = compare_algos.STAGE
FLOWS = os.path.join(STAGE, "raw", "kis", "flows")
DEV_END, SEAL_FROM, SEAL_END = pd.Timestamp("2026-05-29"), pd.Timestamp("2026-06-01"), pd.Timestamp("2026-10-08")
RT, TOPK = 0.0027, 20
HERE = os.path.dirname(__file__)


def load_flows():
    """원본 응답 → (date, code) 별 순매수대금(원). 같은 날이 여러 응답에 있으면 가장 늦게 받은 것."""
    cache = os.path.join(STAGE, "derived", "flows_panel.parquet")      # 저장소 밖
    if os.path.exists(cache):
        return pd.read_parquet(cache)
    recs = []
    for kind, key, cols, unit in (("investor", "output2", ["frgn_ntby_tr_pbmn", "orgn_ntby_tr_pbmn", "prsn_ntby_tr_pbmn"], 1e6),
                                  ("program", "output", ["whol_smtn_ntby_tr_pbmn"], 1.0)):
        for fp in glob.glob(os.path.join(FLOWS, kind, "*", "*.json")):
            j = json.load(open(fp, encoding="utf-8"))
            for row in j["response"].get(key) or []:
                if not row.get("stck_bsop_date"):
                    continue
                r = {"date": row["stck_bsop_date"], "code": "A" + j["code"], "kind": kind, "fetched": j["fetched_at"]}
                for c in cols:
                    r[c] = float(row.get(c) or 0) * unit
                recs.append(r)
    d = pd.DataFrame(recs)
    d["date"] = pd.to_datetime(d.date, format="%Y%m%d")
    out = None
    for kind in ("investor", "program"):
        x = d[d.kind == kind].sort_values("fetched").drop_duplicates(["date", "code"], keep="last").drop(columns=["kind", "fetched"])
        x = x.dropna(axis=1, how="all")
        out = x if out is None else out.merge(x, on=["date", "code"], how="outer")
    out = out.rename(columns={"frgn_ntby_tr_pbmn": "frgn", "orgn_ntby_tr_pbmn": "orgn", "prsn_ntby_tr_pbmn": "prsn",
                              "whol_smtn_ntby_tr_pbmn": "prog"})
    out.to_parquet(cache)
    return out


def panel():
    compare_algos.END = SEAL_END
    px = compare_algos.load_daily().sort_values(["code", "date"])
    g = px.groupby("code", sort=False)
    px["val20"] = g["val"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    o1 = g["o"].shift(-1)
    px["fwd1"] = g["o"].shift(-2) / o1 - 1
    px["fwd5"] = g["o"].shift(-6) / o1 - 1
    px["entry_raw"] = px.raw_close * (o1 / px.c)
    f = load_flows()
    d = px.merge(f, on=["date", "code"], how="left")
    for c in ("frgn", "orgn", "prsn", "prog"):
        d[f"i_{c}"] = d[c] / d.val20
    d["i_fo"] = (d.frgn + d.orgn) / d.val20
    d.loc[~((d.frgn > 0) & (d.orgn > 0)), "i_fo"] = np.nan
    d["elig"] = (d.raw_close >= 1000) & (d.val20 >= 5e8) & (d.v > 0)
    return d[d.elig].reset_index(drop=True)


SIGNALS = {"E1 외국인 순매수 상위": ("i_frgn", False), "E2 기관 순매수 상위": ("i_orgn", False),
           "E3 개인 순매수 하위": ("i_prsn", True), "E4 프로그램 순매수 상위": ("i_prog", False),
           "E5 외국인·기관 동반 순매수 상위": ("i_fo", False)}


def cost(w_prev, w, price):
    dw = w.sub(w_prev, fill_value=0).abs()
    p = price.reindex(dw.index).fillna(1e9).to_numpy()
    return float((dw.to_numpy() * (RT / 2 + tick_size(p) / p)).sum())


def run(d, col, asc, h):
    fwd = f"fwd{h}"
    days = sorted(d.date.unique())[::h]
    rows, wb_prev, ws_prev = [], pd.Series(dtype=float), pd.Series(dtype=float)
    for day in days:
        x = d[(d.date == day)].dropna(subset=[fwd]).set_index("code")
        if len(x) < 100:
            continue
        s = x[col].dropna()
        if len(s) < TOPK:
            continue
        pick = s.sort_values(ascending=asc).index[:TOPK]
        wb, ws = pd.Series(1 / len(x), index=x.index), pd.Series(1 / TOPK, index=pick)
        cb, cs = cost(wb_prev, wb, x.entry_raw), cost(ws_prev, ws, x.entry_raw)
        rb, rs = x[fwd].mean() - cb, x.loc[pick, fwd].mean() - cs
        rows.append(dict(date=day, strat=rs, bench=rb, excess=rs - rb, gross=x.loc[pick, fwd].mean()))
        wb_prev, ws_prev = wb, ws
    return pd.DataFrame(rows).set_index("date")


def tstat(x):
    return x.mean() / x.std() * np.sqrt(len(x)) if len(x) > 2 else np.nan


def main():
    d = panel()
    cover = d.groupby("date").i_frgn.apply(lambda s: s.notna().mean())
    lines = ["# E 수급 판정 결과", "",
             f"대상 하루 평균 {d.groupby('date').size().mean():.0f} 종목, 수급 커버리지 평균 {cover.mean():.1%} (최저 {cover.min():.1%}), "
             f"구간 {d.date.min():%Y-%m-%d} ~ {d.date.max():%Y-%m-%d}", "",
             "| 규칙 | 보유 | 회 | 비용 후 평균 | t | 기준 대비 초과 | 초과 t | 분기 양수 | 최근12M 합 | 봉인 | 판정 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    passed = 0
    for lab, (col, asc) in SIGNALS.items():
        for h in (1, 5):
            dev = run(d[d.date <= DEV_END], col, asc, h)
            t, te = tstat(dev.strat), tstat(dev.excess)
            qpos = (dev.strat.groupby(dev.index.to_period("Q")).sum() > 0).mean()
            last12 = dev.strat[dev.index > DEV_END - pd.Timedelta(days=365)].sum()
            ok_dev = dev.strat.mean() > 0 and t > 3 and dev.excess.mean() > 0 and te > 2 and qpos >= 2 / 3 and last12 > 0
            seal_txt, ok = "열지 않음", False
            if ok_dev:
                s = run(d[(d.date >= SEAL_FROM) & (d.date <= SEAL_END)], col, asc, h)
                ok = s.strat.mean() > 0 and tstat(s.strat) > 2
                seal_txt = f"{s.strat.mean()*100:+.3f}% t{tstat(s.strat):+.2f} (초과 {s.excess.mean()*100:+.3f}%, {len(s)}회)"
            passed += ok
            lines.append(f"| {lab} | {h}일 | {len(dev)} | {dev.strat.mean()*100:+.3f}% | {t:+.2f} | {dev.excess.mean()*100:+.3f}% | {te:+.2f} | "
                         f"{qpos:.0%} | {last12*100:+.2f}% | {seal_txt} | {'합격' if ok else '불합격'} |")
    # 참고: 일별 순위 IC (개발)
    lines += ["", "참고 — 강도와 다음 수익의 일별 순위 IC (개발 구간, 판정 아님)", "", "| 강도 | IC 1일 | t | IC 5일 | t |", "|---|---|---|---|---|"]
    dv = d[d.date <= DEV_END]
    for c in ("i_frgn", "i_orgn", "i_prsn", "i_prog"):
        res = []
        for h in (1, 5):
            x = dv.dropna(subset=[c, f"fwd{h}"])
            ic = x.groupby("date").apply(lambda g: g[c].rank().corr(g[f"fwd{h}"].rank()))
            if h == 5:
                ic = ic.iloc[::5]
            res += [ic.mean(), tstat(ic)]
        lines.append(f"| {c} | {res[0]:+.4f} | {res[1]:+.2f} | {res[2]:+.4f} | {res[3]:+.2f} |")
    lines += ["", f"합격 {passed} / 10"]
    out = "\n".join(lines)
    print(out)
    with open(os.path.join(HERE, "results_e.md"), "w", encoding="utf-8") as f:
        f.write(out + "\n")


if __name__ == "__main__":
    main()
