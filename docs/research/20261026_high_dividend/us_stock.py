"""미국 S&P500(당시 명단) 금리 초과 배당 전략 H1·H3·T vs 같은 명단 동일가중 B vs 금리 R (사전등록). 리츠는 제외하고 따로 본다.
형성 = 매년 첫 거래일 2001~2025, 12개월 보유, 배당 15% 원천징수 후, 왕복비용 0.2% (모두 동일). 생존편향: E 제외 / Q SPY 대체.
실행: .venv/bin/python -I docs/research/20261026_high_dividend/us_stock.py"""
import os, sys, gzip, json, glob, warnings, numpy as np, pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, "/home/kuzimo/projects/CTradeSystem/docs/research/20261021_slot_swing_recompute")
from us_data import load, ROOT
HERE = os.path.dirname(os.path.abspath(__file__))
RP = "/home/kuzimo/projects/CTradeSystem/docs/research/20261021_random_portfolio_vs_qqq"
src = open(f"{RP}/random_portfolios.py", encoding="utf-8").read()
ns = {"pd": pd}
exec(src[src.index("RENAME = {"):src.index("def tr_index")], ns)   # RENAME, REUSED, REUSED_TO, yahoo_ticker
yahoo_ticker = ns["yahoo_ticker"]
memb = pd.read_csv(f"{RP}/data_sp500_membership/sp500_components_updated.csv", parse_dates=["date"])
rate = pd.read_csv(f"{ROOT}/staging/global_macro/series/us_10y.csv", parse_dates=["date"]).set_index("date")["value"]
sec = f"{ROOT}/staging/us_fundamentals_sec/raw/submissions"
REIT = {os.path.basename(f)[:-8] for f in glob.glob(sec + "/*.json.gz") if json.load(gzip.open(f)).get("sic") == "6798"}
REIT |= {"EQR", "AVB", "INVH", "PCL", "PPS", "GGP", "AMB", "BRE", "TCO", "CBL", "NLY", "AGNC", "HCN", "HCP", "PEAK", "ARC", "FR", "LRY", "CCG", "ACAS", "SPG"}
spy = load("SPY"); cal = spy.index
WH, COST = 0.85, 0.002

_cache = {}
def get(t):
    if t in _cache: return _cache[t]
    df = load(t)
    if df is None: _cache[t] = None; return None
    cp = f"{ROOT}/raw/yahoo/corporate_actions/{t}.csv"; dv = pd.Series(dtype=float)
    if os.path.exists(cp):
        ca = pd.read_csv(cp, parse_dates=["date"]); raw = pd.read_csv(f"{ROOT}/raw/yahoo/candle_1d/{t}_1d.csv", parse_dates=["timestamp"]).set_index("timestamp")
        mult = pd.Series(1.0, index=df.index)
        for d, n, m in zip(ca[ca.type == "split"].date, ca[ca.type == "split"].numerator, ca[ca.type == "split"].denominator):
            mult[df.index < d] *= n / m
        g = ca[ca.type == "dividend"].groupby("date").amount.sum()
        dv = g / mult.reindex(g.index, method="ffill").fillna(1.0)
    _cache[t] = (df, dv); return _cache[t]

def paid(dv, a, b):   # a < date <= b
    return float(dv[(dv.index > a) & (dv.index <= b)].sum()) if len(dv) else 0.0

rows = []; years = range(2001, 2026)
for y in years:
    d0 = cal[cal >= pd.Timestamp(f"{y}-01-01")][0]
    d1 = cal[cal >= pd.Timestamp(f"{y+1}-01-01")][0] if y < 2025 else cal[cal >= pd.Timestamp("2026-01-01")][0]
    r10 = rate[rate.index < d0].iloc[-1]
    m = memb[memb.date <= d0].iloc[-1].tickers.split(","); n_mem = len(m)
    cand = []
    for t in m:
        yt = yahoo_ticker(t, d0)
        if not yt: continue
        g = get(yt)
        if g is None or d0 not in g[0].index: continue
        cand.append((t, yt, g))
    floor = 0.10 * np.nanmedian([g[0]["dvol_60d"].loc[d0] for _, _, g in cand])
    spyret = spy["close"].loc[d1] / spy["close"].loc[d0] - 1 + WH * paid(get("SPY")[1], d0, d1) / spy["close"].loc[d0]
    for t, yt, (df, dv) in cand:
        if df["dvol_60d"].loc[d0] < floor: continue
        p0 = df["close"].loc[d0]
        end = df.index[df.index <= d1][-1]
        if end <= d0: continue
        gone = end < d1 - pd.Timedelta(days=10)
        p1 = df["close"].loc[end]
        d_a = paid(dv, d0 - pd.Timedelta(days=365), d0 - pd.Timedelta(days=1)); d_b = paid(dv, d0 - pd.Timedelta(days=730), d0 - pd.Timedelta(days=366)); d_c = paid(dv, d0 - pd.Timedelta(days=1095), d0 - pd.Timedelta(days=731))
        dnext = paid(dv, d0 - pd.Timedelta(days=1), end)
        rows.append(dict(y=y, t=t, reit=(yt in REIT or t in REIT), yld=100 * d_a / p0, r10=r10, steady=(d_a > 0 and d_b > 0 and d_c > 0 and d_a >= 0.97 * d_b and d_b >= 0.97 * d_c),
                         ret=(p1 + WH * dnext) / p0 - 1, cut=(d_a > 0 and dnext < 0.8 * d_a), gone=gone, n_mem=n_mem, spy=spyret))
