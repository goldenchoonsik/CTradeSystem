"""사건 패널 분석 — PREREGISTRATION.md 의 판정 규칙 그대로. 실행: .venv/bin/python -I analysis.py"""
import os
import numpy as np, pandas as pd
from scipy import stats
HERE = os.path.dirname(os.path.abspath(__file__))
E = pd.read_csv(os.path.join(HERE, 'events.csv.gz'), parse_dates=['filed', 'fy_end'])
E['g0'] = E.R0 / E.R1 - 1; E['g1'] = E.R1 / E.R2 - 1
E['ACC'] = E.g0 - E.g1
E['AMT'] = ((E.R0 - E.R1) - (E.R1 - E.R2)) / E.R1
E['dN'] = np.where((E.N0 > 0) & (E.N1 > 0), E.N0 / E.N1 - 1, np.nan)
E['x12'] = E.r12 - E.spy_r12; E['x3'] = E.r3 - E.spy_r3; E['xfy'] = E.fy_ret - E.spy_fy_ret
E['yr'] = E.filed.dt.year; E['q'] = E.filed.dt.to_period('Q')
pd.set_option('display.width', 220)
print('사건', len(E), '| 12m', E.x12.notna().sum(), '| 이익 비교 가능', E.dN.notna().sum())
def yr_t(df, col):
    m = df.groupby('yr')[col].mean().dropna()
    return m.mean(), (m.mean() / (m.std(ddof=1) / np.sqrt(len(m))) if len(m) > 2 else np.nan), len(m)
# 1) 동시기 상관 (회계연도 말 연도별 순위상관 평균)
E['fyy'] = E.fy_end.dt.year
print('\n[1] 동시기 회계연도 초과수익과의 순위상관 (연도별 평균)')
for v in ['g0', 'dN']:
    ic = E.dropna(subset=[v, 'xfy']).groupby('fyy').apply(lambda d: stats.spearmanr(d[v], d.xfy)[0] if len(d) > 30 else np.nan).dropna()
    print(f'  {v}: 평균 {ic.mean():.2f} (연도 {len(ic)}개, 범위 {ic.min():.2f}~{ic.max():.2f})')
# 회귀: 순위 두 개 같이
from numpy.linalg import lstsq
Z = E.dropna(subset=['g0', 'dN', 'xfy']).copy()
for c in ['g0', 'dN', 'xfy']: Z[c + '_r'] = Z.groupby('fyy')[c].rank(pct=True)
X = np.c_[np.ones(len(Z)), Z.g0_r, Z.dN_r]
b = lstsq(X, Z.xfy_r, rcond=None)[0]
print(f'  둘 같이 회귀(순위): 매출 계수 {b[1]:.2f}, 이익 계수 {b[2]:.2f}  (n={len(Z)})')
# 2) 매출 구간 × 이익 상태
def bucket_g(g):
    return pd.cut(g, [-9, 0, .05, .15, .30, 99], labels=['<0', '0~5', '5~15', '15~30', '≥30'], right=False)
E['gb'] = bucket_g(E.g0)
def nistate(r):
    if not (r.N1 > 0): return None
    if r.N0 <= 0: return '적자전환'
    d = r.N0 / r.N1 - 1
    return '이익↑' if d >= .2 else ('이익 그대로' if abs(d) <= .1 else ('이익↓' if d <= -.2 else '중간'))
E['ns'] = E.apply(nistate, axis=1)
def tab(df, rowvar, colvar=None):
    out = []
    for k, d in df.groupby(rowvar, observed=True):
        a = d.dropna(subset=['xfy']); b_ = d.dropna(subset=['x12'])
        out.append({rowvar: k, 'n': len(d), '동시기 평균': a.xfy.mean(), '동시기 중앙': a.xfy.median(),
                    '12m 평균': b_.x12.mean(), '12m 중앙': b_.x12.median(), '12m 연도t': yr_t(b_, 'x12')[1], '12m 승률': (b_.x12 > 0).mean()})
    return pd.DataFrame(out).round(3)
print('\n[2] 이익 그대로 — 매출 증가율 구간별'); print(tab(E[E.ns == '이익 그대로'], 'gb').to_string(index=False))
print('\n[2b] 매출 증가율 15%↑ — 이익 상태별'); print(tab(E[E.g0 >= .10], 'ns').to_string(index=False))
print('\n[2c] 이익 하락 — 매출 증가율 구간별'); print(tab(E[E.ns == '이익↓'], 'gb').to_string(index=False))
print('\n[2d] 매출 그대로(|g0|≤5%) — 이익 상태별'); print(tab(E[E.g0.abs() <= .05], 'ns').to_string(index=False))
# 3) 예측력 5분위
E['qq'] = E.q.astype(str)
def quint(df, v, h='x12'):
    d = df.dropna(subset=[v, h]).copy()
    d['qt'] = d.groupby('qq')[v].transform(lambda s: pd.qcut(s.rank(method='first'), 5, labels=False) if len(s) >= 25 else np.nan)
    d = d.dropna(subset=['qt'])
    sp = d[d.qt == 4].groupby('yr')[h].mean() - d[d.qt == 0].groupby('yr')[h].mean()
    sp = sp.dropna()
    ics = d.groupby('qq').apply(lambda x: stats.spearmanr(x[v], x[h])[0] if len(x) > 25 else np.nan).dropna()
    m = d.groupby('qt')[h].mean()
    return {'n': len(d), '1(하)': m[0], '3': m[2], '5(상)': m[4], '상−하': sp.mean(), 't(연도)': sp.mean() / (sp.std(ddof=1) / np.sqrt(len(sp))), 'IC(분기평균)': ics.mean(), '연도수': len(sp)}
print('\n[3] 공시 분기 안 5분위 → 12개월 초과수익')
for lab, df in [('전체', E), ('이익 그대로', E[E.ns == '이익 그대로']), ('이익↓', E[E.ns == '이익↓'])]:
    r = pd.DataFrame({v: quint(df, v) for v in ['g0', 'AMT', 'ACC']}).T.round(3)
    print(f'-- {lab}'); print(r.to_string())
print('\n[3b] 2×2: 증가값 확대(AMT>0) 안에서 증가율 가속 위/아래 (전체)')
d = E[(E.AMT > 0) & E.x12.notna()].copy(); d['acc_up'] = d.ACC > 0
print(tab(d, 'acc_up').to_string(index=False))
d = E[(E.AMT <= 0) & E.x12.notna()].copy(); d['acc_up'] = d.ACC > 0
print('증가값 줄어듦(AMT≤0):'); print(tab(d, 'acc_up').to_string(index=False))
print('\n[4] 2023 이전 공시만 (재확인)')
E2 = E[E.filed < '2024-01-01']
for lab, df in [('전체', E2)]:
    print(pd.DataFrame({v: quint(df, v) for v in ['g0', 'AMT', 'ACC']}).T.round(3).to_string())
print(tab(E2[E2.g0 >= .10], 'ns').to_string(index=False))
