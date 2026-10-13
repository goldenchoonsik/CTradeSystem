"""122630 vs 2x K200 선물 괴리 회귀 (롱 전용, 당일 청산).
p = ln(ETF) - 2 ln(F); z = p - p 의 과거 W분 평균 (당일 안에서, 일중 리밸런싱/베이시스 레벨 제거).
z 가 매우 낮으면(ETF 가 싸면) 매수. 진입 = 신호 봉 종가(lag0, 낙관) / 다음 봉 종가(lag1, 현실). 보유 h분. 비용 0.03%.
"""
import numpy as np, pandas as pd

d = pd.read_csv('DataVault/features/122630/data_snapshot_1m.csv', usecols=['timestamp', 'close', 'volume', 'ref_10100_close'])
d['ts'] = pd.to_datetime(d['timestamp']); d = d.set_index('ts').sort_index()
d['day'] = d.index.normalize(); tm = d.index.hour * 100 + d.index.minute
d = d[(tm >= 901) & (tm <= 1519)].dropna(subset=['close', 'ref_10100_close'])
tm = d.index.hour * 100 + d.index.minute
g = d.groupby('day')
d['p'] = np.log(d.close) - 2 * np.log(d.ref_10100_close)
entry_ok = (tm >= 910) & (tm <= 1500)
q = pd.PeriodIndex(d.index, freq='Q')
COST = 0.03
print(f'기간 {d.index[0].date()}~{d.index[-1].date()}, 행 {len(d):,}')
for W in [10, 30]:
    d['z'] = (d.p - g['p'].transform(lambda s: s.rolling(W, min_periods=W).mean())) * 100  # %
    zs = d.z[entry_ok].dropna()
    print(f'\n=== W={W}분  z 분포(%): p1={zs.quantile(.01):+.3f} p5={zs.quantile(.05):+.3f} p50={zs.median():+.3f} p95={zs.quantile(.95):+.3f}')
    for lag in [0, 1]:
        for h in [2, 5, 10, 20]:
            e = g['close'].shift(-lag); x = g['close'].shift(-lag - h)
            fwd = (x / e - 1) * 100
            m = entry_ok & d.z.notna() & fwd.notna()
            ic = np.corrcoef(d.z[m].rank(), fwd[m].rank())[0, 1]
            out = []
            for pct in [0.05, 0.01]:
                thr = d.z[m].quantile(pct)
                sel = m & (d.z <= thr)
                r = fwd[sel] - COST
                qq = r.groupby(q[sel]).mean()
                out.append(f'하위{pct:.0%}: n={sel.sum()} 순={r.mean():+.3f}% t={r.mean() / r.std() * np.sqrt(sel.sum()):+.1f} 분기+={int((qq > 0).sum())}/{len(qq)}')
            print(f'  lag{lag} h={h:2d} IC={ic:+.3f} | ' + ' | '.join(out))
