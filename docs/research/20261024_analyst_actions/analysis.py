"""PREREGISTRATION.md 의 시험 R·P·U·B. 실행: .venv/bin/python -I analysis.py"""
import glob, os, sys, warnings
import numpy as np, pandas as pd
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO, 'docs/research/20261023_steady_compounders'))
from sc_common import load_raw, price_tr
RAW = os.path.join(REPO, 'DataVault/staging/us_analyst_actions/raw')
fr = []
for f in glob.glob(RAW + '/*.csv'):
    try: d = pd.read_csv(f, parse_dates=['GradeDate'])
    except Exception: continue
    if len(d): d['ticker'] = os.path.basename(f)[:-4]; fr.append(d)
A = pd.concat(fr); A['day'] = A.GradeDate.dt.normalize()
A = A[A.day >= '2012-09-01']
cal = load_raw('SPY').loc['2012-01-01':'2026-10-20'].index
spy = price_tr('SPY', cal).tr.values
P = {}
for t in A.ticker.unique():
    d = price_tr(t, cal)
    if d is not None: P[t] = d
tr = pd.DataFrame({t: d.tr for t, d in P.items()}); raw_close = pd.DataFrame({t: d.raw_close for t, d in P.items()})
spy_s = pd.Series(spy, index=cal)
A = A[A.ticker.isin(tr.columns)]
print('종목', A.ticker.nunique(), '원본 건수', len(A))
H = {'react': (-1, 1), 'd20': (1, 21), 'd60': (1, 61)}
def rets(t, day, a, b):
    i = cal.searchsorted(day, side='right') - 1   # day 이하 마지막 거래일
    if i + a < 0 or i + b >= len(cal): return np.nan
    x = tr[t].values
    if not (np.isfinite(x[i + a]) and np.isfinite(x[i + b])): return np.nan
    return x[i + b] / x[i + a] - 1 - (spy[i + b] / spy[i + a] - 1)
def add_ret(df):
    for k, (a, b) in H.items():
        df[k] = [rets(t, d, a, b) for t, d in zip(df.ticker, df.day)]
    df['yr'] = df.day.dt.year
    return df
def t_by_year(df, col):
    m = df.groupby('yr')[col].mean().dropna()
    return m.mean(), m.mean() / (m.std(ddof=1) / np.sqrt(len(m))) if len(m) > 2 else np.nan, len(m)
def halves(df, col):
    a, b = df[df.yr <= 2019], df[df.yr >= 2020]
    return a[col].mean(), b[col].mean()
def diff_report(df, flag, cols=('react', 'd20', 'd60')):
    out = {}
    for c in cols:
        d = df.dropna(subset=[c])
        up, dn = d[d[flag] > 0], d[d[flag] < 0]
        s = up.groupby('yr')[c].mean() - dn.groupby('yr')[c].mean(); s = s.dropna()
        h1 = up[up.yr <= 2019][c].mean() - dn[dn.yr <= 2019][c].mean(); h2 = up[up.yr >= 2020][c].mean() - dn[dn.yr >= 2020][c].mean()
        out[c] = {'상 평균': up[c].mean(), '하 평균': dn[c].mean(), '상−하': s.mean(), 't(연도)': s.mean() / (s.std(ddof=1) / np.sqrt(len(s))), '~2019': h1, '2020~': h2,
                  'n상': len(up), 'n하': len(dn)}
    return pd.DataFrame(out).T.round(4)
