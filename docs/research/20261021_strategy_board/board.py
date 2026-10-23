"""전략 비교 평가판 1차 — 공개된 고전 전략을 같은 잣대로 (2026-10-21)

같은 잣대:
- 기간 2007-04 ~ 2026-08 (모든 ETF 존재 + 12개월 신호 준비), --end=2023-12-31 이면 반도체 급등(2024~) 제외판.
  하위 구간 2007-04~2015 / 2016~2023 / 2024~2026-08
- 신호는 월말 종가(일 단위 전략은 그날 종가), 체결은 다음 거래일 시가
- 비용 편도: 미국 ETF 0.1%(수수료+호가), 국내 ETF 0.02%
- 원화 1억 기준 평가. 세전 = 세금 없음(연금·ISA 계좌로 국내상장 동일 ETF 를 들 때의 근사)
                       세후 = 미국 직투 22%(연 250만 공제, 다음 해 5월 납부, 배당 15% 원천징수, 마지막 날 청산 정산)
                       국내주식형 ETF(069500) 매매차익은 비과세
- 미국 ETF 는 배당 재투자(총수익). 069500 은 Creon 수정 가격 = 분배금 반영(총수익에 가까움, 10-21 정정)
- 현금: 미 국채 3개월(달러 계좌) / 한국 기준금리(원화 계좌)
전략 파라미터는 모두 공개 당시 값 그대로(최적화 없음).
실행: .venv/bin/python docs/research/20261021_strategy_board/board.py
"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '20261021_slot_swing_recompute'))
from us_data import load, ROOT
from us_tax_book import Book

START = pd.Timestamp('2007-03-30')
END = pd.Timestamp(next((a.split('=')[1] for a in sys.argv if a.startswith('--end=')), '2026-08-31'))   # 반도체 급등 제외 비교: --end=2023-12-31
US = ['SPY', 'QQQ', 'EFA', 'AGG', 'IEF', 'TLT', 'GLD', 'DBC', 'VNQ',
      'XLK', 'XLF', 'XLE', 'XLV', 'XLI', 'XLY', 'XLP', 'XLU', 'XLB', 'QLD', 'TQQQ']
SECTORS = ['XLK', 'XLF', 'XLE', 'XLV', 'XLI', 'XLY', 'XLP', 'XLU', 'XLB']
TB = {2005: 3.15, 2006: 4.73, 2007: 4.36, 2008: 1.37, 2009: 0.15, 2010: 0.14, 2011: 0.05, 2012: 0.09, 2013: 0.06, 2014: 0.03,
      2015: 0.05, 2016: 0.32, 2017: 0.93, 2018: 1.94, 2019: 2.06, 2020: 0.37, 2021: 0.05, 2022: 2.02, 2023: 5.07,
      2024: 4.97, 2025: 4.20, 2026: 3.90}
BOK = {2005: 3.30, 2006: 4.30, 2007: 4.80, 2008: 4.70, 2009: 2.00, 2010: 2.20, 2011: 3.10, 2012: 3.00, 2013: 2.60, 2014: 2.35,
       2015: 1.65, 2016: 1.35, 2017: 1.30, 2018: 1.60, 2019: 1.60, 2020: 0.65, 2021: 0.70, 2022: 2.30, 2023: 3.50,
       2024: 3.45, 2025: 2.70, 2026: 2.50}

# ---------------------------------------------------------------- 데이터
spy = load('SPY')
cal = spy.loc['2005-01-01':END].index
T = len(cal)
fx = pd.read_csv(f"{ROOT}/staging/fx_usdkrw/usdkrw_daily.csv", parse_dates=['date']).set_index('date')['usdkrw']
FX = fx.reindex(fx.index.union(cal)).ffill().reindex(cal).to_numpy()
RF = np.array([TB[d.year] / 100 / 252 for d in cal])
RFK = np.array([BOK[d.year] / 100 / 252 for d in cal])

def divs(t, idx):
    cp = f"{ROOT}/raw/yahoo/corporate_actions/{t}.csv"
    if not os.path.exists(cp): return pd.Series(0.0, index=idx)
    ca = pd.read_csv(cp, parse_dates=['date'])
    raw = load(t)
    mult = pd.Series(1.0, index=raw.index)
    for d, n, m in zip(ca[ca.type == 'split'].date, ca[ca.type == 'split'].numerator, ca[ca.type == 'split'].denominator):
        mult[raw.index < d] *= n / m
    dv = ca[ca.type == 'dividend'].groupby('date').amount.sum()
    dv = (dv / mult.reindex(dv.index).fillna(1.0)).reindex(idx).fillna(0.0)
    return dv

cols = US + ['K200']
ci = {c: k for k, c in enumerate(cols)}
C = np.full((T, len(cols)), np.nan); O = C.copy(); DV = np.zeros_like(C)
for t in US:
    df = load(t).reindex(cal)
    C[:, ci[t]] = df.close.to_numpy(); O[:, ci[t]] = df.open.to_numpy(); DV[:, ci[t]] = divs(t, cal).to_numpy()
k = pd.read_csv(f"{ROOT}/raw/creon/candle_1d/A069500_1d.csv", parse_dates=['timestamp']).set_index('timestamp')
k = k.rename(columns=lambda c: c.replace('upbit_', ''))
kr = k.loc[:END]
k_on_us = kr.close.reindex(kr.index.union(cal)).ffill().reindex(cal)
C[:, ci['K200']] = k_on_us.to_numpy() / FX          # 달러 환산(혼합 전략용), 원화 평가 시 다시 곱해짐
O[:, ci['K200']] = C[:, ci['K200']]                 # 미국 달력에 맞춘 근사: 직전 한국 종가로 체결
TR = pd.DataFrame(C, index=cal, columns=cols)
TR = ((TR + pd.DataFrame(DV, index=cal, columns=cols)) / TR.shift(1)).fillna(1.0).cumprod()   # 총수익 지수(신호용)
TRK = pd.Series(k_on_us.to_numpy(), index=cal)      # 원화 K200(신호용)

def lp(i, j):
    k_ = i
    while k_ >= 0 and np.isnan(C[k_, j]): k_ -= 1
    return C[k_, j] if k_ >= 0 else np.nan

mends = [i for i in range(T - 1) if cal[i].month != cal[i + 1].month] + [T - 1]
i0 = int(np.searchsorted(cal, START))
assert cal[i0] in [cal[m] for m in mends] or True
MEND = set(mends)

def mom(t, i, months, skip=0):
    tr = TR[t]
    return tr.iloc[i - 21 * skip] / tr.iloc[i - 21 * months] - 1
def rf_cum(i, months):
    return np.prod(1 + RF[i - 21 * months + 1:i + 1]) - 1
def sma10(t, i):
    ms = [m for m in mends if m <= i][-10:]
    return C[i, ci[t]] > np.nanmean([C[m, ci[t]] for m in ms])

# ---------------------------------------------------------------- 전략 (월말 i 에서 목표 비중)
def eq_w(names): return {ci[n]: 1.0 / len(names) for n in names} if names else {}
STRATS = {
    'B·SPY 보유': lambda i: eq_w(['SPY']),
    'B·QQQ 보유': lambda i: eq_w(['QQQ']),
    'B·60/40 (SPY/IEF)': lambda i: {ci['SPY']: 0.6, ci['IEF']: 0.4},
    'S1·SPY 10개월선(파버 2007)': lambda i: eq_w(['SPY']) if sma10('SPY', i) else {},
    'S2·QQQ 10개월선': lambda i: eq_w(['QQQ']) if sma10('QQQ', i) else {},
    'S3·GTAA5 추세(파버 2007)': lambda i: {ci[a]: 0.2 for a in ['SPY', 'EFA', 'IEF', 'VNQ', 'DBC'] if sma10(a, i)},
    'S4·듀얼모멘텀 GEM(안토나치 2012)': lambda i: (eq_w([max(['SPY', 'EFA'], key=lambda a: mom(a, i, 12))])
                                          if mom('SPY', i, 12) > rf_cum(i, 12) else eq_w(['AGG'])),
    'S5·섹터 모멘텀 상위3(12-1)': lambda i: eq_w(sorted(SECTORS, key=lambda a: -mom(a, i, 12, 1))[:3]),
    'S6·섹터 모멘텀+절대 필터': lambda i: {ci[a]: 1 / 3 for a in sorted(SECTORS, key=lambda a: -mom(a, i, 12, 1))[:3]
                                     if mom(a, i, 12) > rf_cum(i, 12)},
    'S7·자산 상대강도 상위3(파버 2010)': lambda i: {ci[a]: 1 / 3 for a in sorted(['SPY', 'EFA', 'IEF', 'VNQ', 'DBC'],
                                     key=lambda a: -(mom(a, i, 3) + mom(a, i, 6) + mom(a, i, 12)) / 3)[:3] if sma10(a, i)},
    'S8·영구 포트폴리오(브라운 1981)': lambda i: {ci['SPY']: .25, ci['TLT']: .25, ci['GLD']: .25},
    'S9·리스크패리티(역변동성 4자산)': lambda i: (lambda v: {ci[a]: (1 / v[a]) / sum(1 / x for x in v.values()) for a in v})(
        {a: np.std(np.diff(np.log(C[i - 63:i + 1, ci[a]]))) for a in ['SPY', 'TLT', 'GLD', 'DBC']}),
    'L1·QLD(나스닥 2배) 보유': lambda i: eq_w(['QLD']),
    'K4·K200 50% + SPY 50%': lambda i: {ci['K200']: .5, ci['SPY']: .5},
    'K5·한국판 듀얼모멘텀(K200↔SPY↔현금)': lambda i: (lambda a: eq_w([a]) if (mom(a, i, 12) if a != 'K200' else TRK.iloc[i] / TRK.iloc[i - 252] - 1) > rf_cum(i, 12) else {})(
        max(['K200', 'SPY'], key=lambda a: (TRK.iloc[i] / TRK.iloc[i - 252] - 1) if a == 'K200' else mom(a, i, 12) + (FX[i] / FX[i - 252] - 1))),
}
ANNUAL = {'S8·영구 포트폴리오(브라운 1981)', 'K4·K200 50% + SPY 50%', 'B·60/40 (SPY/IEF)'}   # 연 1회(12월말) 재조정
DAILY = {  # 매일 종가 신호 → 다음 날 시가, 신호가 바뀔 때만 매매
    'L2·QLD + QQQ 200일선': ('QLD', 'QQQ'),
}

def run(name, tax, start=i0):
    f = STRATS.get(name)
    b = Book(cal, FX, RF, lp, start, cost=0.001, tax=tax, div=DV, taxfree={ci['K200']})
    eq = pd.Series(np.nan, index=cal); pending = None; state = None
    for i in range(start, T):
        if i > start: b.start_day(i)
        if pending is not None:
            prices = {j: (O[i, j] if not np.isnan(O[i, j]) else lp(i - 1, j)) for j in set(pending) | set(b.sh)}
            v = b.value(i, prices) * (1 - 0.001)
            b.set_targets(i, {j: v * w for j, w in pending.items()}, prices); pending = None
        if name in DAILY:
            lev, und = DAILY[name]
            on = C[i, ci[und]] > np.nanmean(C[i - 199:i + 1, ci[und]])
            if on != state: pending = eq_w([lev]) if on else {}; state = on
        elif i in MEND and (name not in ANNUAL or cal[i].month == 12 or i == start):
            pending = f(i)
        eq.iloc[i] = b.equity_krw(i)
    fin, taxsum = b.final_after_tax(T - 1)
    return eq.iloc[start:], fin, taxsum

# ---------------------------------------------------------------- 한국 전용(원화 계좌, 한국 달력)
kc = kr.loc['2005-01-01':]
KC = kc.close.to_numpy(float); KO = kc.open.to_numpy(float); kcal = kc.index
RFKK = np.array([BOK[d.year] / 100 / 252 for d in kcal])
k0 = int(np.searchsorted(kcal, START))
def run_kr(kind, cost=0.0002):
    v = 1.0; eq = []; inpos = False
    mendk = {i for i in range(len(kcal) - 1) if kcal[i].month != kcal[i + 1].month}
    for i in range(k0, len(kcal)):
        if kind == 'hold':
            if i > k0: v *= KC[i] / KC[i - 1]
        elif kind == 'sma10':
            if i > k0:
                v *= (KC[i] / KC[i - 1]) if inpos else (1 + RFKK[i])
            if i in mendk:
                ms = sorted(m for m in mendk if m <= i)[-10:]
                want = KC[i] > np.mean(KC[ms])
                if want != inpos: v *= (1 - cost); inpos = want
        elif kind == 'overnight':     # 종가 매수 → 다음 날 시가 매도, 장중은 현금(이자 무시)
            if i > k0: v *= (KO[i] / KC[i - 1]) * (1 - 2 * cost)
        eq.append(v)
    return pd.Series(eq, index=kcal[k0:])

# ---------------------------------------------------------------- 평가
def metrics(eq, rfk):
    e = eq / eq.iloc[0]
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    r = e.pct_change().dropna()
    rk = pd.Series(rfk, index=rfk_index[id(rfk)]).reindex(r.index).fillna(0).to_numpy()
    ann = e.resample('YE').last(); ann = pd.concat([pd.Series([1.0], index=[e.index[0]]), ann]).pct_change().dropna()
    sub = {}
    for a, b_ in [('2007-04-01', '2015-12-31'), ('2016-01-01', '2023-12-31'), ('2024-01-01', '2026-08-31')]:
        s = e.loc[a:b_]
        if len(s) < 20: continue
        s = pd.concat([e.loc[:a].iloc[-1:], s]) if a > '2007-04-01' else s; y = (s.index[-1] - s.index[0]).days / 365.25
        sub[a[:4]] = (s.iloc[-1] / s.iloc[0]) ** (1 / y) - 1
    return dict(cagr=e.iloc[-1] ** (1 / yrs) - 1, mdd=(e / e.cummax() - 1).min(),
                sharpe=(r.to_numpy() - rk).mean() / r.std() * np.sqrt(252), worst=ann.min(), sub=sub)
rfk_index = {id(RFK): cal, id(RFKK): kcal}

if __name__ == '__main__':
    rows = []
    names = list(STRATS) + list(DAILY)
    for nm in names:
        e0, f0, _ = run(nm, False); e1, f1, tx = run(nm, True)
        m = metrics(e0, RFK); yrs = (e0.index[-1] - e0.index[0]).days / 365.25
        rows.append(dict(전략=nm, 세전연=m['cagr'], 세후연=f1 ** (1 / yrs) - 1, 세전억=f0, 세후억=f1, MDD=m['mdd'], 샤프=m['sharpe'],
                         최악년=m['worst'], **{f"{k}~": v for k, v in m['sub'].items()}))
        print(f"{nm:<36} 세전 {m['cagr']*100:5.1f}% 세후 {(f1 ** (1 / yrs) - 1)*100:5.1f}% MDD {m['mdd']*100:6.1f}% 샤프 {m['sharpe']:4.2f}", flush=True)
    for nm, kind in [('K1·K200 보유(069500, 배당 포함 수정가)', 'hold'), ('K2·K200 10개월선', 'sma10'), ('K3·K200 오버나잇(종가→시가)', 'overnight')]:
        e = run_kr(kind); m = metrics(e, RFKK); yrs = (e.index[-1] - e.index[0]).days / 365.25
        rows.append(dict(전략=nm, 세전연=m['cagr'], 세후연=m['cagr'], 세전억=e.iloc[-1] / e.iloc[0], 세후억=e.iloc[-1] / e.iloc[0],
                         MDD=m['mdd'], 샤프=m['sharpe'], 최악년=m['worst'], **{f"{k}~": v for k, v in m['sub'].items()}))
        print(f"{nm:<36} 세전=세후 {m['cagr']*100:5.1f}% MDD {m['mdd']*100:6.1f}% 샤프 {m['sharpe']:4.2f}", flush=True)
    df = pd.DataFrame(rows).sort_values('세후연', ascending=False)
    pct = [c for c in ['세전연', '세후연', 'MDD', '최악년', '2007~', '2016~', '2024~'] if c in df.columns]
    out = df.copy()
    for c in pct: out[c] = (out[c] * 100).round(1)
    out['세전억'] = out['세전억'].round(2); out['세후억'] = out['세후억'].round(2); out['샤프'] = out['샤프'].round(2)
    print(out.to_string(index=False))
    out.to_csv(os.path.join(HERE, f"board_v1_end{END.strftime('%Y%m')}.csv"), index=False, encoding='utf-8-sig')
