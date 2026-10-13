"""장중 '날 유형' 패턴 (롱 전용, 당일 청산 15:18, 하루 1회). 합격 = 122630 순>0·t>2 AND 선물10y(2배 환산 순) >0·t>2·양수연도 ≥70%.
A ORB: 09:01~09:30 고가를 S 이후 처음 돌파하면 다음 봉 매수 → 15:18 청산 (돌파 시각 ≤ 13:00)
B 오전추세 지속: 시가→T 수익 > thr 이면 T+1 매수 → 15:18
C 장중 모멘텀(Gao): 전일종가→09:30 수익 > thr 이면 14:50 매수 → 15:18 (마지막 구간)
"""
import numpy as np, pandas as pd

def piv_from(d):
    d = d.copy(); d['day'] = d.index.normalize(); d['tm'] = d.index.hour * 100 + d.index.minute
    d = d[(d.tm >= 901) & (d.tm <= 1530)]
    return d

e = pd.read_csv('DataVault/features/122630/data_snapshot_1m.csv', usecols=['timestamp', 'high', 'close'])
e['ts'] = pd.to_datetime(e['timestamp']); e = piv_from(e.set_index('ts').sort_index())
f = pd.read_parquet('DataVault/normalized/macro/10100_1m.parquet')
f['ts'] = pd.to_datetime(f['timestamp']); f = f.set_index('ts').sort_index(); f['high'] = f['close']  # 선물 high 없음 → 종가 기준
f = piv_from(f)
def second_thu(y, m):
    x = pd.Timestamp(y, m, 1); return x + pd.Timedelta(days=(3 - x.weekday()) % 7 + 7)
days_f = f['day'].unique(); ex = set()
for y in range(2016, 2027):
    for m in (3, 6, 9, 12):
        d0 = second_thu(y, m); ex.add(d0); nx = days_f[days_f > d0]
        if len(nx): ex.add(nx[0])
f = f[~f['day'].isin(list(ex))]

def daily(d, rule, **kw):
    out = {}
    prev = d.groupby('day')['close'].last().shift(1)
    for dy, g in d.groupby('day'):
        c, h, t = g.close.values, g.high.values, g.tm.values
        ex_i = np.where(t >= 1518)[0]
        if not len(ex_i): continue
        xi = ex_i[0]
        ent = None
        if rule == 'ORB':
            orw = t <= kw['orend']
            if orw.sum() < 5: continue
            orh = h[orw].max()
            for i in np.where((t > kw['orend']) & (t <= 1300))[0]:
                if c[i] > orh * (1 + kw['buf']): ent = i + 1; break
        elif rule == 'AMTREND':
            j = np.where(t == kw['T'])[0]
            if len(j) and (c[j[0]] / c[0] - 1) * 100 > kw['thr']: ent = j[0] + 1
        elif rule == 'GAO':
            j = np.where(t == 930)[0]; k = np.where(t == 1450)[0]
            pc = prev.get(dy, np.nan)
            if len(j) and len(k) and np.isfinite(pc) and (c[j[0]] / pc - 1) * 100 > kw['thr']: ent = k[0] + 1
        if ent is not None and ent < xi:
            out[dy] = (c[xi] / c[ent] - 1) * 100
    return pd.Series(out)

def ev(name, **kw):
    a = daily(e, **kw) - 0.03
    b = daily(f, **kw) * 2 - 0.03
    def st(x): return len(x), x.mean(), x.mean() / x.std() * np.sqrt(len(x)) if len(x) > 2 else np.nan
    n1, m1, t1 = st(a); n2, m2, t2 = st(b)
    yr = b.groupby(pd.to_datetime(b.index).year).mean(); py = (yr > 0).mean()
    ok = m1 > 0 and t1 > 2 and m2 > 0 and t2 > 2 and py >= 0.7
    print(f"{'✅' if ok else '  '} {name:<30} 122630 n={n1:3d} 순={m1:+.3f}% t={t1:+.2f} | 선물10y(2x) n={n2:4d} 순={m2:+.3f}% t={t2:+.2f} 양수연도={int((yr > 0).sum())}/{len(yr)}")

for orend in [930, 1000]:
    for buf in [0.0, 0.002]:
        ev(f'ORB {orend} 돌파+{buf:.1%}', rule='ORB', orend=orend, buf=buf)
for T in [1030, 1100, 1300]:
    for thr in [0.5, 1.0, 2.0]:
        ev(f'오전추세 시가→{T} >{thr}%', rule='AMTREND', T=T, thr=thr)
for thr in [0.0, 0.5, 1.0]:
    ev(f'Gao 09:30까지 >{thr}% →14:51~', rule='GAO', thr=thr)