pd.set_option('display.width', 200)
# R 등급
R = A[A.Action.isin(['up', 'down'])].copy(); R['s'] = np.where(R.Action == 'up', 1, -1)
g = R.groupby(['ticker', 'day']).s.sum().reset_index(); g = g[g.s != 0]; g['s'] = np.sign(g.s)
R = add_ret(g)
print('\n[R] 등급 상향/하향 (같은 날 합침)'); print(diff_report(R, 's').to_string())
# P 목표가
Pt = A[A.priceTargetAction.isin(['Raises', 'Lowers']) & (A.currentPriceTarget > 0) & (A.priorPriceTarget > 0)].copy()
Pt['chg'] = Pt.currentPriceTarget / Pt.priorPriceTarget - 1
Pt['s'] = np.where(Pt.priceTargetAction == 'Raises', 1, -1)
g = Pt.groupby(['ticker', 'day']).agg(s=('s', 'sum'), chg=('chg', 'median')).reset_index(); g = g[g.s != 0]; g['s'] = np.sign(g.s)
Pg = add_ret(g)
print('\n[P] 목표가 상향/하향'); print(diff_report(Pg, 's').to_string())
Pg['bin'] = pd.cut(Pg.chg, [-1, -.10, 0, .10, .20, 9], labels=['<-10%', '-10~0', '0~10', '10~20', '≥20%'])
print(Pg.groupby('bin', observed=True).agg(n=('d20', 'size'), react=('react', 'mean'), d20=('d20', 'mean'), d60=('d60', 'mean')).round(4).to_string())
# 월별 패널 (U,B)
months = pd.date_range('2013-01-31', '2026-08-31', freq='ME')
def month_pos(m): return cal.searchsorted(m, side='right') - 1
rows = []
Pa = A[(A.currentPriceTarget > 0)]
Bx = A[A.priceTargetAction.isin(['Raises', 'Lowers'])]
for m in months:
    i = month_pos(m); j = month_pos(m + pd.offsets.MonthEnd(1))
    if j >= len(cal) or j <= i: continue
    d_end = cal[i]
    w90 = Pa[(Pa.day <= d_end) & (Pa.day > d_end - pd.Timedelta(days=90))]
    pt = w90.groupby('ticker').currentPriceTarget.agg(['median', 'size'])
    w30 = Bx[(Bx.day <= d_end) & (Bx.day > d_end - pd.Timedelta(days=30))]
    br = w30.groupby('ticker').priceTargetAction.agg(up=lambda s: (s == 'Raises').sum(), dn=lambda s: (s == 'Lowers').sum())
    for t in tr.columns:
        x = tr[t].values
        if not (np.isfinite(x[i]) and np.isfinite(x[j])): continue
        o = {'m': m, 'ticker': t, 'ret': x[j] / x[i] - 1 - (spy[j] / spy[i] - 1)}
        if t in pt.index and pt.loc[t, 'size'] >= 3 and np.isfinite(raw_close[t].values[i]):
            # 목표가는 분할 전 달러 → 같은 시점 무수정 종가와 비교
            o['upside'] = pt.loc[t, 'median'] / raw_close[t].values[i] - 1
        if t in br.index and (br.loc[t, 'up'] + br.loc[t, 'dn']) >= 5:
            o['breadth'] = (br.loc[t, 'up'] - br.loc[t, 'dn']) / (br.loc[t, 'up'] + br.loc[t, 'dn'])
        rows.append(o)
M = pd.DataFrame(rows); M['yr'] = M.m.dt.year
def quint_report(v):
    d = M.dropna(subset=[v]).copy()
    d = d[d.groupby('m')[v].transform('size') >= 25]
    d['q'] = d.groupby('m')[v].transform(lambda s: pd.qcut(s.rank(method='first'), 5, labels=False))
    mm = d.groupby(['m', 'q']).ret.mean().unstack(); sp = (mm[4] - mm[0]).dropna()
    ann = sp.groupby(sp.index.year).mean()
    h1, h2 = sp[sp.index.year <= 2019].mean(), sp[sp.index.year >= 2020].mean()
    ic = d.groupby('m').apply(lambda x: x[v].corr(x.ret, method='spearman')).dropna()
    return {'월수': len(sp), 'n': len(d), '하': mm[0].mean(), '3': mm[2].mean(), '상': mm[4].mean(), '상−하(월)': sp.mean(), 't(연도)': ann.mean() / (ann.std(ddof=1) / np.sqrt(len(ann))), '~2019': h1, '2020~': h2, 'IC': ic.mean()}
print('\n[U] 목표 상승여력 5분위 → 다음 달 초과수익'); print(pd.Series(quint_report('upside')).round(4).to_string())
print('\n[B] 목표가 수정 폭(최근 30일 Raises−Lowers 비율) 5분위 → 다음 달'); print(pd.Series(quint_report('breadth')).round(4).to_string())
