"""K200 선물 10년(2016-06~2026-10) 장마감 하락일 반전 검증.
규칙(122630 에서 찾은 것 그대로, 사후 수정 없음): sig = F(S)/F(전일 마지막봉) - 1 < 0 이면 F(S+1) 매수 → F(15:30) 청산.
122630 환산 = 선물수익 x2 - 0.03%(122630 왕복비용). 분기 만기일(3/6/9/12월 둘째 목)과 그 다음 거래일 제외(연속월물 교체 점프).
"""
import numpy as np, pandas as pd

d = pd.read_parquet('DataVault/normalized/macro/10100_1m.parquet')
d['ts'] = pd.to_datetime(d['timestamp']); d = d.set_index('ts').sort_index()
d['day'] = d.index.normalize(); d['tm'] = d.index.hour * 100 + d.index.minute
piv = d.pivot_table(index='day', columns='tm', values='close')
last = d.groupby('day')['close'].last()
prev = last.shift(1)

days = piv.index
def second_thu(y, m):
    first = pd.Timestamp(y, m, 1); off = (3 - first.weekday()) % 7
    return first + pd.Timedelta(days=off + 7)
exp = {second_thu(y, m) for y in range(2016, 2027) for m in (3, 6, 9, 12)}
excl = set()
for e in exp:
    nxt = days[days > e]
    excl.add(e)
    if len(nxt): excl.add(nxt[0])
ok = ~days.isin(list(excl))
# 정규 장(15:30 봉 존재) 만
ok &= piv.get(1530).notna().values


def run(S, X=1530, thr=0.0, sign=-1):
    e = S + 1
    sig = piv[S] / prev - 1
    out = (piv[X] / piv[e] - 1) * 100
    df = pd.DataFrame({'sig': sig, 'out': out})[ok].dropna()
    df = df[df.sig.abs() < 0.08]
    sel = df[df.sig < thr] if sign < 0 else df[df.sig > thr]
    return df, sel.out


print('== 신호시각별 (하락일 sig<0, 선물 1배 그로스 / 122630 환산 순) ==')
for S in [1400, 1440, 1500, 1505, 1508, 1510, 1512, 1514, 1516]:
    df, x = run(S)
    y = x * 2 - 0.03
    print(f'  {S}: n={len(x)} 선물평균={x.mean():+.4f}% t={x.mean() / x.std() * np.sqrt(len(x)):+.2f} | 122630환산 순={y.mean():+.3f}% t={y.mean() / y.std() * np.sqrt(len(y)):+.2f} 승률={(y > 0).mean():.0%} | 전체상관={np.corrcoef(df.sig, df.out)[0, 1]:+.3f}')

print('\n== S=1510 연도별 (122630 환산 순) ==')
df, x = run(1510); y = x * 2 - 0.03
yr = y.groupby(y.index.year).agg(['count', 'mean', 'sum'])
yr['t'] = y.groupby(y.index.year).apply(lambda s: s.mean() / s.std() * np.sqrt(len(s)))
print(yr.round(3).to_string())
print(f'  양수 연도 {(yr["mean"] > 0).sum()}/{len(yr)}')

print('\n== 상승일 대칭 확인 (sig>0 이면 15:11→15:30 하락? — 롱전용이라 매매엔 못 쓰지만 현상 확인) ==')
df, x = run(1510, sign=+1)
print(f'  상승일 n={len(x)} 선물평균={x.mean():+.4f}% t={x.mean() / x.std() * np.sqrt(len(x)):+.2f}')
print(x.groupby(x.index.year).mean().round(4).to_dict())

print('\n== 신호 크기 용량-반응 (S=1510, sig 5분위, 선물 그로스) ==')
df, _ = run(1510)
df['q'] = pd.qcut(df.sig, 5, labels=False)
print(df.groupby('q').agg(sig_mean=('sig', 'mean'), out_mean=('out', 'mean'), n=('out', 'size')).round(4).to_string())
