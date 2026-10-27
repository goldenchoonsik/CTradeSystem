import sys, numpy as np, pandas as pd
sys.path.insert(0, "/home/kuzimo/projects/CTradeSystem/docs/research/20261016_per_martingale")
import us_panel as u
COST = 0.0005; DIV = 0.006 / 252; M = f"{u.ROOT}/raw/macro"; G = f"{u.ROOT}/staging/global_macro/series"; H = 126
def fred(n): d = pd.read_csv(f"{M}/{n}.csv", parse_dates=["observation_date"]).set_index("observation_date").iloc[:, 0]; return pd.to_numeric(d, errors="coerce")
baa, y2 = fred("BAA10Y"), fred("T10Y2Y")
vix = pd.read_csv(f"{G}/w_vix.csv", parse_dates=["date"]).set_index("date").value
tb = pd.read_csv(f"{G}/us_tb3m.csv", parse_dates=["date"]).set_index("date").value
def load(t):
    d = pd.read_csv(f"{u.ROOT}/raw/yahoo/candle_1d/{t}_1d.csv", parse_dates=["timestamp"]).set_index("timestamp"); d = d[~d.index.duplicated()]
    sp = u.splits(t); END = pd.Timestamp("2100-01-01"); return d.close / pd.Series([u.S(sp, x, END) for x in d.index], index=d.index)
def zs(s): return ((s - s.rolling(1260).mean()) / s.rolling(1260).std()).clip(-3, 3)
def logit_fit(X, y, lam=1.0):
    Xb = np.c_[np.ones(len(X)), X]; w = np.zeros(Xb.shape[1]); R = lam * np.eye(Xb.shape[1]); R[0, 0] = 0
    for _ in range(50):
        p = 1 / (1 + np.exp(-Xb @ w)); g = Xb.T @ (p - y) + R @ w; Hs = Xb.T @ (Xb * (p * (1 - p))[:, None]) + R
        st = np.linalg.solve(Hs, g); w -= st
        if np.abs(st).max() < 1e-6: break
    return w
def predict(w, X): return 1 / (1 + np.exp(-(np.c_[np.ones(len(X)), X] @ w)))
T = "SPY QQQ DIA IWM XLK XLF XLE XLV XLP XLU".split(); feats = {}; px = {}; lab = {}
for t in T:
    c = load(t); ix = c.index; px[t] = c
    sh = lambda s: s.reindex(s.index.union(ix)).ffill().reindex(ix).shift(1)
    F = pd.DataFrame({"T": zs(-np.log(c / c.rolling(200).mean())), "M": zs(-(c / c.shift(126) - 1)), "C": zs(sh(baa)), "Y": zs(-sh(y2)), "V": zs(sh(vix))})
    feats[t] = F
    fmin = pd.concat([c.shift(-k) for k in range(1, H + 1)], axis=1).min(axis=1) / c - 1
    lab[t] = (fmin <= -0.10).astype(float).where(fmin.notna())
# 합성 B: 매년 12월 말 재학습
refit = [pd.Timestamp(f"{y}-12-31") for y in range(2007, 2026)]
Wt = {t: pd.Series(np.nan, index=px[t].index) for t in T}
for k, rd in enumerate(refit):
    Xs, ys = [], []
    for t in T:
        ix = px[t].index; cut = ix[ix <= rd]
        if len(cut) <= H: continue
        lim = cut[-H - 1]; m = (ix <= lim); f = feats[t][m].dropna(); y = lab[t][f.index].dropna(); f = f.loc[y.index]
        Xs.append(f.values); ys.append(y.values)
    X = np.vstack(Xs); y = np.concatenate(ys); w = logit_fit(X, y); b = y.mean()
    end = refit[k + 1] if k + 1 < len(refit) else pd.Timestamp("2100-01-01")
    for t in T:
        f = feats[t][(feats[t].index > rd) & (feats[t].index <= end)].dropna()
        if len(f): Wt[t].loc[f.index] = (1 - 2 * (predict(w, f.values) - b)).clip(0.3, 1.0)
    if rd.year in (2007, 2015, 2025): print(f"  재학습 {rd.date()}: 계수 {np.round(w,2)} 기저율 {b:.2f}")
