import sys, numpy as np, pandas as pd
from numpy.lib.stride_tricks import sliding_window_view as swv
sys.path.insert(0, "/home/kuzimo/projects/CTradeSystem/docs/research/20261016_per_martingale")
import us_panel as u
W = 2520; COST = 0.0005; DIV = 0.006 / 252
G = f"{u.ROOT}/staging/global_macro/series"
tb = pd.read_csv(f"{G}/us_tb3m.csv", parse_dates=["date"]).set_index("date").value
def load(t):
    d = pd.read_csv(f"{u.ROOT}/raw/yahoo/candle_1d/{t}_1d.csv", parse_dates=["timestamp"]).set_index("timestamp"); d = d[~d.index.duplicated()]
    sp = u.splits(t); END = pd.Timestamp("2100-01-01")
    return d.close / pd.Series([u.S(sp, x, END) for x in d.index], index=d.index)
def zscore(c):
    y = np.log(c.values); n = len(y); z = np.full(n, np.nan)
    if n < W: return pd.Series(z, index=c.index)
    x = np.arange(W) - (W - 1) / 2; X = swv(y, W)
    b = X @ x / (x @ x); m = X.mean(1); fit = m + b * ((W - 1) / 2)
    res = X - (m[:, None] + b[:, None] * x[None, :]); sd = res.std(1)
    z[W - 1:] = (y[W - 1:] - fit) / sd
    return pd.Series(z, index=c.index)
def state(z, hi=1.5, lo=0.5):
    p = np.ones(len(z)); st = 1
    for i, v in enumerate(z.values):
        if np.isnan(v): p[i] = np.nan; continue
        if st == 1 and v > hi: st = 0
        elif st == 0 and v < lo: st = 1
        p[i] = st
    return pd.Series(p, index=z.index)
def run(pos, r, cash):
    pos = pos.shift(1); sw = pos.diff().abs().fillna(0)
    return pos * (r + DIV) + (1 - pos) * cash - sw * COST
def stats(x, cash):
    eq = (1 + x).cumprod(); y = (x.index[-1] - x.index[0]).days / 365.25
    return (eq.iloc[-1] ** (1 / y) - 1, (eq / eq.cummax() - 1).min(), ((x - cash).mean() / x.std()) * np.sqrt(252))
rng = np.random.default_rng(11)
def shuffle_runs(pos):
    v = pos.values; ch = np.flatnonzero(np.diff(v) != 0) + 1; seg = np.split(v, ch)
    on = [s for s in seg if s[0] == 1]; off = [s for s in seg if s[0] == 0]
    rng.shuffle(on); rng.shuffle(off); io = iter(on); iff = iter(off)
    return pd.Series(np.concatenate([next(io) if s[0] == 1 else next(iff) for s in seg]), index=pos.index)
rows = []; ann = []
for t in "SPY QQQ DIA IWM XLK XLF XLE XLV XLP XLU".split():
    c = load(t); z = zscore(c); r = c.pct_change().fillna(0)
    cash = (tb.reindex(tb.index.union(c.index)).ffill().reindex(c.index).bfill() / 100) / 252
    ok = z.notna(); idx = c.index[ok]
    p1 = state(z)[idx]; p2 = (1 - 0.4 * z.clip(lower=0)).clip(0, 1)[idx]
    rr, cc = r[idx], cash[idx]
    bh = stats(rr + DIV, cc); s1 = stats(run(p1, rr, cc).dropna(), cc); s2 = stats(run(p2, rr, cc).dropna(), cc)
    cg = [stats(run(shuffle_runs(p1), rr, cc).dropna(), cc)[0] for _ in range(300)]
    pl = (np.array(cg) >= s1[0]).mean()
    rows.append(dict(종목=t, 시작=str(idx[0].date()), BH_CAGR=bh[0], BH_MDD=bh[1], BH_샤프=bh[2], R1_CAGR=s1[0], R1_MDD=s1[1], R1_샤프=s1[2], R1_노출=p1.mean(), R2_CAGR=s2[0], R2_MDD=s2[1], R2_샤프=s2[2], 플라시보상위=pl))
    # 연말 z vs 다음 해 수익 (겹치지 않음)
    ye = c.groupby(c.index.year).tail(1); zz = z.reindex(ye.index); fw = np.log(ye).diff().shift(-1)
    a = pd.DataFrame({"z": zz.values, "f": fw.values, "yr": ye.index.year, "t": t}).dropna(); ann.append(a)
df = pd.DataFrame(rows); pd.set_option("display.width", 250)
o = df.copy()
for k in o.columns[2:]: o[k] = o[k].round(3)
print(o.to_string(index=False))
print(f"\n(a) R1 CAGR 우위 {int((df.R1_CAGR>df.BH_CAGR).sum())}/10   R2 {int((df.R2_CAGR>df.BH_CAGR).sum())}/10")
print(f"(b) 평균 CAGR차 R1 {(df.R1_CAGR-df.BH_CAGR).mean()*100:.2f}%p  R2 {(df.R2_CAGR-df.BH_CAGR).mean()*100:.2f}%p")
print(f"(c) 샤프 우위 R1 {int((df.R1_샤프>df.BH_샤프).sum())}/10   R2 {int((df.R2_샤프>df.BH_샤프).sum())}/10")
A = pd.concat(ann); 
# 연도 내 횡단면 상대 순위로 시장 효과 제거도 병행
sp_ = A[["z", "f"]].corr(method="spearman").iloc[0, 1]
yrs = A.yr.unique(); bs = []
for _ in range(2000):
    s = rng.choice(yrs, len(yrs)); B = pd.concat([A[A.yr == y] for y in s]); bs.append(B[["z", "f"]].corr(method="spearman").iloc[0, 1])
print(f"(d) 연말 z vs 다음 해 로그수익 풀링 순위상관 {sp_:.2f}  (n={len(A)}, 연도 {len(yrs)}개)  부트스트랩95% [{np.percentile(bs,2.5):.2f}, {np.percentile(bs,97.5):.2f}]")
A["zr"] = A.groupby("yr").z.rank(pct=True); A["fr"] = A.groupby("yr").f.rank(pct=True)
print(f"    연도 내 횡단면(시장효과 제거) 순위상관 {A[['zr','fr']].corr().iloc[0,1]:.2f}")
print(f"(e) 플라시보 상위% 중앙값 {df.플라시보상위.median()*100:.0f}%")
