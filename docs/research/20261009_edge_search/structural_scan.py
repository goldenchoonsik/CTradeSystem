"""구조 신호 일괄 스캔 — 합격 기준: 122630(2년) 순수익>0 & t>2  AND  선물 10년 그로스 동부호 & 연도 일관성.
롱 전용 규칙(122630 은 공매도 불가). 선물은 만기일/다음날 제외. 122630 비용 0.03% 왕복.
"""
import numpy as np, pandas as pd

def load_etf():
    d = pd.read_csv('DataVault/features/122630/data_snapshot_1m.csv', usecols=['timestamp', 'close'])
    d['ts'] = pd.to_datetime(d['timestamp']); return d.set_index('ts').sort_index()

def load_fut():
    d = pd.read_parquet('DataVault/normalized/macro/10100_1m.parquet')
    d['ts'] = pd.to_datetime(d['timestamp']); d = d.set_index('ts').sort_index()
    return d[d.index.hour * 100 + d.index.minute <= 1530]  # 현물 마감까지만

def pivot(d):
    d = d.copy(); d['day'] = d.index.normalize(); d['tm'] = d.index.hour * 100 + d.index.minute
    p = d.pivot_table(index='day', columns='tm', values='close')
    return p, d.groupby('day')['close'].last()

etf_p, etf_last = pivot(load_etf())
fut_p, fut_last = pivot(load_fut())
def second_thu(y, m):
    f = pd.Timestamp(y, m, 1); return f + pd.Timedelta(days=(3 - f.weekday()) % 7 + 7)
ex = set()
for y in range(2016, 2027):
    for m in (3, 6, 9, 12):
        e = second_thu(y, m); ex.add(e); nx = fut_p.index[fut_p.index > e]
        if len(nx): ex.add(nx[0])
fut_ok = ~fut_p.index.isin(list(ex))

def stat(x):
    x = x.dropna(); return len(x), x.mean(), (x.mean() / x.std() * np.sqrt(len(x)) if len(x) > 2 else np.nan)

def evaluate(name, fn):
    """fn(piv, last) -> (조건 bool Series, 결과 수익률% Series) — 같은 규칙을 두 시장에 적용"""
    c, r = fn(etf_p, etf_last); x = (r[c] - 0.03)
    n1, m1, t1 = stat(x)
    c, r = fn(fut_p, fut_last); c = c & fut_ok; y = r[c].dropna()
    n2, m2, t2 = stat(y)
    yr = y.groupby(y.index.year).mean(); same = int((np.sign(yr) == np.sign(m1 if pd.notna(m1) else 1)).sum())
    ok = (m1 > 0) and (t1 > 2) and (np.sign(m2) == 1) and (t2 > 2) and same >= 0.7 * len(yr)
    print(f"{'✅' if ok else '  '} {name:<44} 122630 n={n1:4d} 순={m1:+.3f}% t={t1:+.2f} | 선물10y n={n2:4d} 그로스={m2:+.4f}% t={t2:+.2f} 동부호연도={same}/{len(yr)}")

prev = lambda last: last.shift(1)
def ret(p, a, b): return (p[b] / p[a] - 1) * 100

print('--- 개장 갭 ---')
for thr in [-0.005, -0.01, -0.02]:
    for a, b in [(901, 930), (901, 1000), (905, 1030), (901, 1530)]:
        evaluate(f'갭<{thr:.1%} 매수 {a}->{b}', lambda p, l, a=a, b=b, thr=thr: ((p[901] / prev(l) - 1) < thr, ret(p, a, b)))
for thr in [0.005, 0.01]:
    for a, b in [(901, 930), (905, 1030)]:
        evaluate(f'갭>+{thr:.1%} 매수 {a}->{b}', lambda p, l, a=a, b=b, thr=thr: ((p[901] / prev(l) - 1) > thr, ret(p, a, b)))

print('--- 무조건 시간대 (계절성) ---')
for a, b in [(901, 930), (930, 1100), (1100, 1300), (1300, 1430), (1430, 1510), (1510, 1530), (901, 1530)]:
    evaluate(f'매일 매수 {a}->{b}', lambda p, l, a=a, b=b: (p[a].notna(), ret(p, a, b)))

print('--- 전일 하락/상승 다음날 ---')
for thr in [-0.01, -0.02, -0.03]:
    evaluate(f'전일<{thr:.0%} → 오늘 0901->1530', lambda p, l, thr=thr: ((l / l.shift(1) - 1).shift(1) < thr, ret(p, 901, 1530)))
    evaluate(f'전일<{thr:.0%} → 오늘 0901->1000', lambda p, l, thr=thr: ((l / l.shift(1) - 1).shift(1) < thr, ret(p, 901, 1000)))

print('--- 장중 급락 후 반등 (오전 하락폭 기준) ---')
for thr in [-0.01, -0.02, -0.03]:
    for a, b in [(1100, 1300), (1100, 1530), (1300, 1530)]:
        evaluate(f'시가→{a} <{thr:.0%} 매수 {a}->{b}', lambda p, l, a=a, b=b, thr=thr: ((p[a] / p[901] - 1) < thr, ret(p, a + 1 if a % 100 < 59 else a, b)))
