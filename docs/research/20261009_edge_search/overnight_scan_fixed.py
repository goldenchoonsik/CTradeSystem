"""오버나잇 vs 장중 분해 + 조건부 오버나잇 보유.
122630: 15:30 동시호가 종가 매수 → 다음날 09:01봉 open(=시가 동시호가) 매도. 과세: 이익분 15.4% (손실 공제 없음, 과표상승≈실이익 가정 → 보수적).
K200 선물(2016~): 15:30 봉 종가 → 다음날 09:01 봉 종가(선물 open 컬럼 없음, 1분 오차 감수). 만기 교차일 제외. 122630 환산=2배.
"""
import numpy as np, pandas as pd

e = pd.read_csv('DataVault/features/122630/data_snapshot_1m.csv', usecols=['timestamp', 'open', 'close'])
e['ts'] = pd.to_datetime(e['timestamp']); e = e.set_index('ts').sort_index()
e['day'] = e.index.normalize(); e['tm'] = e.index.hour * 100 + e.index.minute
ec = e[e.tm == 1530].set_index('day')['close']; eo = e[e.tm == 901].set_index('day')['open']
ed = pd.DataFrame({'c': ec, 'o': eo}).dropna()
ed['on'] = (ed.o / ed.c.shift(1) - 1) * 100      # 오버나잇
ed['id'] = (ed.c / ed.o - 1) * 100               # 장중
ed['dret'] = ((ed.c / ed.c.shift(1) - 1) * 100).shift(1)  # 신호=보유 시작일(전날) 등락 — 결과(오버나잇)와 겹치지 않게
ed = ed.dropna()

f = pd.read_parquet('DataVault/normalized/macro/10100_1m.parquet')
f['ts'] = pd.to_datetime(f['timestamp']); f = f.set_index('ts').sort_index()
f['day'] = f.index.normalize(); f['tm'] = f.index.hour * 100 + f.index.minute
fc = f[f.tm == 1530].set_index('day')['close']; fo = f[f.tm == 901].set_index('day')['close']
fd = pd.DataFrame({'c': fc, 'o': fo}).dropna()
fd['on'] = (fd.o / fd.c.shift(1) - 1) * 100; fd['id'] = (fd.c / fd.o - 1) * 100
fd['dret'] = ((fd.c / fd.c.shift(1) - 1) * 100).shift(1)
def second_thu(y, m):
    x = pd.Timestamp(y, m, 1); return x + pd.Timedelta(days=(3 - x.weekday()) % 7 + 7)
ex = set()
for y in range(2016, 2027):
    for m in (3, 6, 9, 12):
        d0 = second_thu(y, m); ex.add(d0); nx = fd.index[fd.index > d0]
        if len(nx): ex.add(nx[0])
fd = fd[~fd.index.isin(list(ex))].dropna()
fd = fd[(fd.on.abs() < 8) & (fd.id.abs() < 8)]

print('== 분해: 평균 % / t / 합계 ==')
for name, d in [('122630', ed), ('K200F', fd)]:
    for c in ['on', 'id']:
        x = d[c]; print(f'  {name} {"오버나잇" if c == "on" else "장중"}: n={len(x)} 평균={x.mean():+.3f}% t={x.mean() / x.std() * np.sqrt(len(x)):+.2f} 합={x.sum():+.1f}%')
yr = fd.groupby(fd.index.year)[['on', 'id']].sum().round(1)
yr['on>id'] = yr.on > yr.id
print('\n선물 연도별 합(%):'); print(yr.T.to_string())

def tax_net(g):  # 122630 오버나잇 1회 순수익(%): 비용 0.03 + 이익 시 15.4% 과세
    g = g - 0.03; return np.where(g > 0, g * (1 - 0.154), g)

print('\n== 오버나잇 보유 전략 (122630 실측, 비용·과세 반영) ==')
for name, cond in [('매일', ed.dret > -999), ('당일 하락(<0)', ed.dret < 0), ('당일 상승(>0)', ed.dret > 0),
                   ('당일 <-1%', ed.dret < -1), ('당일 >+1%', ed.dret > 1)]:
    x = pd.Series(tax_net(ed.on[cond]), index=ed.index[cond])
    yrs = x.groupby(x.index.year).mean().round(3).to_dict()
    print(f'  {name:<14} n={len(x)} 순평균={x.mean():+.3f}% t={x.mean() / x.std() * np.sqrt(len(x)):+.2f} 승률={(x > 0).mean():.0%} 연도별={yrs}')
print('  (비교) 장중 매일 09:01→15:30 순평균:', round((ed.id - 0.03).mean(), 3))

print('\n== 선물 10년 조건부 오버나잇 (그로스, 122630 환산 2배 전 수치) ==')
for name, cond in [('매일', fd.dret > -999), ('당일 하락', fd.dret < 0), ('당일 상승', fd.dret > 0), ('당일 <-1%', fd.dret < -1), ('당일 >+1%', fd.dret > 1)]:
    x = fd.on[cond]; yrs = x.groupby(x.index.year).mean()
    print(f'  {name:<10} n={len(x)} 평균={x.mean():+.4f}% t={x.mean() / x.std() * np.sqrt(len(x)):+.2f} 양수연도={(yrs > 0).sum()}/{len(yrs)}')
