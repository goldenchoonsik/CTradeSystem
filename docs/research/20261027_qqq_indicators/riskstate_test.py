import sys, numpy as np, pandas as pd
sys.path.insert(0, "/home/kuzimo/projects/CTradeSystem/docs/research/20261016_per_martingale")
import us_panel as u
COST = 0.0005; DIV = 0.006 / 252; M = f"{u.ROOT}/raw/macro"; G = f"{u.ROOT}/staging/global_macro/series"
def fred(n): d = pd.read_csv(f"{M}/{n}.csv", parse_dates=["observation_date"]).set_index("observation_date").iloc[:, 0]; return pd.to_numeric(d, errors="coerce")
baa, y2 = fred("BAA10Y"), fred("T10Y2Y")
vix = pd.read_csv(f"{G}/w_vix.csv", parse_dates=["date"]).set_index("date").value
tb = pd.read_csv(f"{G}/us_tb3m.csv", parse_dates=["date"]).set_index("date").value
def load(t):
    d = pd.read_csv(f"{u.ROOT}/raw/yahoo/candle_1d/{t}_1d.csv", parse_dates=["timestamp"]).set_index("timestamp"); d = d[~d.index.duplicated()]
    sp = u.splits(t); END = pd.Timestamp("2100-01-01"); return d.close / pd.Series([u.S(sp, x, END) for x in d.index], index=d.index)
rng = np.random.default_rng(5)
def shuffle_runs(w):
    v = w.values; ch = np.flatnonzero(np.diff(v) != 0) + 1; seg = np.split(v, ch); lv = {}
    for s in seg: lv.setdefault(s[0], []).append(s)
    for k in lv: rng.shuffle(lv[k])
    it = {k: iter(v_) for k, v_ in lv.items()}
    return pd.Series(np.concatenate([next(it[s[0]]) for s in seg]), index=w.index)
def stats(x, cash):
    eq = (1 + x).cumprod(); y = (x.index[-1] - x.index[0]).days / 365.25
    return eq.iloc[-1] ** (1 / y) - 1, (eq / eq.cummax() - 1).min(), ((x - cash).mean() / x.std()) * np.sqrt(252)
def run(w, r, cash):
    w = w.shift(1); sw = w.diff().abs().fillna(0); return (w * (r + DIV) + (1 - w) * cash - sw * COST).dropna()
rows, dd_all, score_ref = [], [], None
for t in "SPY QQQ DIA IWM XLK XLF XLE XLV XLP XLU".split():
    c = load(t); ix = c.index
    sh = lambda s: s.reindex(s.index.union(ix)).ffill().shift(1).reindex(ix) if False else s.reindex(s.index.union(ix)).ffill().reindex(ix).shift(1)
    C = (sh(baa) > sh(baa).rolling(250).mean()).astype(float); Y = (sh(y2) < 0).astype(float)
    V = (sh(vix).rolling(20).mean() > 25).astype(float)
    ok = sh(baa).rolling(250).mean().notna() & sh(y2).notna() & sh(vix).rolling(20).mean().notna() & (ix >= "1997-01-01")
    sc = (C + Y + V)[ok]; c2 = c[ok]; r = c2.pct_change().fillna(0)
    cash = (tb.reindex(tb.index.union(ix)).ffill().reindex(ix).bfill() / 100 / 252)[ok]
    w = sc.map({0: 1.0, 1: 1.0, 2: 0.6, 3: 0.3}); bh = stats(r + DIV, cash); rs = stats(run(w, r, cash), cash)
    cg = [stats(run(shuffle_runs(w), r, cash), cash)[0] for _ in range(300)]
    # 향후 252일 최대낙폭 -15% 이하 여부
    fmin = pd.concat([c2.shift(-k) for k in range(1, 253)], axis=1).min(axis=1) / c2 - 1
    d = pd.DataFrame({"sc": sc, "dd": fmin, "yr": sc.index.year, "t": t}).dropna(); d["hit"] = (d.dd <= -0.15).astype(float); dd_all.append(d)
    if t == "SPY": spy_c, spy_sc = c2, sc
    rows.append(dict(종목=t, 시작=str(sc.index[0].date()), BH_CAGR=bh[0], BH_MDD=bh[1], BH_샤프=bh[2], 규칙_CAGR=rs[0], 규칙_MDD=rs[1], 규칙_샤프=rs[2], 평균비중=w.mean(), 플라시보상위=(np.array(cg) >= rs[0]).mean()))
df = pd.DataFrame(rows); pd.set_option("display.width", 250); o = df.copy()
for k in o.columns[2:]: o[k] = o[k].round(3)
print(o.to_string(index=False))
ok_a = ((df.규칙_CAGR - df.BH_CAGR >= -0.005) & (df.규칙_MDD - df.BH_MDD >= 0.10)).sum()
print(f"\n(a) CAGR차≥-0.5%p & MDD 10%p↑ 개선 {ok_a}/10   평균 CAGR차 {(df.규칙_CAGR-df.BH_CAGR).mean()*100:.2f}%p  평균 MDD개선 {(df.규칙_MDD-df.BH_MDD).mean()*100:.1f}%p")
print(f"(b) 샤프 우위 {(df.규칙_샤프>df.BH_샤프).sum()}/10")
D = pd.concat(dd_all)
def ratio(x): hi = x[x.sc >= 2].hit.mean(); lo = x[x.sc <= 1].hit.mean(); return hi, lo, hi / lo if lo > 0 else np.nan
for lab, sub in (("SPY", D[D.t == "SPY"]), ("QQQ", D[D.t == "QQQ"]), ("풀링 10개", D)):
    hi, lo, rt = ratio(sub); yrs = sub.yr.unique(); bs = []
    for _ in range(1000):
        s = rng.choice(yrs, len(yrs)); B = pd.concat([sub[sub.yr == y] for y in s]); bs.append(ratio(B)[2])
    print(f"(c) {lab}: P(-15%↓ | 점수≥2)={hi:.2f}  P(| 점수≤1)={lo:.2f}  배율 {rt:.2f}  95% [{np.nanpercentile(bs,2.5):.2f}, {np.nanpercentile(bs,97.5):.2f}]  점수≥2 비율 {(sub.sc>=2).mean():.2f}")
print(f"(d) 플라시보 상위% 중앙값 {df.플라시보상위.median()*100:.0f}%")
print("\n(e) SPY 사건 점검: 점수≥2 최초일 → 그 종가 대비 이후 최저")
for name, a, b in (("닷컴", "2000-01-01", "2002-12-31"), ("2008", "2007-06-01", "2009-06-30"), ("2020", "2020-01-01", "2020-06-30"), ("2022", "2021-12-01", "2022-12-31")):
    s = spy_sc[a:b]; hit = s[s >= 2]
    if hit.empty: print(f"  {name}: 점수≥2 없음"); continue
    d0 = hit.index[0]; p0 = spy_c[d0]; seg = spy_c[d0:b]; pk = spy_c[a:d0].max()
    print(f"  {name}: 최초 {d0.date()}  구간 고점 대비 이미 {p0/pk-1:.1%}, 이후 추가 최저 {seg.min()/p0-1:.1%} ({seg.idxmin().date()})")
print("\n점수 분포 SPY:", spy_sc.value_counts(normalize=True).sort_index().round(2).to_dict())
