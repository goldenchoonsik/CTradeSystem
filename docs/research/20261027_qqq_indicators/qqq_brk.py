"""QQQ+BRK-B 정적 혼합 (2026-10-27, 탐색·비중 격자는 표본내). 연 1회 리밸런싱, 가격수익+QQQ 배당 0.6%/년(BRK 무배당), 리밸런싱 비용 무시(연 1회 소액)."""
import sys, numpy as np, pandas as pd
sys.path.insert(0, "/home/kuzimo/projects/CTradeSystem/docs/research/20261016_per_martingale")
import us_panel as u
def load(t):
    d = pd.read_csv(f"{u.ROOT}/raw/yahoo/candle_1d/{t}_1d.csv", parse_dates=["timestamp"]).set_index("timestamp"); d = d[~d.index.duplicated()]
    sp = u.splits(t); END = pd.Timestamp("2100-01-01"); return d.close / pd.Series([u.S(sp, x, END) for x in d.index], index=d.index)
q, b = load("QQQ"), load("BRK-B"); print("BRK-B 분할", dict(u.splits("BRK-B")))
rq = q.pct_change() + 0.006 / 252; rb = b.pct_change()
R = pd.concat([rq, rb], axis=1, keys=["Q", "B"]).dropna()
def port(wq, R):
    eq = 1.0; wts = np.array([wq, 1 - wq]); hold = wts.copy(); out = []; yr = R.index[0].year
    for d, row in R.iterrows():
        if d.year != yr: hold = wts.copy(); yr = d.year        # 연초 리밸런싱
        pr = float(hold @ row.values); hold = hold * (1 + row.values) / (1 + pr); out.append(pr)
    return pd.Series(out, index=R.index)
def st(x):
    eq = (1 + x).cumprod(); y = (x.index[-1] - x.index[0]).days / 365.25
    return eq.iloc[-1] ** (1 / y) - 1, (eq / eq.cummax() - 1).min(), x.mean() / x.std() * np.sqrt(252)
P = [("전체 1999-03~", "1999-03-10", "2026-10-20"), ("닷컴 포함 1999~2009", "1999-03-10", "2009-12-31"), ("2010~", "2010-01-01", "2026-10-20"), ("최근10년 2016-10~", "2016-10-01", "2026-10-20")]
W = [1.0, 0.8, 0.7, 0.6, 0.5, 0.4, 0.2, 0.0]; ports = {w: port(w, R) for w in W}
for lab, a, bb in P:
    print(f"\n=== {lab}")
    rows = {f"QQQ {int(w*100)}% / BRK {int((1-w)*100)}%": dict(zip(["CAGR", "MDD", "샤프"], st(ports[w][a:bb]))) for w in W}
    df = pd.DataFrame(rows).T; df["CAGR"] *= 100; df["MDD"] *= 100; print(df.round(2).to_string())
print("\n=== 상관 (일수익)", round(R.Q.corr(R.B), 2), " | 연도별 수익 QQQ vs BRK")
Y = R.groupby(R.index.year).apply(lambda z: (1 + z).prod() - 1); Y["차(B-Q)"] = Y.B - Y.Q
print((Y * 100).round(1).T.to_string())
print("BRK가 QQQ를 이긴 해:", int((Y.B > Y.Q).sum()), "/", len(Y))
print("\n=== 하락 구간 (고점→저점)")
for n, a, bb in (("닷컴", "2000-03-10", "2002-10-09"), ("2008", "2007-10-31", "2009-03-09"), ("2020", "2020-02-19", "2020-03-23"), ("2022", "2021-11-19", "2022-12-28")):
    r_ = R[a:bb]; print(f"  {n}: QQQ {((1+r_.Q).prod()-1)*100:.1f}%  BRK {((1+r_.B).prod()-1)*100:.1f}%  50:50 {((1+ports[0.5][a:bb]).prod()-1)*100:.1f}%")
