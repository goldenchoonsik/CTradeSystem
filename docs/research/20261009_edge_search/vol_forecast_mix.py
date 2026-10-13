"""규칙(방향) + 변동성 예측(크기) 혼용 검증. K200 선물 10년.
변동성 추정 3종으로 변동성목표 노출을 만들고, 추세필터(120일선)는 공통:
 (a) 일봉 20일 표준편차 (현재 규칙)
 (b) HAR: 1분봉 실현변동성(장중 RV + 오버나잇^2)의 1/5/22일 평균 → 다음날 RV 선형회귀(직전 750일, 월별 재추정)
 (c) 얕은 GBM(depth 2): 같은 HAR 특징 → 다음날 log RV
예측 정확도(다음날 실현변동성과의 상관, QLIKE)와 전략 성과를 비교.
"""
import numpy as np, pandas as pd, lightgbm as lgb, warnings
warnings.filterwarnings('ignore')

f = pd.read_parquet('DataVault/normalized/macro/10100_1m.parquet')
f['ts'] = pd.to_datetime(f['timestamp']); f = f.set_index('ts').sort_index()
tm = f.index.hour * 100 + f.index.minute
f = f[(tm >= 901) & (tm <= 1530)]
day = f.index.normalize()
lr = np.log(f['close']).groupby(day).diff()
rv_intra = (lr ** 2).groupby(day[lr.index.isin(lr.index)]).sum()
c = f.groupby(day)['close'].last(); o = f.groupby(day)['close'].first()
r = c.pct_change()
on = np.log(o / c.shift(1))
def second_thu(y, m):
    x = pd.Timestamp(y, m, 1); return x + pd.Timedelta(days=(3 - x.weekday()) % 7 + 7)
roll = []
for y in range(2016, 2027):
    for m in (3, 6, 9, 12):
        d0 = second_thu(y, m); nx = r.index[r.index > d0]
        if len(nx): roll.append(nx[0])
r.loc[roll] = 0.0; on.loc[roll] = 0.0
r = r.dropna().clip(-0.15, 0.15)
rv = (rv_intra.reindex(r.index) + on.reindex(r.index).fillna(0) ** 2).clip(lower=1e-8)  # 일간 분산
px = (1 + r).cumprod()

H = pd.DataFrame({'d': rv, 'w': rv.rolling(5).mean(), 'm': rv.rolling(22).mean()}, index=r.index)
logH = np.log(H)
target = np.log(rv).shift(-1)
months = pd.PeriodIndex(r.index, freq='M')
f_har = pd.Series(np.nan, index=r.index); f_gbm = pd.Series(np.nan, index=r.index)
for m in months.unique():
    te = months == m; first = np.where(te)[0][0]
    tr = np.arange(max(0, first - 750), first - 1)
    if len(tr) < 749: continue
    X, Y = logH.iloc[tr], target.iloc[tr]; ok = X.notna().all(1) & Y.notna()
    A = np.c_[np.ones(ok.sum()), X[ok].values]; beta = np.linalg.lstsq(A, Y[ok].values, rcond=None)[0]
    resid_var = np.var(Y[ok].values - A @ beta)
    Xt = logH[te]
    f_har[te] = np.exp(np.c_[np.ones(te.sum()), Xt.values] @ beta + resid_var / 2)
    mdl = lgb.train(dict(objective='regression', learning_rate=0.05, num_leaves=4, max_depth=2, min_data_in_leaf=50, verbose=-1, seed=1),
                    lgb.Dataset(X[ok], Y[ok]), num_boost_round=150)
    f_gbm[te] = np.exp(mdl.predict(Xt) + resid_var / 2)

f_std = (r.rolling(20).std()) ** 2          # 일간 분산 단위
oos = f_har.notna() & f_gbm.notna()
nxt = rv.shift(-1)
print(f'OOS {r.index[oos][0].date()} ~ {r.index[oos][-1].date()}')
for name, fc in [('일봉20일 std', f_std), ('HAR(1분 RV)', f_har), ('GBM depth2(1분 RV)', f_gbm)]:
    m = oos & fc.notna() & nxt.notna()
    corr = np.corrcoef(np.log(fc[m]), np.log(nxt[m]))[0, 1]
    qlike = (nxt[m] / fc[m] - np.log(nxt[m] / fc[m]) - 1).mean()
    print(f'  예측정확도 {name:<20} log상관={corr:.3f}  QLIKE={qlike:.3f} (낮을수록 좋음)')

trend = (px > px.rolling(120).mean()).astype(float)
y1 = r.shift(-1)
def perf(L, name):
    L = L.clip(0, 2).fillna(0)
    ret = (L * y1 - L.diff().abs().fillna(0) * 0.0003)[oos].dropna()
    eq = (1 + ret).cumprod(); yrs = len(ret) / 250
    cagr = eq.iloc[-1] ** (1 / yrs) - 1; vol = ret.std() * np.sqrt(250); mdd = (1 - eq / eq.cummax()).max()
    yr = ret.groupby(ret.index.year).apply(lambda x: (1 + x).prod() - 1)
    print(f'  {name:<34} CAGR={cagr:+6.1%} 변동성={vol:5.1%} 샤프={cagr / vol:+.2f} MDD={mdd:5.1%} 최악연도={yr.min():+.0%} 회전={L.diff().abs()[oos].mean():.3f}')
print('전략 (추세필터 공통, 목표 연 25%)')
for name, fc in [('일봉20일 std', f_std), ('HAR(1분 RV)', f_har), ('GBM depth2(1분 RV)', f_gbm)]:
    perf(0.25 / np.sqrt(fc * 250) * trend, f'변동성목표[{name}]+추세')
