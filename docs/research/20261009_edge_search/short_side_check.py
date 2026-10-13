"""IC 일관 신호들의 롱/숏 쪽 순수익 비교 (겹침 없음, 과거 60일 문턱, 1분 지연, 비용 0.03).
숏 = 122630 하락 수익. 실제 구현은 선물 매도 등이 필요 — '숏이 가능하다면' 상한 확인용."""
import numpy as np, pandas as pd

d = pd.read_csv('DataVault/features/122630/data_snapshot_1m.csv', usecols=['timestamp', 'close', 'ref_10100_close', 'ref_10100_volume'])
d['ts'] = pd.to_datetime(d['timestamp']); d = d.set_index('ts').sort_index()
us = pd.read_parquet('DataVault/normalized/candles_1m/US_500_1m.parquet', columns=['timestamp', 'close'])
us['ts'] = pd.to_datetime(us['timestamp']) + pd.Timedelta(minutes=2); us = us.set_index('ts')['close'].sort_index()
tm = d.index.hour * 100 + d.index.minute
d = d[(tm >= 901) & (tm <= 1519)].copy()
day = d.index.normalize(); g = d.groupby(day)
d['us'] = us.reindex(d.index, method='ffill', tolerance=pd.Timedelta(minutes=3))
usk = d['us'].reindex(d.index - pd.Timedelta(minutes=15), method='ffill', tolerance=pd.Timedelta(minutes=3)).values

def surge(c, k, tmv):
    s = d.groupby(day)[c].transform(lambda x: x.rolling(k).sum())
    return np.log(s / s.groupby(tmv).transform(lambda x: x.rolling(20, min_periods=10).mean().shift(1)))
tmv = d.index.hour * 100 + d.index.minute
sigs = {
    'fut_lead5': g['ref_10100_close'].pct_change(5) * 2 - g['close'].pct_change(5),
    'fut_momvol15': g['ref_10100_close'].pct_change(15) * np.exp(surge('ref_10100_volume', 15, tmv).clip(-3, 3)),
    'us500_15': pd.Series(d['us'].values / usk - 1, index=d.index),
}

def bt(sig, h, p, side, cost=0.03):
    df = pd.DataFrame({'c': d.close, 's': sig * side}); df['tm'] = tmv; df['day'] = day
    ent = (df.tm >= 910) & (df.tm <= 1500)
    hist, thr = [], {}
    for dy, grp in df[ent].groupby('day'):
        if len(hist) >= 20: thr[dy] = np.nanquantile(np.concatenate(hist[-60:]), 1 - p)
        hist.append(grp['s'].replace([np.inf, -np.inf], np.nan).dropna().values)
    out = []
    for dy, grp in df.groupby('day'):
        if dy not in thr: continue
        c, t, s = grp.c.values, grp.tm.values, grp.s.values; n = len(c)
        fe = np.where(t >= 1518)[0]; xmax = fe[0] if len(fe) else n - 1
        i = 0
        while i < n - 1:
            if 910 <= t[i] <= 1500 and np.isfinite(s[i]) and s[i] >= thr[dy]:
                e = i + 1; x = min(e + h, xmax)
                if x <= e: break
                out.append((grp.index[e], side * (c[x] / c[e] - 1) * 100 - cost)); i = x + 1
            else: i += 1
    r = pd.Series(dict(out)).sort_index()
    hy = r.groupby([r.index.year, (r.index.month - 1) // 6]).mean()
    return f'n={len(r):5d} 순={r.mean():+.3f}% t={r.mean() / r.std() * np.sqrt(len(r)):+.2f} 반기양={int((hy > 0).sum())}/{len(hy)}'

for name, s in sigs.items():
    for h in [6, 15, 30]:
        print(f'{name:<13} h={h:2d} | 롱(상위5%) {bt(s, h, 0.05, +1)} | 숏(하위5%) {bt(s, h, 0.05, -1)}')
