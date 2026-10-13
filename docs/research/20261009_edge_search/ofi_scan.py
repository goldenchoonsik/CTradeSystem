"""주문흐름 불균형(OFI) 스캔. Creon raw 1m 의 cvd = 당일 누적(매수주도-매도주도 체결수량) → 봉별 흐름 = 당일 내 diff.
OFI_k = sum(Δcvd, k봉) / sum(volume, k봉).  bar_close 라벨 → 봉 t 값은 t 에 확정.
타깃 122630 (2년, 순수익): 진입 = 신호 다음 봉 종가, h분 보유, 겹침 없음, 15:18 강제청산, 문턱 = 직전 60일 분위(미래 미사용).
선물 10년: 선물 OFI → 선물 자체 수익 x2 - 0.03 (같은 방식) — 장기 일관성 확인.
"""
import glob, numpy as np, pandas as pd

def load_raw(sym, start=None):
    fs = sorted(glob.glob(f'DataVault/raw/creon/candle_1m/A{sym}/*.csv'))
    if start: fs = [f for f in fs if f[-14:-4] >= start]
    d = pd.concat([pd.read_csv(f, usecols=['timestamp', 'upbit_close', 'upbit_volume', 'cvd']) for f in fs])
    d['ts'] = pd.to_datetime(d['timestamp']); d = d.set_index('ts').sort_index()
    d = d[~d.index.duplicated()]
    day = d.index.normalize()
    d['flow'] = d.groupby(day)['cvd'].diff().fillna(d['cvd'])
    d = d.rename(columns={'upbit_close': 'close', 'upbit_volume': 'vol'})
    return d[['close', 'vol', 'flow']]

def ofi(d, k):
    day = d.index.normalize(); g = d.groupby(day)
    f = g['flow'].transform(lambda s: s.rolling(k).sum()); v = g['vol'].transform(lambda s: s.rolling(k).sum())
    return f / v.replace(0, np.nan)

def backtest(close, sig, h, p, side, mult=1.0, cost=0.03):
    """sig 상위(side=+1) 또는 하위(side=-1) p 분위에서 롱. 겹침 없음."""
    df = pd.DataFrame({'c': close, 's': sig * side}).dropna(subset=['c'])
    tm = df.index.hour * 100 + df.index.minute
    df = df[(tm >= 901) & (tm <= 1519)]
    df['tm'] = df.index.hour * 100 + df.index.minute; df['day'] = df.index.normalize()
    ent = (df.tm >= 910) & (df.tm <= 1500)
    hist, thr = [], {}
    for dy, grp in df[ent].groupby('day'):
        if len(hist) >= 20: thr[dy] = np.nanquantile(np.concatenate(hist[-60:]), 1 - p)
        hist.append(grp['s'].dropna().values)
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
                out.append((grp.index[e], (c[x] / c[e] - 1) * 100 * mult - cost)); i = x + 1
            else: i += 1
    return pd.Series(dict(out)).sort_index()

def summ(r):
    if len(r) < 30: return 'n<30'
    hy = r.groupby([r.index.year, (r.index.month - 1) // 6]).mean()
    return f'n={len(r):5d} 순={r.mean():+.3f}% t={r.mean() / r.std() * np.sqrt(len(r)):+.2f} 승률={(r > 0).mean():.0%} 반기양={int((hy > 0).sum())}/{len(hy)}'

etf = load_raw('122630'); kodex = load_raw('069500'); fut = load_raw('10100')
hyn = load_raw('000660'); sam = load_raw('005930')
fr = (etf.flow / etf.vol.replace(0, np.nan)).dropna()
print('122630 봉별 흐름/거래량 분포:', fr.describe()[['mean', '25%', '50%', '75%']].round(3).to_dict(), '| |값|=1 비율', round((fr.abs() > 0.999).mean(), 3))

idx = etf.index
srcs = {'etf': etf, '069500': kodex, 'fut': fut, 'hynix': hyn, 'samsung': sam}
print('\n=== 122630 타깃 (2년) — 신호원별 OFI ===')
for name, src in srcs.items():
    for k in [5, 15, 30]:
        s = ofi(src, k).reindex(idx)
        for h in [10, 30]:
            line = []
            for side in [+1, -1]:
                r = backtest(etf.close, s, h, 0.05, side)
                line.append(('추종' if side > 0 else '역행') + ' ' + summ(r))
            print(f'  {name:<8} k={k:2d} h={h:2d} | ' + ' | '.join(line))
