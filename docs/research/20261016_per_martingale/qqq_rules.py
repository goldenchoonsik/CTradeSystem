"""QQQ 단일 종목: 단순보유 vs 이동평균·RSI·이격도 규칙 (2026-10-16 탐색, 사전등록 아님). 가격 수익만(배당 제외), 신호 t 종가 → t+1 수익부터, 편도 0.05%."""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "/home/kuzimo/projects/CTradeSystem/docs/research/20261016_per_martingale")
import us_panel as u
px = pd.read_csv(f"{u.ROOT}/raw/yahoo/candle_1d/QQQ_1d.csv", parse_dates=["timestamp"]).set_index("timestamp").close
px = px[~px.index.duplicated()]
sp = u.splits("QQQ"); END = pd.Timestamp("2100-01-01")
px = px / pd.Series([u.S(sp, d, END) for d in px.index], index=px.index)       # 분할 연속
print("QQQ", px.index[0].date(), px.index[-1].date(), len(px), "분할", dict(sp))
r = px.pct_change().fillna(0)
COST = 0.0005
def sma(n): return px.rolling(n).mean()
def rsi(n):
    d = px.diff(); up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean(); dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)
def state(enter, exit_):          # 진입/청산 조건 상태기계
    pos = np.zeros(len(px)); st = 0; e, x = enter.values, exit_.values
    for i in range(len(px)):
        if st == 0 and e[i]: st = 1
        elif st == 1 and x[i]: st = 0
        pos[i] = st
    return pd.Series(pos, index=px.index)
S = {"단순보유": pd.Series(1.0, index=px.index)}
for n in (50, 100, 150, 200): S[f"종가>SMA{n}"] = (px > sma(n)).astype(float).where(sma(n).notna(), 0)
S["SMA50>SMA200 (골든크로스)"] = (sma(50) > sma(200)).astype(float).where(sma(200).notna(), 0)
S["SMA20>SMA60"] = (sma(20) > sma(60)).astype(float).where(sma(60).notna(), 0)
for lo, hi in ((30, 50), (30, 70), (40, 60)): S[f"RSI14 ≤{lo} 매수/≥{hi} 매도"] = state(rsi(14) <= lo, rsi(14) >= hi)
S["RSI2 ≤10 매수/≥70 매도"] = state(rsi(2) <= 10, rsi(2) >= 70)
for dv in (-0.03, -0.05, -0.08): S[f"이격도(20일) {dv:.0%} 이하 매수/20일선 복귀 매도"] = state(px / sma(20) - 1 <= dv, px >= sma(20))
S["이격도(200일) -10% 이하 매수/200일선 복귀"] = state(px / sma(200) - 1 <= -0.10, px >= sma(200))
S["추세+눌림: >SMA200 이고 RSI14≤40 매수 / RSI≥60 또는 <SMA200 매도"] = state((px > sma(200)) & (rsi(14) <= 40), (rsi(14) >= 60) | (px < sma(200)))

def perf(pos, a, b):
    pos = pos.shift(1).fillna(0); sw = pos.diff().abs().fillna(0)
    x = (pos * r - sw * COST)[a:b]
    if len(x) < 100: return None
    eq = (1 + x).cumprod(); yrs = (x.index[-1] - x.index[0]).days / 365.25
    return dict(CAGR=eq.iloc[-1] ** (1 / yrs) - 1, MDD=(eq / eq.cummax() - 1).min(), Sharpe=x.mean() / x.std() * np.sqrt(252) if x.std() > 0 else np.nan,
                노출=pos[a:b].mean(), 전환=int(sw[a:b].sum() / 2 + .5))
periods = [("전체 1999-03~2026-10", "1999-03-10", "2026-10-14"), ("닷컴 폭락 포함 1999~2009", "1999-03-10", "2009-12-31"),
           ("2010~2026", "2010-01-01", "2026-10-14"), ("최근 10년 2016-06~", "2016-06-01", "2026-10-14"), ("2022 하락장", "2022-01-01", "2022-12-31")]
pd.set_option("display.width", 250)
for lab, a, b in periods:
    rows = {k: perf(v, a, b) for k, v in S.items()}
    df = pd.DataFrame(rows).T.dropna(); bh = df.loc["단순보유"]
    df["CAGR차"] = df.CAGR - bh.CAGR
    print(f"\n=== {lab}")
    out = df.copy(); out[["CAGR", "MDD", "노출", "CAGR차"]] = (out[["CAGR", "MDD", "노출", "CAGR차"]] * 100).round(1); out["Sharpe"] = out.Sharpe.round(2)
    print(out.to_string())
# 연도별 승패 (규칙 vs 단순보유)
print("\n=== 연도별 단순보유 대비 이긴 해 수 (1999~2026, 28년)")
yr = {}
for k, v in S.items():
    pos = v.shift(1).fillna(0); x = pos * r - pos.diff().abs().fillna(0) * COST
    yr[k] = x.groupby(x.index.year).apply(lambda z: (1 + z).prod() - 1)
Y = pd.DataFrame(yr)
print((Y.sub(Y["단순보유"], axis=0) > 0).sum().drop("단순보유").to_string())
