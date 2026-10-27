"""PREREGISTRATION.md 시험 1~3. 실행: .venv/bin/python -I analysis.py"""
import glob, json, os, sys, warnings
import numpy as np, pandas as pd
from scipy import stats
warnings.filterwarnings('ignore')
HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO, 'docs/research/20261023_steady_compounders'))
from sc_common import load_raw, price_tr
rows = []
for f in glob.glob(os.path.join(REPO, 'DataVault/staging/us_earnings_surprise/raw/*.json')):
    t = os.path.basename(f)[:-5]
    try: r = json.load(open(f))['data']['earningsSurpriseTable']['rows']
    except Exception: continue
    for x in r or []:
        try:
            rows.append({'ticker': t, 'day': pd.to_datetime(x['dateReported']), 'eps': float(x['eps']), 'cons': float(str(x['consensusForecast']).replace('$', '').replace(',', ''))})
        except Exception: pass
D = pd.DataFrame(rows).drop_duplicates(['ticker', 'day'])
D = D[(D.cons > 0) & (D.cons.abs() >= 0.05)].copy()
D['sur'] = (D.eps - D.cons) / D.cons.abs()
cal = load_raw('SPY').index; cal = cal[cal >= '2025-01-01']
spy = price_tr('SPY', cal).tr.values
tr = {}
for t in D.ticker.unique():
    d = price_tr(t, cal)
    if d is not None: tr[t] = d.tr.values
def ex(t, day, a, b):
    i = cal.searchsorted(day, side='right') - 1
    if t not in tr or i + a < 0 or i + b >= len(cal): return np.nan
    x = tr[t]
    if not (np.isfinite(x[i + a]) and np.isfinite(x[i + b])): return np.nan
    return x[i + b] / x[i + a] - 1 - (spy[i + b] / spy[i + a] - 1)
D['react'] = [ex(t, d, -1, 1) for t, d in zip(D.ticker, D.day)]
D['d20'] = [ex(t, d, 1, 21) for t, d in zip(D.ticker, D.day)]
D['d60'] = [ex(t, d, 1, 61) for t, d in zip(D.ticker, D.day)]
D['q'] = D.day.dt.to_period('Q').astype(str); D['mo'] = D.day.dt.to_period('M')
print('사건', len(D), '종목', D.ticker.nunique(), '보고일', D.day.min().date(), '~', D.day.max().date(), '| 반응', D.react.notna().sum(), 'd20', D.d20.notna().sum(), 'd60', D.d60.notna().sum())
print(D.groupby('q').size().to_string())
pd.set_option('display.width', 200)
# 1) 5분위
D['qt'] = D.groupby('q').sur.transform(lambda s: pd.qcut(s.rank(method='first'), 5, labels=False) if len(s) >= 50 else np.nan)
print('\n[1] 분기 안 서프라이즈 5분위 평균 초과수익')
print(D.dropna(subset=['qt']).groupby('qt')[['sur', 'react', 'd20', 'd60']].mean().round(4).to_string())
for c in ['react', 'd20', 'd60']:
    d = D.dropna(subset=['qt', c]); sp = d[d.qt == 4].groupby('q')[c].mean() - d[d.qt == 0].groupby('q')[c].mean(); sm = d[d.qt == 4].groupby('mo')[c].mean() - d[d.qt == 0].groupby('mo')[c].mean()
    print(f'  상−하 {c}: 분기별 {sp.round(4).to_dict()} | 월별 평균 {sm.mean():.4f} t {sm.mean()/(sm.std(ddof=1)/np.sqrt(len(sm))):.2f} (월 {len(sm)}개)')
# 2) 부호 3칸
D['sg'] = pd.cut(D.sur, [-99, -.05, .05, 99], labels=['≤−5%', '±5%', '≥+5%'])
print('\n[2] 부호 3칸'); print(D.groupby('sg', observed=True).agg(n=('sur', 'size'), react=('react', 'mean'), d20=('d20', 'mean'), d60=('d60', 'mean')).round(4).to_string())
for c in ['react', 'd20', 'd60']:
    print('  ', c, '승률(≥+5%)', round((D[(D.sg == '≥+5%')][c].dropna() > 0).mean(), 3), '승률(≤−5%)', round((D[(D.sg == '≤−5%')][c].dropna() > 0).mean(), 3))
# 3) 상관
ic1 = D.dropna(subset=['react']).groupby('q').apply(lambda x: stats.spearmanr(x.sur, x.react)[0] if len(x) > 50 else np.nan).dropna()
ic2 = D.dropna(subset=['react', 'd20']).groupby('q').apply(lambda x: stats.spearmanr(x.react, x.d20)[0] if len(x) > 50 else np.nan).dropna()
ic3 = D.dropna(subset=['d20']).groupby('q').apply(lambda x: stats.spearmanr(x.sur, x.d20)[0] if len(x) > 50 else np.nan).dropna()
print('\n[3] 서프라이즈↔즉시 반응', ic1.round(3).to_dict(), '| 반응↔이후20일', ic2.round(3).to_dict(), '| 서프라이즈↔이후20일', ic3.round(3).to_dict())
