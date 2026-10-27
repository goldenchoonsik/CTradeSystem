"""QQQ: 지표 매매 vs 단순보유 확장 시험 (2026-10-27, 탐색+플라시보). 이전(10-16)은 SMA·RSI·이격도만.
추가: 모멘텀(TSMOM)·MACD·볼린저·돈치안·낙폭손절·VIX·변동성목표·계절성·앙상블·2배 레버리지.
공통: 분할 연속 종가, 신호 t종가→t+1, 편도 0.05%, 비보유일은 3개월 T-bill 월수익, 보유일엔 배당 0.6%/년 가산(공정성).
구간: 개발 1999-03~2012 / 홀드아웃 2013~2026-10 (규칙은 전부 사전 고정 파라미터, 격자 탐색 없음)."""
import sys, numpy as np, pandas as pd
sys.path.insert(0, "/home/kuzimo/projects/CTradeSystem/docs/research/20261016_per_martingale")
import us_panel as u
px = pd.read_csv(f"{u.ROOT}/raw/yahoo/candle_1d/QQQ_1d.csv", parse_dates=["timestamp"]).set_index("timestamp")
px = px[~px.index.duplicated()]; sp = u.splits("QQQ"); END = pd.Timestamp("2100-01-01")
adj = pd.Series([u.S(sp, d, END) for d in px.index], index=px.index)
c = px.close / adj; hi = px.high / adj; lo = px.low / adj
G = f"{u.ROOT}/staging/global_macro/series"
vix = pd.read_csv(f"{G}/w_vix.csv", parse_dates=["date"]).set_index("date").value.reindex(c.index).ffill()
tb = pd.read_csv(f"{G}/us_tb3m.csv", parse_dates=["date"]).set_index("date").value
tb = tb.reindex(tb.index.union(c.index)).ffill().reindex(c.index).bfill() / 100
cash = tb / 252; DIV = 0.006 / 252; COST = 0.0005
r = c.pct_change().fillna(0)
sma = lambda n: c.rolling(n).mean()
def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def state(enter, exit_):
    e, x = enter.values, exit_.values; pos = np.zeros(len(c)); st = 0
    for i in range(len(c)):
        if st == 0 and e[i]: st = 1
        elif st == 1 and x[i]: st = 0
        pos[i] = st
    return pd.Series(pos, index=c.index)
B = lambda s: s.astype(float)
S = {"단순보유": pd.Series(1.0, index=c.index)}
for n in (50, 100, 200): S[f"종가>SMA{n}"] = B(c > sma(n))
S["골든 50/200"] = B(sma(50) > sma(200))
for m, d in (("3개월", 63), ("6개월", 126), ("12개월", 252)): S[f"TSMOM {m}>0"] = B(c / c.shift(d) - 1 > 0)
S["12-1개월 모멘텀>0"] = B(c.shift(21) / c.shift(252) - 1 > 0)
macd = ema(c, 12) - ema(c, 26); S["MACD>시그널"] = B(macd > ema(macd, 9)); S["MACD>0"] = B(macd > 0)
m20, s20 = c.rolling(20).mean(), c.rolling(20).std()
S["볼린저 상단돌파→중심선이탈"] = state(c > m20 + 2 * s20, c < m20)
S["돈치안 100일 신고가→50일 신저가"] = state(c >= c.rolling(100).max(), c <= c.rolling(50).min())
for th in (0.10, 0.20):
    dd = c / c.rolling(252, min_periods=1).max() - 1
    S[f"낙폭{int(th*100)}% 손절→SMA100 복귀"] = state((c > sma(100)) & (dd > -th), dd <= -th)
S["VIX>30 회피(25 미만 복귀)"] = 1 - state(vix > 30, vix < 25)
S["VIX>VIX 50일평균×1.3 회피"] = B(~(vix > vix.rolling(50).mean() * 1.3))
S["SMA200 & VIX<30"] = B((c > sma(200)) & (vix < 30))
S["11~4월만 보유"] = pd.Series(c.index.month.isin([11, 12, 1, 2, 3, 4]).astype(float), index=c.index)
S["앙상블(SMA50·100·200 평균)"] = (B(c > sma(50)) + B(c > sma(100)) + B(c > sma(200))) / 3
S["앙상블(TSMOM3·6·12 평균)"] = sum(S[f"TSMOM {m}>0"] for m in ("3개월", "6개월", "12개월")) / 3
rv = r.rolling(20).std() * np.sqrt(252)
for t in (0.15, 0.20): S[f"변동성목표 {int(t*100)}% (상한 1배)"] = (t / rv).clip(upper=1).fillna(0)
S["변동성목표 20% (상한 1.5배)"] = (0.20 / rv).clip(upper=1.5).fillna(0)

