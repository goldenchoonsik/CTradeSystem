"""122630 1m 약신호 결합 — 사전 등록 8개 설정, 월별 전진 재학습, 실매매형 백테스트.
타깃 = 다음 봉(t+1) 종가 진입 → min(t+1+h, 15:18 봉) 종가 청산 수익(%). 롱 전용, 비용 0.03%.
학습: 직전 120거래일(진입창 09:10~15:00 행), 매월 1회 재학습, 다음 달 예측.
"""
import glob, numpy as np, pandas as pd, lightgbm as lgb


class StandardScaler:
    def fit(self, X):
        X = np.asarray(X, float); self.m = X.mean(0); self.s = X.std(0); self.s[self.s == 0] = 1; return self
    def transform(self, X): return (np.asarray(X, float) - self.m) / self.s

class Ridge:
    def __init__(self, alpha): self.a = alpha
    def fit(self, X, y):
        y = np.asarray(y, float); self.b = y.mean(); n, k = X.shape
        self.w = np.linalg.solve(X.T @ X + self.a * np.eye(k), X.T @ (y - self.b)); return self
    def predict(self, X): return X @ self.w + self.b

COST = 0.03
# ---------- 데이터 ----------
cols = ['timestamp', 'close', 'ref_10100_close', 'ref_10100_volume', 'ref_u180_close', 'ref_000660_close', 'ref_005930_close']
d = pd.read_csv('DataVault/features/122630/data_snapshot_1m.csv', usecols=cols)
d['ts'] = pd.to_datetime(d['timestamp']); d = d.set_index('ts').sort_index().drop(columns='timestamp')
d = d[~d.index.duplicated()]

def raw_flow(sym):
    fs = sorted(glob.glob(f'DataVault/raw/creon/candle_1m/A{sym}/*.csv'))
    r = pd.concat([pd.read_csv(f, usecols=['timestamp', 'upbit_volume', 'cvd']) for f in fs if f[-14:-4] >= '2024-08-01'])
    r['ts'] = pd.to_datetime(r['timestamp']); r = r.set_index('ts').sort_index(); r = r[~r.index.duplicated()]
    r['flow'] = r.groupby(r.index.normalize())['cvd'].diff().fillna(r['cvd'])
    return r[['flow', 'upbit_volume']].rename(columns={'upbit_volume': 'vol'})
for sym, key in [('122630', 'etf'), ('10100', 'fut'), ('000660', 'hyn'), ('005930', 'sam')]:
    r = raw_flow(sym).reindex(d.index)
    d[f'{key}_flow'] = r.flow; d[f'{key}_vol'] = r.vol
def ctr(sym):
    x = pd.read_parquet(f'DataVault/normalized/candles_1m/{sym}_1m.parquet', columns=['timestamp', 'close'])
    x['ts'] = pd.to_datetime(x['timestamp']) + pd.Timedelta(minutes=2)  # bar_open → 확정 +1, 안전 +1
    return x.set_index('ts')['close'].sort_index()
ext = {k: ctr(s) for k, s in [('us', 'US_500'), ('nq', 'US_TECH_100'), ('hsi', 'HONG_KONG_50')]}

tm = d.index.hour * 100 + d.index.minute
d = d[(tm >= 901) & (tm <= 1519)].copy()
d['tm'] = d.index.hour * 100 + d.index.minute
d['day'] = d.index.normalize()
g = d.groupby('day')

# ---------- 특징 ----------
F = pd.DataFrame(index=d.index)
pc = lambda c, k: g[c].pct_change(k) * 100
for k in [5, 15, 30]:
    F[f'etf_r{k}'] = pc('close', k)
    F[f'fut_r{k}'] = pc('ref_10100_close', k)
for k in [5, 15]:
    F[f'fut_lead{k}'] = 2 * pc('ref_10100_close', k) - pc('close', k)
F['semidiv15'] = (pc('ref_000660_close', 15) + pc('ref_005930_close', 15)) / 2 - pc('ref_u180_close', 15)
prev_close = g['close'].transform('last').groupby(level=0).first() if False else None
dl = g['close'].last(); pcl = dl.shift(1)
F['day_ret'] = (d['close'] / d['day'].map(pcl) - 1) * 100
F['from_open'] = (d['close'] / g['close'].transform('first') - 1) * 100
F['ma15'] = (d['close'] / g['close'].transform(lambda s: s.rolling(15).mean()) - 1) * 100
F['ma60'] = (d['close'] / g['close'].transform(lambda s: s.rolling(60).mean()) - 1) * 100
for key in ['etf', 'fut', 'hyn', 'sam']:
    for k in [5, 15]:
        f = g[f'{key}_flow'].transform(lambda s: s.rolling(k).sum()); v = g[f'{key}_vol'].transform(lambda s: s.rolling(k).sum())
        F[f'{key}_ofi{k}'] = f / v.replace(0, np.nan)
for key, x in ext.items():
    cur = x.reindex(d.index, method='ffill', tolerance=pd.Timedelta(minutes=3)).values
    for k in [15, 30]:
        past = x.reindex(d.index - pd.Timedelta(minutes=k), method='ffill', tolerance=pd.Timedelta(minutes=3)).values
        F[f'{key}_r{k}'] = (cur / past - 1) * 100
