"""122630 장 후반 하락일 반전(롱 전용) 엄격 검증.
보수 가정: 신호는 T0 봉 종가로 계산, 진입은 T0+1 봉 종가(1분 지연), 청산 15:19 봉 종가 또는 15:30 동시호가 종가.
"""
import numpy as np, pandas as pd

COST = 0.03
df = pd.read_csv('DataVault/features/122630/data_snapshot_1m.csv', usecols=['timestamp', 'close', 'volume', 'ref_10100_close'])
df['ts'] = pd.to_datetime(df['timestamp']); df = df.set_index('ts').sort_index()
df['day'] = df.index.date
tm = df.index.hour * 100 + df.index.minute
df['tm'] = tm

# 데이터 점검: 장 후반 분 존재율·거래량
days = df['day'].nunique()
late = df[df.tm >= 1505]
print('일수', days, '| 분별 존재일수:', late.groupby('tm').size().to_dict())
print('분별 평균 거래량:', late.groupby('tm').volume.mean().round(0).to_dict())

piv = df.pivot_table(index='day', columns='tm', values='close')
prev_close = piv[1530].shift(1) if 1530 in piv else piv.iloc[:, -1].shift(1)
day_open = piv[901]


def test(t0, t_exit, sig_kind='prev', lag=1, thr_list=(0, -0.01, -0.02, -0.03), label=''):
    def col(hhmm):
        return piv[hhmm] if hhmm in piv else None
    p0 = col(t0)
    # 진입 = t0 + lag 분
    h, m = divmod(t0, 100); m += lag; h += m // 60; m %= 60
    pe = col(h * 100 + m); px = col(t_exit)
    base = prev_close if sig_kind == 'prev' else day_open
    d = pd.DataFrame({'sig': p0 / base - 1, 'out': (px / pe - 1) * 100}).dropna()
    d = d[d.sig.abs() < 0.15]
    d.index = pd.to_datetime(d.index)
    res = []
    for thr in thr_list:
        x = d[d.sig < thr].out - COST
        if len(x) < 10: continue
        t = x.mean() / x.std() * np.sqrt(len(x))
        hy = x.groupby([x.index.year, (x.index.month - 1) // 6]).mean()
        res.append(f"  sig<{thr:+.0%}: n={len(x)} 순평균={x.mean():+.3f}% t={t:+.2f} 승률={(x > 0).mean():.0%} "
                   f"반기양수={int((hy > 0).sum())}/{len(hy)} [{' '.join(f'{v:+.2f}' for v in hy.values)}]")
    print(f"\n[{label}{t0}→진입+{lag}분→{t_exit}, 기준={sig_kind}] 상관(전체)={np.corrcoef(d.sig, d.out)[0, 1]:+.3f}")
    print('\n'.join(res))


print('\n===== 본 가설: 하락일 장 후반 반전 =====')
for t0 in [1500, 1505, 1510, 1515]:
    test(t0, 1519)
test(1510, 1530, label='(동시호가 청산) ')
test(1510, 1519, sig_kind='open', label='(시가기준 신호) ')
test(1510, 1519, lag=0, label='(지연 없음 참고) ')

print('\n===== 대조군: 같은 9분·같은 조건, 다른 시각 (반전이 장마감 특유인지) =====')
for t0, tx in [(1010, 1019), (1110, 1119), (1310, 1319), (1410, 1419), (1440, 1449)]:
    test(t0, tx, thr_list=(-0.01, -0.02))