def run(pos, lev=1.0, fin=0.0):
    pos = pos.shift(1).fillna(0); sw = pos.diff().abs().fillna(0)
    ret = pos * lev * (r + DIV) + (1 - pos * lev) * cash - pos * (lev - 1) * fin / 252 - sw * COST
    return ret, pos, sw
def perf(x, pos, sw, a, b):
    x, p, s = x[a:b], pos[a:b], sw[a:b]
    eq = (1 + x).cumprod(); y = (x.index[-1] - x.index[0]).days / 365.25
    ex = x - cash[a:b]
    return dict(CAGR=eq.iloc[-1] ** (1 / y) - 1, MDD=(eq / eq.cummax() - 1).min(), 샤프=ex.mean() / x.std() * np.sqrt(252), 노출=p.mean(), 전환=s.sum() / 2)
P = [("전체 1999~2026", "1999-03-10", "2026-10-20"), ("개발 1999~2012", "1999-03-10", "2012-12-31"), ("홀드아웃 2013~2026", "2013-01-01", "2026-10-20"), ("최근10년 2016-10~", "2016-10-01", "2026-10-20")]
R = {k: run(v) for k, v in S.items()}
# 2배 레버리지 변형 (일 2배 재조정, 차입비용=T-bill+0.5%)
fin = tb + 0.005
R["2배 보유"] = run(pd.Series(1.0, index=c.index), 2.0, fin); R["2배 + SMA200"] = run(S["종가>SMA200"], 2.0, fin)
R["2배 + 앙상블 SMA"] = run(S["앙상블(SMA50·100·200 평균)"], 2.0, fin)
pd.set_option("display.width", 250)
tabs = {}
for lab, a, b in P:
    df = pd.DataFrame({k: perf(x, p, s, a, b) for k, (x, p, s) in R.items()}).T
    df["CAGR차"] = df.CAGR - df.loc["단순보유", "CAGR"]; tabs[lab] = df
    o = df.copy(); o[["CAGR", "MDD", "노출", "CAGR차"]] *= 100; o = o.round({"CAGR": 1, "MDD": 1, "노출": 0, "CAGR차": 1, "샤프": 2, "전환": 0})
    print(f"\n=== {lab}\n{o.to_string()}")
# 플라시보: 같은 평균 체류시간의 무작위 온/오프 (노출 일치) 500회 — 규칙 CAGR이 무작위 타이밍 분포의 어디인지
rng = np.random.default_rng(7); a, b = "1999-03-10", "2026-10-20"
print("\n=== 플라시보(전체): 규칙 CAGR 이 같은 노출·같은 전환빈도 무작위 타이밍 500회 중 상위 몇 %인가")
for k in S:
    if k == "단순보유" or S[k].nunique() > 3: continue
    pos = S[k]; n_sw = pos.diff().abs().sum(); e = pos[a:b].mean()
    if n_sw < 2: continue
    pr = 1 / (len(pos) / n_sw * 2)   # 상태 전환 확률/일(대칭 근사)
    cg = []
    for _ in range(500):
        st = np.zeros(len(c)); s_ = 1 if rng.random() < e else 0
        flips = rng.random(len(c))
        for i in range(len(c)):
            p_flip = pr / (1 - e + 1e-9) * (1 - e) if s_ == 1 else pr / (e + 1e-9) * e
            if flips[i] < p_flip: s_ = 1 - s_
            st[i] = s_
        x, p_, sw_ = run(pd.Series(st, index=c.index)); cg.append(perf(x, p_, sw_, a, b)["CAGR"])
    real = tabs["전체 1999~2026"].loc[k, "CAGR"]
    print(f"{k:32s} 규칙 {real*100:5.1f}%  무작위 평균 {np.mean(cg)*100:5.1f}% 상위 {(np.array(cg) >= real).mean()*100:4.1f}%")
