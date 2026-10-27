"""당시 S&P500 시총 상위 10(분기말 재선정) vs QQQ·SPY(=VOO). 2010~2023-12, 달러 세전 총수익.
시총 = steady_compounders fund.parquet(SEC 주식수×가격, 분기말), 가격 = raw/yahoo 분할보정+배당재투자.
실행: .venv/bin/python -I docs/research/20261023_sp500_top10_vs_qqq/top10.py"""
import sys, os
import numpy as np, pandas as pd
sys.path.insert(0, 'docs/research/20261023_steady_compounders')
from sc_common import STAGE, load_raw, price_tr
END = pd.Timestamp('2023-12-29')
F = pd.read_parquet(os.path.join(STAGE, 'derived', 'fund.parquet'))
F['start'] = pd.to_datetime(F.start)
F = F[(F.start >= '2009-12-01') & (F.start <= END) & F.mcap.notna()]
F = F[F.ticker != 'GOOG']   # 알파벳 이중 클래스 중복 제거(GOOGL 한 칸으로)
cal = load_raw('SPY').loc['2009-01-01':'2026-08-31'].index
P = {t: price_tr(t, cal) for t in set(F.ticker) | {'SPY', 'QQQ', 'XOM'}}
P = {t: d for t, d in P.items() if d is not None}
# XOM 은 SEC 주식수가 비어 있어 시총이 없다 → 어림 주식수(10억 주, 손으로 입력한 근사값)로 보충
XS = {2009: 4.73, 2010: 4.7, 2011: 4.9, 2012: 4.5, 2013: 4.3, 2014: 4.2, 2015: 4.15, 2016: 4.15, 2017: 4.25, 2018: 4.25,
      2019: 4.23, 2020: 4.23, 2021: 4.23, 2022: 4.2, 2023: 4.0}
xs = sorted(F.start.unique())
F = pd.concat([F, pd.DataFrame({'start': xs, 'ticker': 'XOM',
    'mcap': [P['XOM'].raw_close.loc[:pd.Timestamp(d)].iloc[-1] * XS[pd.Timestamp(d).year] * 1e9 for d in xs]})], ignore_index=True)
tr = pd.DataFrame({t: d.tr for t, d in P.items()})
starts = sorted(F.start.unique())
starts = [pd.Timestamp(s) for s in starts]
def run(n, weight, fixed_at=None):
    val, curve, lists = 1.0, [], {}
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else END
        if fixed_at is not None and s >= fixed_at:
            pass
        if fixed_at is not None:
            sel_date = max(x for x in starts if x <= fixed_at)
        else:
            sel_date = s
        g = F[F.start == sel_date].dropna(subset=['mcap'])
        g = g[g.ticker.isin(tr.columns)]
        g = g[tr.loc[s, g.ticker].notna().values & tr.loc[e, g.ticker].notna().values]
        top = g.nlargest(n, 'mcap')
        lists[s.date()] = top.ticker.tolist()
        r = tr.loc[e, top.ticker] / tr.loc[s, top.ticker]
        if fixed_at is not None:   # 보유: 처음 비중에서 표류 → 단순화: 구간마다 같은 종목 재균형 대신 별도 계산
            return None, lists
        w = top.mcap / top.mcap.sum() if weight == 'cap' else pd.Series(1 / len(top), index=top.index)
        val *= float((r.values * w.values).sum())
        curve.append((e, val))
    return pd.Series(dict(curve)), lists
def stats(c, label):
    y = (c.index[-1] - pd.Timestamp('2009-12-31')).days / 365.25
    # 분기 수익 기반 연변동성·MDD(분기 단위, 대략)
    q = c.pct_change().dropna()
    mdd = (c / c.cummax() - 1).min()
    return {'전략': label, '연복리%': round((c.iloc[-1] ** (1 / y) - 1) * 100, 1), '배수': round(c.iloc[-1], 2),
            '분기MDD%': round(mdd * 100, 0), '분기샤프': round(q.mean() / q.std() * 2, 2)}
bench = {}
for b in ['QQQ', 'SPY']:
    s = tr[b]
    pts = [s0 for s0 in starts] + [END]
    c = pd.Series({pts[i + 1]: s.loc[pts[i + 1]] / s.loc[starts[0]] for i in range(len(pts) - 1)})
    bench[b] = c
rows = [stats(bench['QQQ'], 'QQQ'), stats(bench['SPY'], 'SPY(=VOO)')]
res = {}
for n, w in [(10, 'cap'), (10, 'eq'), (5, 'cap'), (20, 'cap')]:
    c, L = run(n, w); res[(n, w)] = (c, L)
    rows.append(stats(c, f'S&P500 상위{n} {"시총가중" if w=="cap" else "동일가중"} (분기 재선정)'))
# 고정 명단: 2012-12 시총 상위10 매수 후 보유(표류)
sd = max(x for x in starts if x <= pd.Timestamp('2012-12-31'))
g = F[(F.start == sd) & F.ticker.isin(tr.columns)].nlargest(10, 'mcap')
t10 = g.ticker.tolist()
for lab, t0, ws in [('2012말 상위10 시총가중 보유', sd, g.mcap / g.mcap.sum()), ('2012말 상위10 동일가중 보유', sd, pd.Series(.1, index=g.index))]:
    cc = (tr.loc[END, t10] / tr.loc[t0, t10]).values @ ws.values
    y = (END - t0).days / 365.25
    rows.append({'전략': lab + ' (2012-12→2023-12)', '연복리%': round((cc ** (1 / y) - 1) * 100, 1), '배수': round(cc, 2)})
for b in ['QQQ', 'SPY']:
    cc = tr.loc[END, b] / tr.loc[sd, b]; y = (END - sd).days / 365.25
    rows.append({'전략': f'{b} (2012-12→2023-12)', '연복리%': round((cc ** (1 / y) - 1) * 100, 1), '배수': round(cc, 2)})
pd.set_option('display.width', 200)
print(pd.DataFrame(rows).to_string(index=False))
print('\n2012말 상위10:', t10)
for d in ['2010-03-31', '2013-03-28', '2016-03-31', '2019-03-29', '2022-03-31']:
    k = min(res[(10, 'cap')][1], key=lambda x: abs((pd.Timestamp(x) - pd.Timestamp(d)).days))
    print(k, res[(10, 'cap')][1][k])
# 구간별 연복리
c10 = res[(10, 'cap')][0]
for a, b in [('2010-01-01', '2015-12-31'), ('2016-01-01', '2023-12-31')]:
    out = {}
    for lab, c in [('상위10', c10), ('QQQ', bench['QQQ']), ('SPY', bench['SPY'])]:
        x = c[a:b]; base = c[:a].iloc[-1] if len(c[:a]) else 1.0
        y = (x.index[-1] - pd.Timestamp(a)).days / 365.25
        out[lab] = round(((x.iloc[-1] / base) ** (1 / y) - 1) * 100, 1)
    print(a[:4], '~', b[:4], out)
