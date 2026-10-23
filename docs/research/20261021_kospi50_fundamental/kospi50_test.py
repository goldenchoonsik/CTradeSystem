"""코스피 시총 상위 50 안에서 재무 지표로 고르기 — PREREGISTRATION.md 그대로.

실행: .venv/bin/python -I docs/research/20261021_kospi50_fundamental/kospi50_test.py
출력: results.md, monthly_returns.csv, picks_latest.csv
"""
import glob
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
KR = os.path.join(ROOT, "DataVault", "staging", "kr_equity")
START_SIG, LAST_SIG = pd.Timestamp("2017-03-01"), pd.Timestamp("2026-07-31")
ENDS = (pd.Timestamp("2023-12-31"), pd.Timestamp("2026-08-31"))
BUY_C, SELL_C = 0.0012, 0.0033
N_TOP, N_UNI = 10, 50

# ---------- 데이터 ----------
mc = pd.concat([pd.read_parquet(p, columns=["Code", "Name", "Market", "Marcap", "Date"])
                for p in sorted(glob.glob(os.path.join(KR, "raw", "marcap", "*", "*.parquet")))])
mc = mc[(mc.Market == "KOSPI") & mc.Code.str.endswith("0") & (mc.Marcap > 0)]
mc["code"] = "A" + mc.Code
names = mc.drop_duplicates("code", keep="last").set_index("code")["Name"]

pr = pd.read_parquet(os.path.join(KR, "derived", "prices_1d.parquet"), columns=["code", "date", "close"])
pr["code"] = pr["code"].astype(str)
close = pr.pivot(index="date", columns="code", values="close").sort_index()
cf = close.ffill()
tdays = close.index

k200 = pd.read_csv(os.path.join(KR, "raw", "creon", "index_1d", "U180_1d.csv"), usecols=["date", "close"])
k200 = pd.Series(k200["close"].values, index=pd.to_datetime(k200["date"].astype(str))).sort_index()

# 신호일 = 매월 마지막 거래일(marcap 기준)
mdates = pd.Series(sorted(mc.Date.unique()))
sig_dates = mdates.groupby(mdates.dt.to_period("M")).max()
sig_dates = sig_dates[(sig_dates >= START_SIG) & (sig_dates <= LAST_SIG)].tolist()


def next_tday(d):
    i = tdays.searchsorted(d, side="right")
    return tdays[i] if i < len(tdays) else None


# ---------- 재무 ----------
f = pd.read_parquet(os.path.join(KR, "derived", "fundamentals_q.parquet"))
f["code"] = "A" + f["stock_code"].astype(str)
f = f.sort_values(["code", "bsns_year", "q"])
key = f.set_index(["code", "bsns_year", "q"])


def lookup(col, codes, years, qs):
    idx = pd.MultiIndex.from_arrays([codes, years, qs])
    return key[col].reindex(idx).values


ni_ann_prev = lookup("net_income_annual", f.code, f.bsns_year - 1, np.full(len(f), 4))
op_ann_prev = lookup("op_income_annual", f.code, f.bsns_year - 1, np.full(len(f), 4))
ni_cum_prev = lookup("net_income_cum", f.code, f.bsns_year - 1, f.q)
op_cum_prev = lookup("op_income_cum", f.code, f.bsns_year - 1, f.q)
f["ttm_ni"] = np.where(f.q == 4, f.net_income_cum, ni_ann_prev + f.net_income_cum - ni_cum_prev)
f["ttm_op"] = np.where(f.q == 4, f.op_income_cum, op_ann_prev + f.op_income_cum - op_cum_prev)
op_cum_qm1 = np.where(f.q == 1, 0.0, lookup("op_income_cum", f.code, f.bsns_year, f.q - 1))
f["op_q"] = f.op_income_cum - op_cum_qm1
key = f.set_index(["code", "bsns_year", "q"])
f["op_q_prev"] = lookup("op_q", f.code, f.bsns_year - 1, f.q)
f["assets_prev"] = lookup("assets", f.code, f.bsns_year - 1, f.q)
f["s4"] = (f.op_q - f.op_q_prev) / f.assets_prev.where(f.assets_prev > 0)
f["fin"] = f.sales_cum.isna() & f.sales.isna()

