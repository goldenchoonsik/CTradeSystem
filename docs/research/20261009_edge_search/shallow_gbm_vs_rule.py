"""일 단위: 얕은 LightGBM vs 규칙(변동성목표+추세필터). K200 선물 10년 일봉, 2배 상품 기준, 전날 정보로 노출 결정.
GBM: 다음날 수익 회귀, 직전 750거래일 학습·매월 재학습. 노출 매핑 2가지:
  (a) 게이트: L = clip(0.25/vol20,0,2) * [pred>0]  — 규칙의 추세필터 자리를 GBM 이 대체
  (b) 단독:   L = 2 * [pred>0]
비교 구간 = GBM 예측이 존재하는 날(OOS)만, 규칙도 같은 구간으로.
"""
import numpy as np, pandas as pd, lightgbm as lgb

f = pd.read_parquet('DataVault/normalized/macro/10100_1m.parquet')
f['ts'] = pd.to_datetime(f['timestamp']); f = f.set_index('ts').sort_index()
tm = f.index.hour * 100 + f.index.minute
c = f[tm == 1530]['close']; c.index = c.index.normalize()
r = c.pct_change()
def second_thu(y, m):
    x = pd.Timestamp(y, m, 1); return x + pd.Timedelta(days=(3 - x.weekday()) % 7 + 7)
for y in range(2016, 2027):
    for m in (3, 6, 9, 12):
        d0 = second_thu(y, m); nx = r.index[r.index > d0]
        if len(nx): r.loc[nx[0]] = 0.0
r = r.dropna().clip(-0.15, 0.15); px = (1 + r).cumprod()   # 점프 제거된 가격

X = pd.DataFrame(index=r.index)
for k in [5, 20, 60, 120]: X[f'mom{k}'] = px.pct_change(k)
X['dist_ma120'] = px / px.rolling(120).mean() - 1
X['vol20'] = r.rolling(20).std() * np.sqrt(250)
X['vol_ratio'] = X.vol20 / (r.rolling(120).std() * np.sqrt(250))
X['dd_1y'] = px / px.rolling(250).max() - 1
X['dow'] = r.index.dayofweek
y = r.shift(-1)                      # 다음날 수익 (X 는 t 종가까지 정보)

pred = pd.Series(np.nan, index=r.index)
months = pd.PeriodIndex(r.index, freq='M')
for depth in [1, 2]:
    p = pd.Series(np.nan, index=r.index)
    for m in months.unique():
        te = (months == m)
        first = np.where(te)[0][0]
        tr_idx = np.arange(max(0, first - 750), first - 1)   # 마지막 1일은 타깃 미확정이라 제외
        if len(tr_idx) < 750 - 1: continue
        Xt, yt = X.iloc[tr_idx], y.iloc[tr_idx]
        ok = Xt.notna().all(1) & yt.notna()
        mdl = lgb.train(dict(objective='regression', learning_rate=0.03, num_leaves=2 ** depth, max_depth=depth,
                             min_data_in_leaf=100, lambda_l2=10, feature_fraction=0.8, verbose=-1, seed=1),
                        lgb.Dataset(Xt[ok], yt[ok].clip(-0.05, 0.05)), num_boost_round=100)
        p[te] = mdl.predict(X[te])
    globals()[f'pred_d{depth}'] = p

vt = (0.25 / X.vol20).clip(0, 2)
trend = (px > px.rolling(120).mean()).astype(float)
oos = pred_d1.notna()

def perf(L, name):
    L = L.fillna(0).clip(0, 2)
    ret = (L * y - L.diff().abs().fillna(0) * 0.0003)[oos].dropna()   # L(t) 로 t+1 수익
    eq = (1 + ret).cumprod(); yrs = len(ret) / 250
    cagr = eq.iloc[-1] ** (1 / yrs) - 1; vol = ret.std() * np.sqrt(250); mdd = (1 - eq / eq.cummax()).max()
    yr = ret.groupby(ret.index.year).apply(lambda x: (1 + x).prod() - 1)
    print(f'{name:<38} CAGR={cagr:+6.1%} 샤프={cagr / vol:+.2f} MDD={mdd:5.1%} 노출={L[oos].mean():.2f} 최악연도={yr.min():+.0%}')

print(f'OOS 구간: {r.index[oos][0].date()} ~ {r.index[oos][-1].date()} ({oos.sum()}일)')
perf(pd.Series(2.0, index=r.index), '2배 그냥 보유')
perf(vt * trend, '규칙: 변동성목표 + 추세필터')
for dpt in [1, 2]:
    p = globals()[f'pred_d{dpt}']
    perf(vt * (p > 0), f'GBM depth{dpt} 게이트 (변동성목표 x [예측>0])')
    perf(2 * (p > 0).astype(float), f'GBM depth{dpt} 단독 (2배 x [예측>0])')
    perf(vt * trend * (p > 0), f'규칙 + GBM depth{dpt} 추가 필터')
    hit = ((p > 0) == (y > 0))[oos & y.notna()].mean()
    print(f'   depth{dpt} 방향 적중률 {hit:.1%} | 예측>0 비율 {(p[oos] > 0).mean():.0%}')
