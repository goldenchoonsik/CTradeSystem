"""5단계 — 종목 단계 L3 검정 S1·S2·S3. PREREGISTRATION_stage5_stocks.md 그대로.

  .venv/bin/python -I docs/research/20261022_global_topdown/stage5_stocks_test.py
출력: results_stage5_stocks.md·json, signals/registry_stage5.json
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../..")))
from shared.data_library.global_macro import kr_industry, reading as R, reading_eval as E, stocks, store  # noqa: E402
from shared.data_library.paths import data_vault_path  # noqa: E402

START, SPLIT, H = pd.Timestamp("2013-03-31"), E.SPLIT, R.H


def month_end_fund(codes) -> pd.DataFrame:
    """gold_1d 의 월말(마지막 거래일) 시총·거래대금."""
    g = pd.read_parquet(data_vault_path("staging", "kr_equity", "derived", "gold_1d.parquet"), columns=["code", "date", "market_cap", "adv_20"],
                        filters=[("code", "in", list(codes))])
    g["m"] = pd.to_datetime(g.date).dt.to_period("M")
    f = g.sort_values("date").groupby(["code", "m"]).last()
    return f


def main():
    ks = pd.read_parquet(store.path("derived", "kr_industry", "company_ksic.parquet"))
    ks = ks[ks.sector.isin(R.SECTOR_HS)]
    sm = stocks.monthly_close()
    codes = [c for c in ks.stock_code if c in sm.columns]
    ks = ks[ks.stock_code.isin(codes)].set_index("stock_code")
    sc = pd.read_parquet(store.path("derived", "kr_industry", "sector_close.parquet"))
    hist = pd.read_parquet(store.path("reading", "history.parquet"))
    sec = hist[hist.kind == "sector"].copy()
    sec["t"] = pd.to_datetime(sec.t)
    score = sec.pivot(index="t", columns="key", values="rho")          # 합성 점수
    score_D = sec.pivot(index="t", columns="key", values="delta")      # 수요 충격 D(순위는 score_D 와 같음)
    fund = month_end_fund(codes)
    mk = sc[kr_industry.MARKET]
    months = [t for t in sm.index if START <= t <= sm.index.max() - pd.offsets.MonthEnd(H) and t in score.index]
    rows = []
    for t in months:
        T = t + pd.offsets.MonthEnd(H)
        if T not in sm.index or T not in sc.index:
            continue
        per = t.to_period("M")
        r6 = np.log(sm.loc[T] / sm.loc[t])
        mk6 = np.log(mk[T] / mk[t])
        betas = {}
        for s in R.SECTOR_HS:
            cs = [c for c in ks.index[ks.sector == s] if c in sm.columns]
            if cs and s in sc:
                betas[s] = stocks.sector_beta(sm[cs], sc[s], mk, t)
        for c in codes:
            s = ks.loc[c, "sector"]
            if s not in betas or pd.isna(r6.get(c)) or s not in score.columns or pd.isna(score.loc[t, s]):
                continue
            try:
                f = fund.loc[(c, per)]
            except KeyError:
                continue
            if pd.isna(f.market_cap) or f.market_cap < stocks.MIN_MCAP_KRW or pd.isna(f.adv_20) or f.adv_20 < stocks.MIN_ADV_KRW:
                continue
            b = betas[s].get(c)
            s6 = np.log(sc.loc[T, s] / sc.loc[t, s])
            rows.append({"t": t, "code": c, "sector": s, "score": float(score.loc[t, s]), "score_D": float(score_D.loc[t, s]), "beta": None if pd.isna(b) else float(b),
                         "ex_mkt": float(r6[c] - mk6), "ex_sec": float(r6[c] - s6)})
    df = pd.DataFrame(rows)
    out = {"generated_at": datetime.now().isoformat(timespec="seconds"), "prereg_sha256": open(os.path.join(HERE, "PREREGISTRATION_stage5_stocks.sha256")).read().split()[0],
           "n_rows": int(len(df)), "n_stocks": int(df.code.nunique()), "months": [str(df.t.min().date()), str(df.t.max().date())]}

    def ic_series(d, x, y, within=False):
        ics = {}
        for t_, g in d.groupby("t"):
            g = g.dropna(subset=[x, y])
            if within:
                g = g.assign(**{x: g[x] - g.groupby("sector")[x].transform("mean"), y: g[y] - g.groupby("sector")[y].transform("mean")})
            if len(g) >= 30:
                ics[t_] = g[x].rank().corr(g[y].rank())
        return pd.Series(ics, dtype=float)

    def summ(ic, extra=None):
        o = {}
        for per, m in (("all", ic.index == ic.index), ("early", ic.index <= SPLIT), ("late", ic.index > SPLIT)):
            icm, t, n = E.mean_nw(ic[m], H)
            o[per] = {"ic": None if pd.isna(icm) else float(icm), "t": None if pd.isna(t) else float(t), "n_months": int(n)}
            if extra is not None:
                for k, v in extra(per).items():
                    o[per][k] = v
        return o

    res = {}
    for tag, col in (("S1", "score"), ("S3_1", "score_D")):
        def ex1(per, col=col):
            d = df[(df.t <= SPLIT) if per == "early" else (df.t > SPLIT) if per == "late" else (df.t == df.t)]
            fav, un = d[d[col] > 0].ex_mkt, d[d[col] < 0].ex_mkt
            return {"spread": float(fav.mean() - un.mean()) if len(fav) and len(un) else None, "base_up": float((d.ex_mkt > 0).mean()) if len(d) else None,
                    "hit": float(((d[col] > 0) == (d.ex_mkt > 0))[d[col] != 0].mean()) if len(d) else None}
        res[tag] = summ(ic_series(df, col, "ex_mkt"), ex1)
    for tag, col in (("S2", "score"), ("S3_2", "score_D")):
        d2 = df.dropna(subset=["beta"]).copy()
        d2["inter"] = d2[col] * d2.beta
        def ex2(per, col=col):
            d = d2[(d2.t <= SPLIT) if per == "early" else (d2.t > SPLIT) if per == "late" else (d2.t == d2.t)]
            o = {"base_up": float((d.ex_sec > 0).mean()) if len(d) else None}
            for nm, m in (("fav", d[col] > 0), ("unfav", d[col] < 0)):
                g = d[m]
                if len(g) < 100:
                    o[f"beta_spread_{nm}"] = None
                    continue
                q = g.groupby("t").beta.transform(lambda v: pd.qcut(v.rank(method="first"), 3, labels=False) if len(v) >= 9 else np.nan)
                hi, lo = g[q == 2].ex_sec, g[q == 0].ex_sec
                o[f"beta_spread_{nm}"] = float(hi.mean() - lo.mean()) if len(hi) and len(lo) else None
            return o
        res[tag] = summ(ic_series(d2, "inter", "ex_sec", within=True), ex2)
    V = {k: ("실전 후보" if all((res[k][p]["ic"] or 0) > 0 for p in ("early", "late")) and (res[k]["all"]["t"] or 0) >= 2
             else "작게 실험" if all((res[k][p]["ic"] or 0) > 0 for p in ("early", "late")) else "버림") for k in ("S1", "S2")}
    out.update(results=res, verdicts=V)
    json.dump(out, open(os.path.join(HERE, "results_stage5_stocks.json"), "w"), ensure_ascii=False, indent=1, default=str)
    # 신호 원장
    def sig(id_, name, s, v):
        f = lambda p: {"effect": s[p]["ic"], "t": s[p]["t"], "spread": s[p].get("spread") if "spread" in s[p] else s[p].get("beta_spread_fav"), "hit": s[p].get("hit")}
        return {"id": id_, "family": "업종 판독 → 종목(가격)", "name": name, "verdict": v, "horizon": "6개월", "periods": ["~2016", "2017~"], "dev": f("early"), "conf": f("late"),
                "current": {"date": str(df.t.max().date()), "text": f"종목 {out['n_stocks']}개, 업종 7"}}
    reg = {"stage": 5, "generated_at": out["generated_at"], "prereg_sha256": out["prereg_sha256"], "source": "docs/research/20261022_global_topdown/stage5_stocks_test.py",
           "periods": ["~2016", "2017~"], "signals": [sig("S1", "업종 판독 점수 → 종목 초과수익(코스피 대비)", res["S1"], V["S1"]),
                                                      sig("S2", "업종 점수 × 업종 베타 → 종목 초과수익(업종 대비, 업종 내)", res["S2"], V["S2"])]}
    json.dump(reg, open(store.path("signals", "registry_stage5.json"), "w"), ensure_ascii=False, indent=1, default=str)
    report(out)


def report(out):
    res, V = out["results"], out["verdicts"]
    f3 = lambda x: "–" if x is None else f"{x:+.3f}"
    f1 = lambda x: "–" if x is None else f"{x:+.1f}"
    pp = lambda x: "–" if x is None else f"{x * 100:+.1f}%p"
    pc = lambda x: "–" if x is None else f"{x * 100:.0f}%"
    L = ["# 5단계 — 종목 단계 L3 (업종 판독 → 종목)", "", f"사전등록 sha256 {out['prereg_sha256'][:12]}…, {out['months'][0]}~{out['months'][1]}, 종목 {out['n_stocks']}·행 {out['n_rows']}, 계산 {out['generated_at']}",
         "생존 편향(현재 상장사·오늘 분류)·수출 노출 대신 업종 베타(대리) — 사전등록에 명시.", "", "## 판정", "", f"- **S1 업종 판독 → 종목**: {V['S1']}", f"- **S2 업종 내 전이(점수 × 베타)**: {V['S2']}", "",
         "## S1·S3-1 업종 점수 → 종목 초과수익(코스피 대비)", "", "| 점수 | 구간 | IC | NW t | 개월 | 우호−비우호 업종 종목 6개월 차 | 적중 | 종목>코스피 비율 |", "|---|---|---|---|---|---|---|---|"]
    for k, nm in (("S1", "합성 점수"), ("S3_1", "수요 충격만(참고)")):
        for per, pn in (("all", "전체"), ("early", "~2016"), ("late", "2017~")):
            v = res[k][per]
            L.append(f"| {nm} | {pn} | {f3(v['ic'])} | {f1(v['t'])} | {v['n_months']} | {pp(v.get('spread'))} | {pc(v.get('hit'))} | {pc(v.get('base_up'))} |")
    L += ["", "## S2·S3-2 업종 내: 점수 × 업종 베타 → 종목 초과수익(업종 지수 대비)", "", "| 점수 | 구간 | IC(업종 내) | NW t | 개월 | 우호 업종 β상위−하위 | 비우호 업종 β상위−하위 | 종목>업종 비율 |", "|---|---|---|---|---|---|---|---|"]
    for k, nm in (("S2", "합성 점수"), ("S3_2", "수요 충격만(참고)")):
        for per, pn in (("all", "전체"), ("early", "~2016"), ("late", "2017~")):
            v = res[k][per]
            L.append(f"| {nm} | {pn} | {f3(v['ic'])} | {f1(v['t'])} | {v['n_months']} | {pp(v.get('beta_spread_fav'))} | {pp(v.get('beta_spread_unfav'))} | {pc(v.get('base_up'))} |")
    open(os.path.join(HERE, "results_stage5_stocks.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