# 신호일마다 '그때까지 접수된 것 중 가장 최근 기간' 행
f = f.dropna(subset=["rcept_dt"]).sort_values(["code", "rcept_dt", "period_end"])
f["pe_max"] = f.groupby("code")["period_end"].cummax()
f_best = f[f.period_end == f.pe_max].drop_duplicates(["code", "period_end"], keep="last")


def fund_at(d, codes):
    sub = f_best[(f_best.rcept_dt <= d) & f_best.code.isin(codes)]
    return sub.sort_values("rcept_dt").groupby("code").tail(1).set_index("code")


# ---------- 신호 ----------
def signals_at(d):
    day = mc[mc.Date == d].sort_values("Marcap", ascending=False)
    day = day[day.code.isin(close.columns)].head(N_UNI).set_index("code")
    fu = fund_at(d, day.index)
    s = pd.DataFrame(index=day.index)
    s["mcap"] = day.Marcap
    s["name"] = day.Name
    eq = fu.equity.reindex(s.index)
    s["S1 B/M"] = (eq / s.mcap).where(eq > 0)
    s["S2 E/P"] = fu.ttm_ni.reindex(s.index) / s.mcap
    s["S3 OP/A"] = (fu.ttm_op / fu.assets.where(fu.assets > 0)).reindex(s.index).where(~fu.fin.reindex(s.index).fillna(False).astype(bool))
    s["S4 이익개선"] = fu.s4.reindex(s.index)
    r1, r3 = s["S1 B/M"].rank(), s["S3 OP/A"].rank()
    s["S5 가치+수익성"] = pd.concat([r1, r3], axis=1).mean(axis=1).where(r1.notna())
    i = tdays.searchsorted(d, side="right") - 1
    if i >= 252:
        s["M 12-1 모멘텀"] = cf.iloc[i - 21][s.index] / cf.iloc[i - 252][s.index] - 1
    else:
        s["M 12-1 모멘텀"] = np.nan
    return s


SIGS = ["S1 B/M", "S2 E/P", "S3 OP/A", "S4 이익개선", "S5 가치+수익성", "M 12-1 모멘텀"]


# ---------- 시뮬레이션 ----------
def simulate():
    rows, weights_all = [], {}
    books = {}  # 이름 → 직전 보유 비중(표류 후)
    trades = [(d, next_tday(d)) for d in sig_dates]
    trades = [(d, t) for d, t in trades if t is not None]
    snaps = {d: signals_at(d) for d, _ in trades}
    for i in range(len(trades) - 1):
        d, t0 = trades[i]
        t1 = trades[i + 1][1]
        s = snaps[d]
        ret = cf.loc[t1, s.index] / cf.loc[t0, s.index] - 1
        targets = {"상위50 시총가중": s.mcap / s.mcap.sum(),
                   "상위50 동일가중": pd.Series(1 / len(s), index=s.index)}
        for g in SIGS:
            v = s[g].dropna()
            if len(v) < 2 * N_TOP:
                continue
            top, bot = v.nlargest(N_TOP).index, v.nsmallest(N_TOP).index
            targets[f"{g} | 상위10"] = pd.Series(1 / N_TOP, index=top)
            targets[f"{g} | 하위10"] = pd.Series(1 / N_TOP, index=bot)
        rec = {"start": t0, "end": t1}
        for nm, w in targets.items():
            old = books.get(nm, pd.Series(dtype=float))
            allc = w.index.union(old.index)
            dw = w.reindex(allc).fillna(0) - old.reindex(allc).fillna(0)
            cost = BUY_C * dw.clip(lower=0).sum() + SELL_C * (-dw).clip(lower=0).sum()
            r = ret.reindex(w.index).fillna(0.0)
            gross = (w * r).sum()
            rec[nm] = (1 - cost) * (1 + gross) - 1
            rec[f"turn::{nm}"] = dw.abs().sum() / 2
            drift = w * (1 + r)
            books[nm] = drift / drift.sum()
        rec["K200"] = k200.asof(t1) / k200.asof(t0) - 1
        rows.append(rec)
    return pd.DataFrame(rows).set_index("end"), snaps


def stats(r, turn=None):
    eq = (1 + r).cumprod()
    yrs = len(r) / 12
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    vol = r.std() * np.sqrt(12)
    mdd = (eq / np.maximum(eq.cummax(), 1) - 1).min()
    return cagr, vol, cagr / vol if vol else np.nan, mdd, (turn.mean() * 12 if turn is not None else np.nan)


