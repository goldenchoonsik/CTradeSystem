"""122630 1m 엣지 스캔: (1) 지평별 손익분기 IC vs 후보 신호 분기별 IC, (2) 장 후반 모멘텀 규칙(롱 전용)."""
import numpy as np, pandas as pd

COST = 0.03  # 왕복 % (수수료 0.005%x2 + 슬리피지 0.01%x2)
df = pd.read_csv('DataVault/features/122630/data_snapshot_1m.csv',
                 usecols=['timestamp', 'close', 'ref_10100_close', 'ref_000660_close'])
df['ts'] = pd.to_datetime(df['timestamp']); df = df.set_index('ts').sort_index()
df['day'] = df.index.date
tm = df.index.hour * 100 + df.index.minute
g = df.groupby('day')

# ---------- (1) 지평별 ----------
print('=== (1) 지평별 손익분기 IC vs 후보 신호 (진입 09:10~15:00, 장중 전방수익) ===')
etf, fut = df['close'], df['ref_10100_close']
sig = pd.DataFrame(index=df.index)
sig['fut_lead5'] = g['ref_10100_close'].pct_change(5) * 2 - g['close'].pct_change(5)   # 선물 2배 - ETF (선행)
sig['day_ret'] = etf / g['close'].transform('first') - 1                                 # 당일 누적
sig['ma15'] = etf / g['close'].transform(lambda s: s.rolling(15).mean()) - 1
sig['hynix5'] = g['ref_000660_close'].pct_change(5)
sig['rev5'] = -g['close'].pct_change(5)
entry = (tm >= 910) & (tm <= 1500)
q = pd.PeriodIndex(df.index, freq='Q')
rows = []
for h in [6, 15, 30, 60]:
    fwd = (g['close'].shift(-h) / etf - 1) * 100
    m = entry & fwd.notna()
    sd = fwd[m].std()
    be = COST / (sd * 2.06)
    line = {'h': h, 'σ_h%': round(sd, 3), '손익분기IC': round(be, 3)}
    for c in sig:
        mm = m & sig[c].notna()
        ics = pd.Series({p: np.corrcoef(sig[c][mm & (q == p)].rank(), fwd[mm & (q == p)].rank())[0, 1]
                         for p in q[mm].unique()})
        line[c] = f"{ics.mean():+.3f}({(np.sign(ics) == np.sign(ics.mean())).sum()}/{len(ics)})"
        # 상위 10% 신호 진입 순기대수익
        top = mm & (sig[c] >= sig[c][mm].quantile(0.9))
        line[c + '_top10net'] = round(fwd[top].mean() - COST, 3)
    rows.append(line)
print(pd.DataFrame(rows).set_index('h').T.to_string())

# ---------- (2) 장 후반 모멘텀 (롱 전용) ----------
print('\n=== (2) 장 후반 모멘텀: 신호=전일종가→T0 수익, 결과=T0→T1 (롱 전용, 순수익=결과-0.03%) ===')
daily_close = g['close'].last()
prev_close = daily_close.shift(1)


def at(hhmm):
    s = df[tm == hhmm]['close']; s.index = s.index.date; return s


for t0, t1 in [(1450, 1519), (1500, 1510), (1500, 1519), (1510, 1519), (1430, 1500)]:
    p0, p1 = at(t0), at(t1)
    d = pd.DataFrame({'sig': p0 / prev_close - 1, 'out': (p1 / p0 - 1) * 100}).dropna()
    d = d[d.sig.abs() < 0.15]
    c = np.corrcoef(d.sig, d.out)[0, 1]
    print(f'\n[{t0}->{t1}] n={len(d)} 상관={c:+.3f}')
    for thr in [0.0, 0.01, 0.02, 0.03]:
        x = d[d.sig > thr].out - COST
        yr = x.groupby(pd.to_datetime(x.index).year).agg(['count', 'mean'])
        t = x.mean() / x.std() * np.sqrt(len(x)) if len(x) > 2 else np.nan
        print(f'  sig>{thr:.0%}: n={len(x)} 순평균={x.mean():+.3f}% t={t:+.2f} 승률={(x > 0).mean():.0%} | 연도별 '
              + ' '.join(f"{y}:{r['mean']:+.3f}({int(r['count'])})" for y, r in yr.iterrows()))
    # 대조: 하락일 (롱이면 손실이어야 정상)
    x = d[d.sig < -0.01].out
    print(f'  [대조] sig<-1%: n={len(x)} 평균결과={x.mean():+.3f}%')
