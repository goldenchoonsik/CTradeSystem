"""코스피 상위 50 · 성장 지표(매출 성장·가속·성장 대비 PER·3개월 모멘텀) — PREREGISTRATION_growth.md 그대로.

실행: .venv/bin/python -I docs/research/20261021_kospi50_fundamental/growth_test.py
출력: results_growth.md, monthly_returns_growth.csv, trades_growth.csv
kospi50_test 의 데이터·시뮬레이션을 재사용하고 신호만 바꾼다.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kospi50_test as kt  # noqa: E402

START = pd.Timestamp("2017-08-31")

# ---------- 분기 매출 ----------
g = pd.read_parquet(os.path.join(kt.KR, "derived", "fundamentals_q.parquet"))
g["code"] = "A" + g["stock_code"].astype(str)
key = g.set_index(["code", "bsns_year", "q"])


def lk(col, codes, years, qs):
    return key[col].reindex(pd.MultiIndex.from_arrays([codes, years, qs])).values


prev_cum = np.where(g.q == 1, 0.0, lk("sales_cum", g.code, g.bsns_year, g.q - 1))
g["sales_q"] = g.sales_cum - prev_cum
key = g.set_index(["code", "bsns_year", "q"])
sq_ly = lk("sales_q", g.code, g.bsns_year - 1, g.q)
g["g1"] = (g.sales_q / pd.Series(sq_ly, index=g.index).where(lambda s: s > 0) - 1)
key = g.set_index(["code", "bsns_year", "q"])
pq_y = np.where(g.q == 1, g.bsns_year - 1, g.bsns_year)
pq_q = np.where(g.q == 1, 4, g.q - 1)
g["g1_prev"] = lk("g1", g.code, pq_y, pq_q)
g["g2"] = g.g1 - g.g1_prev
g = g.dropna(subset=["rcept_dt"]).sort_values(["code", "rcept_dt", "period_end"])
g["pe_max"] = g.groupby("code")["period_end"].cummax()
g_best = g[g.period_end == g.pe_max].drop_duplicates(["code", "period_end"], keep="last")


def growth_at(d, codes):
    sub = g_best[(g_best.rcept_dt <= d) & g_best.code.isin(codes)]
    return sub.sort_values("rcept_dt").groupby("code").tail(1).set_index("code")


_base = kt.signals_at


def signals_growth(d):
    s = _base(d)
    gu = growth_at(d, s.index)
    s["G1 매출성장"] = gu.g1.reindex(s.index)
    s["G2 매출가속"] = gu.g2.reindex(s.index)
    r_g, r_e = s["G1 매출성장"].rank(), s["S2 E/P"].rank()
    s["G3 성장대비PER"] = (r_g + r_e) / 2
    i = kt.tdays.searchsorted(d, side="right") - 1
    s["G4 3개월 모멘텀"] = kt.cf.iloc[i][s.index] / kt.cf.iloc[i - 63][s.index] - 1
    return s


SIGS = ["G1 매출성장", "G2 매출가속", "G3 성장대비PER", "G4 3개월 모멘텀"]
kt.signals_at = signals_growth
kt.SIGS = SIGS
kt.sig_dates[:] = [d for d in kt.sig_dates if d >= START]


def main():
    R, snaps = kt.simulate()
    R.to_csv(os.path.join(kt.HERE, "monthly_returns_growth.csv"))
    port = [c for c in R.columns if not c.startswith("turn::") and c != "start"]
    L = ["# 코스피 상위 50 · 성장 지표 상위 10 결과", "",
         f"사전 고정: PREREGISTRATION_growth.md (sha256 {open(os.path.join(kt.HERE, 'PREREGISTRATION_growth.sha256')).read()[:16]}…). "
         f"월 {len(R)}개, {R.start.iloc[0].date()} ~ {R.index[-1].date()}. 가격수익, 비용 반영(K200 제외).", ""]
    for end in kt.ENDS:
        W = R[R.index <= end + pd.Timedelta(days=10)]
        L += [f"## {W.start.iloc[0].date()} ~ {W.index[-1].date()} ({len(W)}개월)", "",
              "| 포트폴리오 | 연복리 | 변동성 | 수익/변동 | 최대낙폭 | 연 회전 | 대 동일가중 월초과 (t) | 대 시총가중 월초과 (t) |",
              "|---|---|---|---|---|---|---|---|"]
        for c in port:
            cg, vo, sh, md, to = kt.stats(W[c], W.get(f"turn::{c}"))
            ea, eb = W[c] - W["상위50 동일가중"], W[c] - W["상위50 시총가중"]
            L.append(f"| {c} | {cg:.1%} | {vo:.0%} | {sh:.2f} | {md:.0%} | {to:.1f} | {ea.mean():+.2%} ({kt.tstat(ea):.2f}) | {eb.mean():+.2%} ({kt.tstat(eb):.2f}) |")
        L.append("")
    L += ["## 판정 (사전 고정 기준)", "", "| 신호 | 두 기간 > 동일가중 | 두 기간 > 시총가중 | 대 동일가중 t(~2026-08) | 상위−하위 > 0 | 합격 |", "|---|---|---|---|---|---|"]
    for s in SIGS:
        top, bot = f"{s} | 상위10", f"{s} | 하위10"
        c1 = c2 = True
        for end in kt.ENDS:
            W = R[R.index <= end + pd.Timedelta(days=10)]
            c1 &= kt.stats(W[top])[0] > kt.stats(W["상위50 동일가중"])[0]
            c2 &= kt.stats(W[top])[0] > kt.stats(W["상위50 시총가중"])[0]
        t = kt.tstat(R[top] - R["상위50 동일가중"])
        c4 = kt.stats(R[top])[0] > kt.stats(R[bot])[0]
        ok = c1 and c2 and t >= 2.0 and c4
        L.append(f"| {s} | {'O' if c1 else 'X'} | {'O' if c2 else 'X'} | {t:.2f} | {'O' if c4 else 'X'} | {'**합격**' if ok else '불합격'} |")
    cols = ["상위50 시총가중", "상위50 동일가중"] + [f"{s} | 상위10" for s in SIGS]
    Y = (1 + R[cols]).groupby(R.index.year).prod() - 1
    L += ["", "## 연도별 (비용 후)", "", "| 연도 | " + " | ".join(c.replace(" | 상위10", "") for c in cols) + " |", "|" + "---|" * (len(cols) + 1)]
    for y, row in Y.iterrows():
        L.append(f"| {y} | " + " | ".join(f"{v:.0%}" for v in row) + " |")
    # 매매 기록: 신호별 가장 오래 든 종목·자주 든 종목
    trades = [(d, kt.next_tday(d)) for d in kt.sig_dates]
    rows = []
    for sname in SIGS:
        held = {}
        for d, t in trades[:-1]:
            v = snaps[d][sname].dropna()
            top = set(v.nlargest(kt.N_TOP).index) if len(v) >= 2 * kt.N_TOP else set()
            for c in list(held):
                if c not in top:
                    rows.append((sname, c, held.pop(c), t))
            for c in top:
                held.setdefault(c, t)
        rows += [(sname, c, t0, None) for c, t0 in held.items()]
    T = pd.DataFrame(rows, columns=["signal", "code", "entry", "exit"])
    T["name"] = T.code.map(kt.names)
    last = trades[-1][1]
    T["exit_eval"] = T.exit.fillna(last)
    T["months"] = [sum(1 for _, x in trades if e <= x < x2) for e, x2 in zip(T.entry, T.exit_eval)]
    T["ret"] = [kt.cf.at[x2, c] / kt.cf.at[e, c] - 1 for c, e, x2 in zip(T.code, T.entry, T.exit_eval)]
    T.to_csv(os.path.join(kt.HERE, "trades_growth.csv"), index=False)
    L += ["", "## 종목 (총 보유 개월 상위 12, 보유 구간 수익 복리)", ""]
    for sname, x in T.groupby("signal", sort=False):
        s = x.groupby("name").agg(m=("months", "sum"), comp=("ret", lambda r: (1 + r).prod() - 1)).sort_values("m", ascending=False).head(12)
        L.append(f"- {sname} (매수 {len(x)}건, 보유 중앙 {x.months.median():.0f}개월): " + ", ".join(f"{n} {int(r.m)}개월 {r.comp:+.0%}" for n, r in s.iterrows()))
    with open(os.path.join(kt.HERE, "results_growth.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