def tstat(x):
    x = x.dropna()
    return x.mean() / x.std(ddof=1) * np.sqrt(len(x))


def main():
    R, snaps = simulate()
    R.to_csv(os.path.join(HERE, "monthly_returns.csv"))
    port = [c for c in R.columns if not c.startswith("turn::")]
    L = ["# 코스피 시총 상위 50 · 재무 지표 상위 10 결과", "",
         f"사전 고정: PREREGISTRATION.md (sha256 {open(os.path.join(HERE, 'PREREGISTRATION.sha256')).read()[:16]}…). "
         f"월 {len(R)}개, {R.start.iloc[0].date()} ~ {R.index[-1].date()}. 가격수익(배당 제외), 비용 반영(K200 제외).", ""]
    for end in ENDS:
        W = R[R.index <= end + pd.Timedelta(days=10)]
        L += [f"## {W.start.iloc[0].date()} ~ {W.index[-1].date()} ({len(W)}개월)", "",
              "| 포트폴리오 | 연복리 | 변동성 | 수익/변동 | 최대낙폭 | 연 회전 | 대 동일가중 월초과 (t) | 대 시총가중 월초과 (t) |",
              "|---|---|---|---|---|---|---|---|"]
        for c in port:
            if c == "start":
                continue
            tn = W.get(f"turn::{c}")
            cg, vo, sh, md, to = stats(W[c], tn)
            ea, eb = W[c] - W["상위50 동일가중"], W[c] - W["상위50 시총가중"]
            L.append(f"| {c} | {cg:.1%} | {vo:.0%} | {sh:.2f} | {md:.0%} | {to:.1f} | {ea.mean():+.2%} ({tstat(ea):.2f}) | {eb.mean():+.2%} ({tstat(eb):.2f}) |")
        L.append("")
    # 판정
    L += ["## 판정 (사전 고정 기준)", "", "| 신호 | 두 기간 > 동일가중 | 두 기간 > 시총가중 | 대 동일가중 t(~2026-08) | 상위−하위 > 0 | 합격 |", "|---|---|---|---|---|---|"]
    for g in SIGS:
        top, bot = f"{g} | 상위10", f"{g} | 하위10"
        if top not in R:
            continue
        c1 = c2 = True
        for end in ENDS:
            W = R[R.index <= end + pd.Timedelta(days=10)]
            c1 &= stats(W[top])[0] > stats(W["상위50 동일가중"])[0]
            c2 &= stats(W[top])[0] > stats(W["상위50 시총가중"])[0]
        t = tstat(R[top] - R["상위50 동일가중"])
        c4 = stats(R[top])[0] > stats(R[bot])[0]
        ok = c1 and c2 and t >= 2.0 and c4
        L.append(f"| {g} | {'O' if c1 else 'X'} | {'O' if c2 else 'X'} | {t:.2f} | {'O' if c4 else 'X'} | {'**합격**' if ok else '불합격'} |")
    # 연도별
    L += ["", "## 연도별 (비용 후)", ""]
    cols = ["상위50 시총가중", "상위50 동일가중", "K200"] + [f"{g} | 상위10" for g in SIGS if f"{g} | 상위10" in R]
    Y = (1 + R[cols]).groupby(R.index.year).prod() - 1
    L.append("| 연도 | " + " | ".join(c.replace(" | 상위10", "") for c in cols) + " |")
    L.append("|" + "---|" * (len(cols) + 1))
    for y, row in Y.iterrows():
        L.append(f"| {y} | " + " | ".join(f"{v:.0%}" for v in row) + " |")
    # 최신 종목
    last_d = max(d for d in mc.Date.unique() if d <= pd.Timestamp("2026-09-30"))
    s = signals_at(pd.Timestamp(last_d))
    L += ["", f"## 참고: {pd.Timestamp(last_d).date()} 기준 상위 10", ""]
    out = []
    for g in SIGS[:5]:
        top = s[g].dropna().nlargest(N_TOP).index
        L.append(f"- {g}: " + ", ".join(names.get(c, c) for c in top))
        out += [(g, c, names.get(c, c), s.at[c, g]) for c in top]
    pd.DataFrame(out, columns=["signal", "code", "name", "value"]).to_csv(os.path.join(HERE, "picks_latest.csv"), index=False)
    with open(os.path.join(HERE, "results.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
