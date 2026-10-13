"""고정 규칙(사전 확정, 문턱 튜닝 없음) 견고성:
규칙: 신호시각 S 의 종가가 전일 종가보다 낮으면(sig<0) S+1분 종가에 매수, 15:30 동시호가 종가에 청산. 하루 1회, 오버나잇 없음.
"""
import numpy as np, pandas as pd

df = pd.read_csv('DataVault/features/122630/data_snapshot_1m.csv', usecols=['timestamp', 'close'])
df['ts'] = pd.to_datetime(df['timestamp']); df = df.set_index('ts').sort_index()
df['day'] = df.index.date; df['tm'] = df.index.hour * 100 + df.index.minute
piv = df.pivot_table(index='day', columns='tm', values='close')
piv.index = pd.to_datetime(piv.index)
prev_close = piv[1530].shift(1)


def run(S, cost=0.03, exit_t=1530, thr=0.0):
    e = S + 1 if S % 100 != 59 else S + 41
    d = pd.DataFrame({'sig': piv[S] / prev_close - 1, 'ret': (piv[exit_t] / piv[e] - 1) * 100}).dropna()
    d = d[(d.sig.abs() < 0.15) & (d.sig < thr)]
    return d.ret - cost


print('== 신호 시각 흔들기 (cost 0.03) ==')
for S in [1505, 1506, 1507, 1508, 1509, 1510, 1511, 1512, 1513, 1514, 1515, 1517]:
    x = run(S)
    print(f'  {S}: n={len(x)} 평균={x.mean():+.3f}% t={x.mean() / x.std() * np.sqrt(len(x)):+.2f} 승률={(x > 0).mean():.0%}')

x = run(1510)
print('\n== 비용 민감도 (S=1510) ==')
for c in [0.0, 0.03, 0.05, 0.08, 0.12]:
    y = run(1510, cost=c)
    print(f'  왕복 {c:.2f}%: 평균={y.mean():+.3f}% t={y.mean() / y.std() * np.sqrt(len(y)):+.2f}')

print('\n== 월별 손익 (S=1510, 단리 합 %) ==')
mo = x.groupby(x.index.to_period('M')).agg(['count', 'sum'])
print(mo.round(2).T.to_string())
print(f'  양수 월 {int((mo["sum"] > 0).sum())}/{len(mo)}')
cum = x.cumsum(); mdd = (cum.cummax() - cum).max()
print(f'  누적 단리 {cum.iloc[-1]:+.2f}% / MDD {mdd:.2f}%p / 연환산 거래수 {len(x) / ((x.index[-1] - x.index[0]).days / 365):.0f}')
print(f'  최대 손실 거래 {x.min():+.2f}% · 최대 이익 {x.max():+.2f}% · 상위 5건 제외 평균 {x.drop(x.nlargest(5).index).mean():+.3f}%')

print('\n== 기간 분할 ==')
for a, b in [('2024-08-30', '2025-06-30'), ('2025-07-01', '2026-03-31'), ('2026-04-01', '2026-10-08'),
             ('2026-09-07', '2026-10-08')]:
    y = x[a:b]
    print(f'  {a}~{b}: n={len(y)} 평균={y.mean():+.3f}% 합={y.sum():+.2f}% 승률={(y > 0).mean():.0%}')

# 변동성 정규화: 수익이 변동성 큰 2026에만 몰린 건지 — 당일 변동성(09:10~15:10 1분 수익 표준편차) 대비
r1 = df['close'].groupby(df['day']).pct_change()
vol = r1[(df.tm >= 910) & (df.tm <= 1510)].groupby(df['day'][(df.tm >= 910) & (df.tm <= 1510)]).std() * 100
vol.index = pd.to_datetime(vol.index)
z = (x / vol.reindex(x.index)).dropna()
print('\n== 변동성 정규화 수익(거래수익/당일 1분 σ) 기간별 평균 ==')
for a, b in [('2024-08-30', '2025-06-30'), ('2025-07-01', '2026-03-31'), ('2026-04-01', '2026-10-08')]:
    print(f'  {a}~{b}: {z[a:b].mean():+.3f}')