rng = np.random.default_rng(3)
def shuffle(w):
    v = w.values; o = np.argsort(v, kind="stable"); 
    ch = np.flatnonzero(np.diff(v) != 0) + 1; seg = np.split(v, ch); rng.shuffle(seg); return pd.Series(np.concatenate(seg), index=w.index)
def stats(x, cash):
    eq = (1 + x).cumprod(); y = (x.index[-1] - x.index[0]).days / 365.25
    return eq.iloc[-1] ** (1 / y) - 1, (eq / eq.cummax() - 1).min(), ((x - cash).mean() / x.std()) * np.sqrt(252)
def run(w, r, cash): w = w.shift(1); sw = w.diff().abs().fillna(0); return (w * (r + DIV) + (1 - w) * cash - sw * COST).dropna()
def old_rule(t):   # 이전 3지표 점수 규칙
    ix = px[t].index; sh = lambda s: s.reindex(s.index.union(ix)).ffill().reindex(ix).shift(1)
    sc = (sh(baa) > sh(baa).rolling(250).mean()).astype(float) + (sh(y2) < 0).astype(float) + (sh(vix).rolling(20).mean() > 25).astype(float)
    return sc.map({0: 1.0, 1: 1.0, 2: 0.6, 3: 0.3})
rows = []; abl = {k: [] for k in "TMCYV"}
for t in T:
    c = px[t]; r = c.pct_change().fillna(0); ix = c.index
    cash = tb.reindex(tb.index.union(ix)).ffill().reindex(ix).bfill() / 100 / 252
    F = feats[t]; A = (1 - 0.35 * F.mean(axis=1, skipna=False).clip(lower=0)).clip(0.3, 1.0); B = Wt[t]; O = old_rule(t)
    sel = (ix >= "2008-01-01") & A.notna() & B.notna() & O.notna(); sl = lambda s: s[sel]
    rr, cc = sl(r), sl(cash); bh = stats(rr + DIV, cc); out = dict(종목=t, BH_CAGR=bh[0], BH_MDD=bh[1], BH_샤프=bh[2])
    for nm, w in (("A", A), ("B", B), ("구3지표", O)):
        s_ = stats(run(sl(w), rr, cc), cc); out.update({f"{nm}_CAGR": s_[0], f"{nm}_MDD": s_[1], f"{nm}_샤프": s_[2], f"{nm}_평균비중": sl(w).mean()})
        if nm in ("A", "B"):
            cg = [stats(run(shuffle(sl(w)), rr, cc), cc)[0] for _ in range(200)]; out[f"{nm}_플라시보상위"] = (np.array(cg) >= s_[0]).mean()
    for k in "TMCYV":
        Ak = (1 - 0.35 * F.drop(columns=k).mean(axis=1, skipna=False).clip(lower=0)).clip(0.3, 1.0)
        abl[k].append(stats(run(sl(Ak), rr, cc), cc)[2] - bh[2])
    rows.append(out)
df = pd.DataFrame(rows); pd.set_option("display.width", 300); o = df.copy()
for k in o.columns[1:]: o[k] = o[k].round(3)
print(o.to_string(index=False))
print()
for nm in ("A", "B", "구3지표"):
    a = ((df[f"{nm}_CAGR"] - df.BH_CAGR >= -0.005) & (df[f"{nm}_MDD"] - df.BH_MDD >= 0.10)).sum(); b = (df[f"{nm}_샤프"] > df.BH_샤프).sum()
    print(f"[{nm}] (a) {a}/10  평균 CAGR차 {(df[f'{nm}_CAGR']-df.BH_CAGR).mean()*100:.2f}%p  평균 MDD개선 {(df[f'{nm}_MDD']-df.BH_MDD).mean()*100:.1f}%p  (b) 샤프 우위 {b}/10", end="")
    if nm != "구3지표": print(f"  (d) 플라시보 상위 중앙값 {df[f'{nm}_플라시보상위'].median()*100:.0f}%  (f) 구3지표 대비 샤프 우위 {(df[f'{nm}_샤프']>df['구3지표_샤프']).sum()}/10")
    else: print()
print("\n[ablation: 성분 1개 제거한 합성 A 의 평균 샤프차(단순보유 대비) — 전체 A:", round((df.A_샤프 - df.BH_샤프).mean(), 3), "]")
for k in "TMCYV": print(f"  제거 {k}: {np.mean(abl[k]):.3f}")
