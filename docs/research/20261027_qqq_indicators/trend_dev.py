"""QQQ 로그 추세선 방식별 편차(이격 표준편차)와 예측력 비교. A는 전구간 회귀(미래참조, 기준용), 나머지는 인과적."""
import sys, numpy as np, pandas as pd
sys.path.insert(0, "/home/kuzimo/projects/CTradeSystem/docs/research/20261016_per_martingale")
import us_panel as u
px = pd.read_csv(f"{u.ROOT}/raw/yahoo/candle_1d/QQQ_1d.csv", parse_dates=["timestamp"]).set_index("timestamp")
px = px[~px.index.duplicated()]; sp = u.splits("QQQ"); END = pd.Timestamp("2100-01-01")
c = px.close / pd.Series([u.S(sp, d, END) for d in px.index], index=px.index)
L = np.log(c); t = np.arange(len(L), dtype=float); n = len(L)
def rolling_fit(w):          # 최근 w일 로그 선형회귀의 오늘 값
    out = np.full(n, np.nan); y = L.values
    for i in range(w - 1, n):
        yy = y[i - w + 1:i + 1]; xx = np.arange(w); b, a = np.polyfit(xx, yy, 1); out[i] = a + b * (w - 1)
    return pd.Series(out, index=L.index)
def expanding_fit(minw=504):
    out = np.full(n, np.nan); y = L.values
    for i in range(minw, n, 1):
        if i % 5 and i != n - 1: out[i] = np.nan; continue
        b, a = np.polyfit(t[:i + 1], y[:i + 1], 1); out[i] = a + b * t[i]
    return pd.Series(out, index=L.index).ffill()
T = {}
b, a = np.polyfit(t, L.values, 1); T["A 전구간 회귀(미래참조·기준)"] = pd.Series(a + b * t, index=L.index)
T["B 확장창 회귀(인과)"] = expanding_fit()
for y_ in (3, 5, 10): T[f"C 롤링 {y_}년 회귀"] = rolling_fit(252 * y_)
for d in (200, 500, 1000): T[f"D EMA{d}(로그)"] = L.ewm(span=d, adjust=False).mean()
for d in (200, 756): T[f"E SMA{d}(로그)"] = L.rolling(d).mean()
fwd = L.shift(-252) - L
rows = []
for k, tr in T.items():
    g = (L - tr)
    for lab, a_, b_ in (("1999~", "2002-01-01", "2026-10-20"), ("2010~", "2010-01-01", "2026-10-20")):
        x = g[a_:b_].dropna(); f = fwd[x.index].dropna(); xx = x[f.index]
        sd = x.std(); ac = x.autocorr(252)
        rows.append(dict(방식=k, 구간=lab, 이격표준편차=round(sd, 3), 이격_평균=round(x.mean(), 3), 이격_1년자기상관=round(ac, 2), 이격vs향후1년수익_상관=round(np.corrcoef(xx, f)[0, 1], 2)))
pd.set_option("display.width", 250); print(pd.DataFrame(rows).to_string(index=False))