F['tod'] = (d.index.hour - 9) * 60 + d.index.minute
r1 = g['close'].pct_change() * 100
F['rv_today'] = r1.groupby(d['day']).transform(lambda s: s.expanding().std())
daily_rv = r1.groupby(d['day']).std()
F['rv_20d'] = d['day'].map(daily_rv.shift(1).rolling(20).mean())   # 전일까지
F['fut_volsurge15'] = np.log(g['ref_10100_volume'].transform(lambda s: s.rolling(15).sum()) /
                             g['ref_10100_volume'].transform(lambda s: s.rolling(15).sum()).groupby(d['tm']).transform(lambda s: s.rolling(20, min_periods=10).mean().shift(1)))
F = F.replace([np.inf, -np.inf], np.nan)

# ---------- 타깃 ----------
def target(h):
    out = np.full(len(d), np.nan)
    pos = 0
    for dy, grp in d.groupby('day', sort=True):
        c = grp['close'].values; t = grp['tm'].values; n = len(c)
        fe = np.where(t >= 1518)[0]; xmax = fe[0] if len(fe) else n - 1
        e = np.arange(n) + 1; x = np.minimum(e + h, xmax)
        valid = e < x
        r = np.full(n, np.nan); r[valid] = (c[x[valid]] / c[e[valid]] - 1) * 100
        out[pos:pos + n] = r; pos += n
    return pd.Series(out, index=d.index)

ent = ((d.tm >= 910) & (d.tm <= 1500)).values
days = np.array(sorted(d['day'].unique()))
months = pd.PeriodIndex(days, freq='M').unique()

def walk_forward(h, model):
    y = target(h)
    pred = pd.Series(np.nan, index=d.index); thr5 = {}
    for m in months:
        mdays = days[pd.PeriodIndex(days, freq='M') == m]
        tr_days = days[days < mdays[0]][-120:]
        if len(tr_days) < 120: continue
        trm = d['day'].isin(tr_days).values & ent & y.notna().values
        tem = d['day'].isin(mdays).values & ent
        X, Y = F[trm], y[trm]
        keep = X.columns[X.notna().mean() > 0.5]; X = X[keep]; med = X.median()
        Xf = X.fillna(med); Yc = Y.clip(Y.quantile(0.005), Y.quantile(0.995))
        if model == 'ridge':
            sc = StandardScaler().fit(Xf); mdl = Ridge(alpha=1000.0).fit(np.nan_to_num(sc.transform(Xf)), Yc)
            p_tr = mdl.predict(np.nan_to_num(sc.transform(Xf))); p = mdl.predict(np.nan_to_num(sc.transform(F[tem][keep].fillna(med))))
        else:
            mdl = lgb.train(dict(objective='regression', learning_rate=0.03, num_leaves=15, min_data_in_leaf=500,
                                 feature_fraction=0.7, bagging_fraction=0.7, bagging_freq=1, lambda_l2=10, verbose=-1, seed=7),
                            lgb.Dataset(X, Yc), num_boost_round=200)
            p_tr = mdl.predict(X); p = mdl.predict(F[tem][keep])
        pred[tem] = p
        thr5[m] = np.quantile(p_tr, 0.95)
    return y, pred, thr5

def trade(pred, rule, thr5, h):
    out = []
    for dy, grp in d.groupby('day'):
        pr = pred.loc[grp.index].values
        if np.all(np.isnan(pr)): continue
        m = pd.Period(dy, freq='M')
        c = grp['close'].values; t = grp['tm'].values; n = len(c)
        fe = np.where(t >= 1518)[0]; xmax = fe[0] if len(fe) else n - 1
        th = (COST + 0.0) if rule == 'pred>cost' else thr5.get(m, np.inf)
        i = 0
        while i < n - 1:
            if 910 <= t[i] <= 1500 and np.isfinite(pr[i]) and pr[i] > th:
                e = i + 1; x = min(e + h, xmax)
                if x <= e: break
                out.append((grp.index[e], (c[x] / c[e] - 1) * 100 - COST)); i = x + 1
            else: i += 1
    return pd.Series(dict(out)).sort_index()

import sys as _s; MODELS = _s.argv[1].split(',') if len(_s.argv) > 1 else ['ridge', 'lgbm']
print('특징 수:', F.shape[1], '| 기간', d.index[0].date(), '~', d.index[-1].date())
for h in [120, 240]:
    for model in MODELS:
        y, pred, thr5 = walk_forward(h, model)
        m = pred.notna() & y.notna()
        ic_m = pd.Series({p: np.corrcoef(pred[m & (pd.PeriodIndex(d.index, freq='Q') == p)].rank(), y[m & (pd.PeriodIndex(d.index, freq='Q') == p)].rank())[0, 1]
                          for p in pd.PeriodIndex(d.index[m], freq='Q').unique()})
        for rule in ['pred>cost', 'top5%']:
            r = trade(pred, rule, thr5, h)
            if len(r) < 20:
                print(f'h={h} {model:<5} {rule:<9} | 거래 {len(r)}건 — 판단불가 | OOS IC {ic_m.mean():+.3f} ({int((ic_m > 0).sum())}/{len(ic_m)}분기+)'); continue
            hy = r.groupby([r.index.year, (r.index.month - 1) // 6]).mean()
            print(f'h={h} {model:<5} {rule:<9} | n={len(r):4d} 순={r.mean():+.3f}% t={r.mean() / r.std() * np.sqrt(len(r)):+.2f} 승률={(r > 0).mean():.0%} '
                  f'합={r.sum():+.1f}% 반기양={int((hy > 0).sum())}/{len(hy)} [{" ".join(f"{v:+.2f}" for v in hy.values)}] | OOS IC {ic_m.mean():+.3f} ({int((ic_m > 0).sum())}/{len(ic_m)}분기+)')
