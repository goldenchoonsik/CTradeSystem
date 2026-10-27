"""10-K 사건 패널 — docs/research/20261023_revenue_vs_price/PREREGISTRATION.md 정의 그대로.
출력 events.csv.gz. 실행: .venv/bin/python -I docs/research/20261023_revenue_vs_price/build_events.py"""
import json, os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '20261023_steady_compounders'))
from sc_common import load_raw, membership, price_tr, yahoo_ticker, STAGE
from build_fund import CF, REV, NI, annual
W = pd.read_parquet(os.path.join(STAGE, 'derived', 'windows.parquet'), columns=['ticker'])
tickers = sorted(W.ticker.unique())
cal = load_raw('SPY').loc['2008-01-01':'2026-08-31'].index
spy = price_tr('SPY', cal).tr
memb = membership()
mrow_cache = {}
def members(d):
    i = memb.index[memb.date <= d][-1]
    if i not in mrow_cache:
        r = memb.loc[i]
        mrow_cache[i] = {yahoo_ticker(t, r.date) for t in r.tickers.split(',')}
    return mrow_cache[i]
def pos_after(d):
    i = cal.searchsorted(d, side='right')   # d 다음 거래일
    return i if i < len(cal) else None
def pos_upto(d):
    i = cal.searchsorted(d, side='right') - 1
    return i if i >= 0 else None
rows = []
for t in tickers:
    fp = f'{CF}/{t}.json'
    if not os.path.exists(fp): continue
    try: f = json.load(open(fp))['facts']
    except Exception: continue
    rev, ni = annual(f, REV), annual(f, NI)
    if rev.empty: continue
    P = None
    for filed, g in rev.groupby('filed'):
        g = g.sort_values('end')
        ends = list(g.end)
        # 같은 공시 안의 연속 3개 회계연도 (간격 300~430일)
        if len(ends) < 3: continue
        e2, e1, e0 = ends[-3:]
        if not (300 <= (e1 - e2).days <= 430 and 300 <= (e0 - e1).days <= 430): continue
        if (filed - e0).days > 200 or filed < e0: continue
        if t not in members(filed): continue
        R = g.set_index('end').val
        R0, R1, R2 = R[e0], R[e1], R[e2]
        if min(R0, R1, R2) <= 0: continue
        n = ni[ni.filed == filed].set_index('end').val
        N0, N1 = n.get(e0, np.nan), n.get(e1, np.nan)
        if P is None:
            P = price_tr(t, cal)
            if P is None: break
        tr, obs = P.tr.values, P.obs.values
        i0 = pos_after(filed)
        if i0 is None: continue
        o = {'ticker': t, 'filed': filed, 'fy_end': e0, 'R0': R0, 'R1': R1, 'R2': R2, 'N0': N0, 'N1': N1}
        for lab, h in [('r12', 252), ('r3', 63)]:
            i1 = i0 + h
            if i1 < len(cal) and np.isfinite(tr[i0]) and np.isfinite(tr[i1]) and obs[max(i1 - 5, 0):i1 + 1].any():
                o[lab] = tr[i1] / tr[i0] - 1
                o['spy_' + lab] = spy.values[i1] / spy.values[i0] - 1
        a, b = pos_upto(e0), pos_upto(e1)
        if a is not None and b is not None and np.isfinite(tr[a]) and np.isfinite(tr[b]) and b > 0:
            o['fy_ret'] = tr[a] / tr[b] - 1
            o['spy_fy_ret'] = spy.values[a] / spy.values[b] - 1
        rows.append(o)
E = pd.DataFrame(rows)
E.to_csv(os.path.join(HERE, 'events.csv.gz'), index=False)
print('사건', len(E), '종목', E.ticker.nunique(), '12m 있음', E.r12.notna().sum(), '회계연도수익 있음', E.fy_ret.notna().sum(),
      '공시일 범위', E.filed.min().date(), E.filed.max().date())