df = pd.DataFrame(rows); df.to_csv(f"{HERE}/us_stock_rows.csv", index=False)

def port(sub, sel):
    s = sub[sel(sub)]
    return (s.ret.mean() - COST) if len(s) else np.nan, len(s)
out = []
for y, g in df.groupby("y"):
    for grp, gg in (("주식", g[~g.reit]), ("리츠", g[g.reit])):
        r10 = g.r10.iloc[0]; spyr = g.spy.iloc[0] - COST
        n_all = len(gg); cover = len(g) / g.n_mem.iloc[0]
        bB, nB = port(gg, lambda s: s.yld >= 0)
        b1, n1 = port(gg, lambda s: s.yld > r10)
        b3, n3 = port(gg, lambda s: (s.yld > r10) & s.steady)
        pay = gg[gg.yld > 0]; q = pay.yld.quantile(0.8) if len(pay) else np.inf
        bT, nT = port(gg, lambda s: (s.yld >= q) & (s.yld > 0))
        trap = gg[gg.yld > r10].cut.mean() if n1 else np.nan
        out.append(dict(y=y, grp=grp, r10=r10, cover=cover, nB=nB, B=bB, n1=n1, H1=b1, n3=n3, H3=b3, nT=nT, T=bT, SPY=spyr, R=r10 / 100, cut1=trap))
o = pd.DataFrame(out); o.to_csv(f"{HERE}/us_stock_yearly.csv", index=False)
q3 = o.drop_duplicates("y").r10.quantile([1/3, 2/3]).values
o["reg"] = o.r10.apply(lambda v: "고금리" if v >= q3[1] else ("저금리" if v <= q3[0] else "중간"))
pd.set_option("display.width", 250)
print("10Y 3분위 경계", np.round(q3, 2), "| 명단 대비 데이터 적재율 평균", round(o.cover.mean(), 2))
for grp in ("주식", "리츠"):
    s = o[o.grp == grp]
    print(f"\n==== S&P500 {grp} (E: 데이터 있는 종목만) 연도별 % ====")
    t = s.set_index("y")[["r10", "cover", "nB", "n1", "n3", "B", "H1", "H3", "T", "SPY", "cut1"]].copy()
    for c in ["B", "H1", "H3", "T", "SPY"]: t[c] = t[c] * 100
    t["cut1"] = t.cut1 * 100
    print(t.round(1).to_string())
    for reg in ("고금리", "중간", "저금리", None):
        z = s if reg is None else s[s.reg == reg]
        lab = "전체" if reg is None else reg
        res = {}
        for k in ("H1", "H3", "T"):
            e = (z[k] - z["B"]).dropna()
            res[k] = f"{(z[k].mean())*100:5.1f}% (B대비 {e.mean()*100:+5.1f}%p, 승 {int((e>0).sum())}/{len(e)})"
        print(f"[{grp}|{lab} {len(z)}년] B {z.B.mean()*100:5.1f}% SPY {z.SPY.mean()*100:5.1f}% 금리 {z.R.mean()*100:4.1f}% | H1 {res['H1']} | H3 {res['H3']} | T {res['T']}")
# Q: 누락분을 SPY 로 채운 경우 (적재율 만큼 희석)
s = o[o.grp == "주식"].copy()
for k in ("B", "H1", "H3", "T"): s[k + "q"] = s[k] * s.cover + s.SPY * (1 - s.cover)
for reg in ("고금리", "중간", "저금리", None):
    z = s if reg is None else s[s.reg == reg]
    print(f"[Q 희석|{'전체' if reg is None else reg}] B {z.Bq.mean()*100:5.1f}% H1 {z.H1q.mean()*100:5.1f}% (B대비 {(z.H1q-z.Bq).mean()*100:+.1f}%p) H3 {z.H3q.mean()*100:5.1f}% ({(z.H3q-z.Bq).mean()*100:+.1f}%p) T {z.Tq.mean()*100:5.1f}% ({(z.Tq-z.Bq).mean()*100:+.1f}%p)")
# 사라진(상폐·피인수) 종목의 보유 중 평균
print("\n보유 중 사라진 종목 수(연평균)", df.groupby("y").gone.sum().mean().round(1), "| H1 중 배당 20% 이상 삭감 비율(전체 평균)", (o[o.grp=="주식"].cut1.mean()*100).round(1), "%")
