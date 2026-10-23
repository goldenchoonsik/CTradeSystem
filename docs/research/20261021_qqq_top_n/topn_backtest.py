"""QQQ 종목 수 줄이기 — 당시 나스닥100 시총 상위 N (2026-10-21)

명단: ndx_membership(위키 변경표 역적용), 시총: build_caps(SEC 주식 수 × Yahoo 가격, 공시일 기준).
규칙: 분기말(3·6·9·12월 말) 시총으로 상위 N 선정 → 다음 거래일 시가 체결. 가중 = 시총(cap) 또는 동일(eq), 분기마다 재조정.
잣대(비교판과 같음): 원화 1억, 편도 0.1%, 배당 재투자(세후 15% 원천징수), 세후 = 미국 직투 22%(연 250만 공제, 다음 해 5월 납부,
      마지막 날 청산 정산), 현금 = 미 국채 3개월. 종료 2023-12-29 / 2026-08-31.
'최적 N' 안정성: ① 2010~2017 에서 고른 N 을 2018~ 에 적용 ② 매년 초 직전 3년 최고 N 으로 갈아타는 걷기 시험 ③ 연도별 1등 N.
한계: Yahoo 에 없는 피인수·상폐 구성 종목(ESRX·CELG·ATVI·XLNX·YHOO·EA 등)과 Baidu 는 순위에서 빠짐(생존 편향).
실행: .venv/bin/python -I docs/research/20261021_qqq_top_n/topn_backtest.py
"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '20261021_slot_swing_recompute'))
from ndx_common import ROOT, RENAME
from ndx_membership import members_at
from us_data import load
from us_tax_book import Book

NS = [1, 2, 3, 5, 7, 10, 15, 20, 30, 50, 100]
END = pd.Timestamp('2026-08-31')
TB = {2009: 0.15, 2010: 0.14, 2011: 0.05, 2012: 0.09, 2013: 0.06, 2014: 0.03, 2015: 0.05, 2016: 0.32, 2017: 0.93, 2018: 1.94,
      2019: 2.06, 2020: 0.37, 2021: 0.05, 2022: 2.02, 2023: 5.07, 2024: 4.97, 2025: 4.20, 2026: 3.90}
BOK = {2009: 2.00, 2010: 2.20, 2011: 3.10, 2012: 3.00, 2013: 2.60, 2014: 2.35, 2015: 1.65, 2016: 1.35, 2017: 1.30, 2018: 1.60,
       2019: 1.60, 2020: 0.65, 2021: 0.70, 2022: 2.30, 2023: 3.50, 2024: 3.45, 2025: 2.70, 2026: 2.50}

caps = pd.read_csv(os.path.join(HERE, 'caps_quarterly.csv'), index_col=0, parse_dates=True)
qqq = load('QQQ')
cal = qqq.loc['2009-12-31':END].index
T = len(cal)
TICK = sorted(set(caps.columns) | {'QQQ', 'SPY'})
J = {t: k for k, t in enumerate(TICK)}


def divs(t, idx):
    cp = f"{ROOT}/raw/yahoo/corporate_actions/{t}.csv"
    if not os.path.exists(cp): return np.zeros(len(idx))
    ca = pd.read_csv(cp, parse_dates=['date']); raw = load(t)
    mult = pd.Series(1.0, index=raw.index)
    sp = ca[ca.type == 'split']
    for d, n, m in zip(sp.date, sp.numerator, sp.denominator):
        mult[raw.index < d] *= n / m
    dv = ca[ca.type == 'dividend'].groupby('date').amount.sum()
    return (dv / mult.reindex(dv.index).fillna(1.0)).reindex(idx).fillna(0.0).to_numpy()


C = np.full((T, len(TICK)), np.nan); O = C.copy(); DV = np.zeros_like(C)
for t, j in J.items():
    df = load(t)
    if df is None: continue
    df = df.reindex(cal)
    C[:, j] = df.close.to_numpy(); O[:, j] = df.open.to_numpy(); DV[:, j] = divs(t, cal)
CF = pd.DataFrame(C).ffill().to_numpy()
fx = pd.read_csv(f"{ROOT}/staging/fx_usdkrw/usdkrw_daily.csv", parse_dates=['date']).set_index('date')['usdkrw']
FX = fx.reindex(fx.index.union(cal)).ffill().reindex(cal).to_numpy()
RF = np.array([TB[d.year] / 100 / 252 for d in cal])
RFK = np.array([BOK[d.year] / 100 / 252 for d in cal])
px = lambda i, j: CF[i, j]

REB = {}                     # 체결일 index → 분기말
for q in caps.index:
    k = int(np.searchsorted(cal, q, side='right'))
    if k < T: REB[k] = q
MEM = {q: {RENAME.get(t, t).replace('.', '-') for t in members_at(q)} for q in caps.index}


def ranked(q, i):
    r = caps.loc[q]
    r = r[r.index.isin(MEM[q])].dropna()
    r = r[[not np.isnan(O[i, J[t]]) for t in r.index]]
    return r.sort_values(ascending=False)


def run(n, scheme, tax, end=END, sel=None):
    """sel: {체결일 index: N} 걷기 시험용(그 분기의 N)."""
    i_end = int(np.searchsorted(cal, end, side='right')) - 1
    i0 = min(REB)
    b = Book(cal, FX, RF, px, i0, cost=0.001, tax=tax, div=DV)
    eq = np.full(T, np.nan)
    for i in range(i0, i_end + 1):
        if i > i0: b.start_day(i)
        if i in REB:
            nn = sel(i) if sel else n
            r = ranked(REB[i], i).iloc[:nn]
            w = (r / r.sum()) if scheme == 'cap' else pd.Series(1.0 / len(r), index=r.index)
            prices = {J[t]: O[i, J[t]] for t in r.index}
            for j in b.sh: prices.setdefault(j, O[i, j] if not np.isnan(O[i, j]) else CF[i - 1, j])
            V = b.value(i, prices)
            b.set_targets(i, {J[t]: V * w[t] * (1 - 0.0025) for t in r.index}, prices)
        eq[i] = b.equity_krw(i)
    fin, taxpaid = b.final_after_tax(i_end)
    return pd.Series(eq[i0:i_end + 1], index=cal[i0:i_end + 1]), fin


def bh(t, tax, end=END):
    i_end = int(np.searchsorted(cal, end, side='right')) - 1
    i0 = min(REB); j = J[t]
    b = Book(cal, FX, RF, px, i0, cost=0.001, tax=tax, div=DV)
    eq = np.full(T, np.nan)
    for i in range(i0, i_end + 1):
        if i > i0: b.start_day(i)
        if i == i0: b.set_targets(i, {j: b.value(i, {j: O[i, j]}) * 0.998}, {j: O[i, j]})
        eq[i] = b.equity_krw(i)
    fin, _ = b.final_after_tax(i_end)
    return pd.Series(eq[i0:i_end + 1], index=cal[i0:i_end + 1]), fin


def stats(eq, fin_at):
    e = eq / eq.iloc[0]; yrs = (e.index[-1] - e.index[0]).days / 365.25
    r = e.pct_change().dropna()
    rk = pd.Series(RFK, index=cal).reindex(r.index).to_numpy()
    sub = {}
    for a, b_ in [('2010', '2015'), ('2016', '2023'), ('2024', '2026')]:
        s = e.loc[:f'{b_}-12-31']; s = s[s.index >= pd.Timestamp(f'{a}-01-01') - pd.Timedelta(days=7)]
        if len(s) > 20 and s.index[-1] > pd.Timestamp(f'{a}-06-30'):
            y = (s.index[-1] - s.index[0]).days / 365.25; sub[f'{a}~'] = ((s.iloc[-1] / s.iloc[0]) ** (1 / y) - 1) * 100
    return dict(세전연=(e.iloc[-1] ** (1 / yrs) - 1) * 100, 세후연=(fin_at ** (1 / yrs) - 1) * 100, 세후억=fin_at,
                MDD=(e / e.cummax() - 1).min() * 100, 샤프=(r.to_numpy() - rk).mean() / r.std() * np.sqrt(252), **sub)


if __name__ == '__main__':
    pd.set_option('display.width', 250)
    # 0) 복제 점검: 명단 전체 시총가중 vs 실제 QQQ (세전, 달러 무관 — 같은 원화 환산)
    e_all, _ = run(200, 'cap', False); e_q, _ = bh('QQQ', False)
    a = e_all.pct_change().dropna(); b = e_q.pct_change().reindex(a.index)
    yrs = (e_all.index[-1] - e_all.index[0]).days / 365.25
    print(f"[복제] 명단 전체 시총가중 {((e_all.iloc[-1] / e_all.iloc[0]) ** (1 / yrs) - 1) * 100:.2f}% vs QQQ {((e_q.iloc[-1] / e_q.iloc[0]) ** (1 / yrs) - 1) * 100:.2f}%/년, "
          f"일간 상관 {a.corr(b):.4f}, 추적오차 {(a - b).std() * np.sqrt(252) * 100:.2f}%/년")
    for yy in range(2010, 2027):
        s1 = e_all[e_all.index.year == yy]; s2 = e_q[e_q.index.year == yy]
        print(f"   {yy}: 합성 {(s1.iloc[-1] / s1.iloc[0] - 1) * 100:6.1f}%  QQQ {(s2.iloc[-1] / s2.iloc[0] - 1) * 100:6.1f}%")
    rows = []
    for end in [pd.Timestamp('2023-12-29'), END]:
        for t in ['QQQ', 'SPY']:
            e0, _ = bh(t, False, end); _, f1 = bh(t, True, end)
            rows.append(dict(end=end.date(), 전략=f'{t} 보유', **stats(e0, f1)))
        for scheme in ['cap', 'eq']:
            for n in NS:
                e0, _ = run(n, scheme, False, end); _, f1 = run(n, scheme, True, end)
                rows.append(dict(end=end.date(), 전략=f'상위{n} {"시총" if scheme == "cap" else "동일"}', N=n, scheme=scheme, **stats(e0, f1)))
                print(rows[-1]['end'], rows[-1]['전략'], f"세후 {rows[-1]['세후연']:.1f}%", flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(HERE, 'topn_results.csv'), index=False, encoding='utf-8-sig')
    for end, g in df.groupby('end'):
        print(f"\n=== 2010-01 ~ {end} ===")
        print(g.drop(columns=['end', 'N', 'scheme']).round(2).to_string(index=False))
