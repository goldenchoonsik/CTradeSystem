"""반도체 괴리(semidiv) 실전형 검증.
신호 s = (하이닉스 k분 수익 + 삼성 k분 수익)/2 - U180 k분 수익.
문턱 = 과거 60거래일 신호의 상위 p 분위 (미래 미사용, 매일 갱신). 진입 = 신호 다음 봉 종가, h분 보유, 15:18 이전 강제청산, 재진입은 청산 후.
"""
import numpy as np, pandas as pd

cols = ['timestamp', 'close', 'ref_u180_close', 'ref_000660_close', 'ref_005930_close']
d = pd.read_csv('DataVault/features/122630/data_snapshot_1m.csv', usecols=cols)
d['ts'] = pd.to_datetime(d['timestamp']); d = d.set_index('ts').sort_index()
tm = d.index.hour * 100 + d.index.minute
d = d[(tm >= 901) & (tm <= 1519)].copy()
d['tm'] = d.index.hour * 100 + d.index.minute
d['day'] = d.index.normalize()

def run(k, h, p, cost=0.03, d=d, verbose=False):
    g = d.groupby('day')
    s = ((g['ref_000660_close'].pct_change(k) + g['ref_005930_close'].pct_change(k)) / 2 - g['ref_u180_close'].pct_change(k)) * 100
    ent = (d.tm >= 910) & (d.tm <= 1500)
    days = d.day.unique()
    # 일별 문턱: 직전 60거래일 진입가능 신호의 (1-p) 분위
    daily_s = s[ent].groupby(d.day[ent])
    thr = {}
    hist = []
    for dy in days:
        if len(hist) >= 20:
            thr[dy] = np.nanquantile(np.concatenate(hist[-60:]), 1 - p)
        if dy in daily_s.groups:
            hist.append(daily_s.get_group(dy).dropna().values)
    trades = []
    for dy, grp in d.groupby('day'):
        if dy not in thr: continue
        c = grp['close'].values; t = grp['tm'].values; sv = s.loc[grp.index].values
        i = 0; n = len(c)
        while i < n - 1:
            if 910 <= t[i] <= 1500 and np.isfinite(sv[i]) and sv[i] >= thr[dy]:
                e = i + 1
                x = e + h
                # 15:18 강제청산
                fe = np.where(t >= 1518)[0]; xmax = fe[0] if len(fe) else n - 1
                x = min(x, xmax, n - 1)
                if x <= e: break
                trades.append((grp.index[e], (c[x] / c[e] - 1) * 100 - cost))
                i = x + 1
            else:
                i += 1
    r = pd.Series(dict(trades)).sort_index()
    return r

print('== 파라미터 격자 (겹침 없는 실제 매매, 과거 문턱) ==')
res = []
for k in [10, 15, 20, 30]:
    for h in [20, 30, 45, 60]:
        for p in [0.05, 0.10]:
            r = run(k, h, p)
            hy = r.groupby([r.index.year, (r.index.month - 1) // 6]).mean()
            res.append(dict(k=k, h=h, p=p, n=len(r), 순평균=round(r.mean(), 3), t=round(r.mean() / r.std() * np.sqrt(len(r)), 2),
                            승률=round((r > 0).mean(), 2), 반기양=f'{int((hy > 0).sum())}/{len(hy)}', 합=round(r.sum(), 1)))
R = pd.DataFrame(res); print(R.to_string(index=False))
print('\n양(+) 비율:', (R.순평균 > 0).mean().round(2), '| t>2 비율:', (R.t > 2).mean().round(2))
