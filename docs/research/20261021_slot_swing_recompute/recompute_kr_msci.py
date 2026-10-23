"""한국 MSCI 슬롯 스윙 3종 정정 재계산 (2026-10-21)

대상(원본 → 20261021_krx_super_gainers_postmortem/):
  A. compare_msci_new_inclusions.py  — 사후 선정 'MSCI 신규편입' 40종목 × K-Titan(+10/+20/−6/40일) · 슈퍼루키(+20/+50/−8/60일)
  B. msci_korea_swing.py             — 코스피 시총 상위 100 × K-Titan (수급 점수순 진입)
  C. realtime_msci_screener_backtest.py — 매일 시총 2.3~4.5조 × K-Titan(200일선 0.88) (거래대금순 진입)
조건: 정정 엔진(swing_engine.py), 체결 2방식(종가 판정·종가 체결 / 시가 진입·장중 손절·익절), 연도별 거래세+0.07%, 매수 0.05%, 한국은행 기준금리 현금이자.
시총은 daily_ext.market_cap 중 mcap_approx==1(부풀림 결함) 행을 뺀다.
실행: .venv/bin/python docs/research/20261021_slot_swing_recompute/recompute_kr_msci.py
"""
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from swing_engine import run, metrics, tstats, fmt, bh_ew

ROOT = "/home/kuzimo/projects/CTradeSystem"
R = f"{ROOT}/DataVault/staging/kr_equity"
uni = pd.read_csv(f"{R}/universe.csv")
nm = dict(zip(uni.code, uni.name))
common = uni[(uni.is_preferred == 0) & (uni.is_spac == 0) & uni.market.isin(['KOSPI', 'KOSDAQ'])]
kospi = set(common[common.market == 'KOSPI'].code)
allc = set(common.code)

MSCI_NEW = ["A019170", "A086520", "A247540", "A022100", "A012450", "A042700", "A196170", "A267260", "A011200", "A352820",
            "A008930", "A128940", "A034020", "A036570", "A251270", "A091990", "A068760", "A263750", "A028300", "A010140",
            "A003670", "A005380", "A326030", "A302440", "A066970", "A293490", "A377300", "A005490", "A010130", "A010950",
            "A047050", "A005070", "A035900", "A010120", "A064350", "A079550", "A450080", "A096770", "A028260", "A035720"]

idx = pd.read_parquet(f"{R}/derived/index_1d.parquet")
u180 = idx[idx.code == 'U180'].set_index('date').sort_index()['close']
cal = u180.loc['2013-01-01':'2024-12-31'].index

px = pd.read_parquet(f"{R}/derived/prices_1d.parquet", columns=['code', 'date', 'open', 'high', 'low', 'close', 'value'],
                     filters=[('date', '>=', pd.Timestamp('2012-01-01')), ('date', '<=', pd.Timestamp('2024-12-31'))])
px = px[px.code.isin(allc)]
ext = pd.read_parquet(f"{R}/derived/daily_ext.parquet",
                      columns=['code', 'date', 'market_cap', 'mcap_approx', 'foreign_hold_pct', 'inst_net_qty'],
                      filters=[('date', '>=', pd.Timestamp('2012-01-01')), ('date', '<=', pd.Timestamp('2024-12-31'))])
ext = ext[ext.code.isin(allc)]

def piv(df, col, cols=None, index=None):
    p = df.pivot(index='date', columns='code', values=col).sort_index()
    return p.reindex(index=index if index is not None else p.index, columns=cols if cols is not None else p.columns)

Cd = piv(px, 'close'); cols = Cd.columns; dix = Cd.index
Od, Hd, Ld, Vd = (piv(px, k, cols) for k in ('open', 'high', 'low', 'value'))
MC = piv(ext, 'market_cap', cols, dix); AP = piv(ext, 'mcap_approx', cols, dix)
FG = piv(ext, 'foreign_hold_pct', cols, dix); IN = piv(ext, 'inst_net_qty', cols, dix)
MCc = MC.where(AP != 1)

ma20 = Cd.rolling(20, min_periods=10).mean(); ma200 = Cd.rolling(200, min_periods=100).mean()
dd_ = Cd.diff(); up = dd_.clip(lower=0); dn = -dd_.clip(upper=0)
rsi = 100 - 100 / (1 + up.ewm(com=13, adjust=False).mean() / dn.ewm(com=13, adjust=False).mean())
fg5 = FG.diff(5); in5 = IN.rolling(5, min_periods=3).sum()
fg20 = FG.diff(20); in20 = IN.rolling(20, min_periods=10).sum()
vma60 = Vd.rolling(60, min_periods=20).mean()
pull = ((Cd / ma20 >= 0.95) & (Cd / ma20 <= 1.03)) | (rsi <= 42.0)
flow5 = (fg5 > 0) | (in5 > 0)
SIG_KT85 = (Cd / ma200 >= 0.85) & pull & flow5
SIG_KT88 = (Cd / ma200 >= 0.88) & pull & flow5
SIG_SR = (Cd / ma200 >= 0.75) & (Cd / ma200 <= 1.20) & (Vd / vma60 >= 2.5) & ((fg20 > 0) | (in20 > 0))

A = lambda df: df.reindex(cal).to_numpy(dtype=float)
C, O, H, L = A(Cd), A(Od), A(Hd), A(Ld)
B = lambda df: df.reindex(cal).fillna(False).to_numpy(dtype=bool)
KT85, KT88, SR = B(SIG_KT85), B(SIG_KT88), B(SIG_SR)
MCn, MCcn, VAL = A(MC), A(MCc), A(Vd)
SCORE = A(fg5 * 100.0 + (in5 > 0).astype(float))
ci = {c: k for k, c in enumerate(cols)}
names = [nm.get(c, c) for c in cols]
kospi_mask = np.array([c in kospi for c in cols])

START = int(np.searchsorted(cal, pd.Timestamp('2013-03-01')))
months = {i for i in range(1, len(cal)) if cal[i].month != cal[i - 1].month and i >= START}

BOK = {2013: 2.60, 2014: 2.35, 2015: 1.65, 2016: 1.35, 2017: 1.30, 2018: 1.60, 2019: 1.60,
       2020: 0.65, 2021: 0.70, 2022: 2.30, 2023: 3.50, 2024: 3.45}
RF = np.array([BOK[d.year] / 100 / 252 for d in cal])
def tax_kr(i):
    d = cal[i]
    if d < pd.Timestamp('2019-05-30'): return 0.0030
    if d.year <= 2020: return 0.0025
    if d.year <= 2022: return 0.0023
    return 0.0020 if d.year == 2023 else 0.0018
COST = dict(buy_cost=lambda i, j: 0.0005, sell_cost=lambda i, j: tax_kr(i) + 0.0007, cash_rate=lambda i: RF[i])
KT = dict(tp1=.10, tp2=.20, sl=-.06, trail=-.05, hold=40)
SRR = dict(tp1=.20, tp2=.50, sl=-.08, trail=-.08, hold=60)

def ok(i, j):
    return not np.isnan(C[i - 1, j])

# --- 후보 함수들 (모두 전일 정보만) ---
new_ix = [ci[c] for c in MSCI_NEW if c in ci]
def c_new(sig):
    return lambda i: [j for j in new_ix if ok(i, j) and sig[i - 1, j]]
def c_new_nosig(i):
    return [j for j in new_ix if ok(i, j) and not np.isnan(O[i, j])]

cache = {}
def top100(mc):
    def f(i):
        m = max(k for k in months | {START} if k <= i)
        key = (id(mc), m)
        if key not in cache:
            row = np.where(kospi_mask, mc[m - 1], np.nan)
            okk = ~np.isnan(row)
            cache[key] = [int(j) for j in np.argsort(-np.where(okk, row, -np.inf))[:min(100, okk.sum())]]
        return cache[key]
    return f
T100c, T100o = top100(MCcn), top100(MCn)
def c_k100(univ, sig=True):
    def f(i):
        u = [j for j in univ(i) if ok(i, j) and (KT85[i - 1, j] if sig else True)]
        return sorted(u, key=lambda j: -np.nan_to_num(SCORE[i - 1, j]))
    return f

def c_rt(mc, sig=True):
    def f(i):
        row = mc[i - 1]
        u = np.where((row >= 2.3e12) & (row <= 4.5e12))[0]
        u = [int(j) for j in u if ok(i, j) and (KT88[i - 1, j] if sig else True)]
        return sorted(u, key=lambda j: -np.nan_to_num(VAL[i - 1, j]))
    return f
def rt_members(i):
    row = MCcn[i - 1]
    return [int(j) for j in np.where((row >= 2.3e12) & (row <= 4.5e12))[0]]

def show(name, eq, t=None):
    print(fmt(name, metrics(eq, START, RF), tstats(t) if t is not None else None), flush=True)
    return eq

if __name__ == '__main__':
    yrs = {}
    MODES = [("종가체결", dict(mode='close', entry='close', order='trail_first')), ("장중체결", dict(mode='intraday', entry='open'))]
    def both(label, cf, rule, key=None):
        for mlab, mk in MODES:
            eq, t, _ = run(cal, C, O, H, L, cf, START, **rule, **COST, names=names, **mk)
            show(f"{label} [{mlab}]", eq, t)
            if key and mlab == "종가체결": yrs[key] = eq
        return t
    print("=== A. 사후 선정 MSCI 신규편입 40종목 ===")
    both("A1 K-Titan", c_new(KT85), KT, 'A K-Titan'); both("A1 대조: 신호 없음", c_new_nosig, KT)
    t = both("A2 슈퍼루키", c_new(SR), SRR, 'A 슈퍼루키'); both("A2 대조: 신호 없음", c_new_nosig, SRR)
    bh = bh_ew(cal, C, lambda i: [j for j in new_ix if not np.isnan(C[i - 1, j])], START); yrs['A 40종목 보유'] = show("A 벤치: 40종목 동일가중 보유(사후 선정 효과)", bh)
    print("=== B. 코스피 시총 상위 100 ===")
    both("B K-Titan", c_k100(T100c), KT, 'B K-Titan'); both("B 대조: 신호 없음", c_k100(T100c, False), KT)
    bh = bh_ew(cal, C, T100c, START); yrs['B 100종목 보유'] = show("B 벤치: 상위100 동일가중 보유", bh)
    print("=== C. 실시간 시총 2.3~4.5조 스크리너 ===")
    both("C K-Titan", c_rt(MCcn), KT, 'C K-Titan'); both("C 대조: 신호 없음", c_rt(MCcn, False), KT)
    bh = bh_ew(cal, C, rt_members, START); yrs['C 구간 보유'] = show("C 벤치: 2.3~4.5조 구간 동일가중 보유", bh)
    k2 = pd.Series(u180.reindex(cal).to_numpy(), index=cal); yrs['K200'] = show("벤치: KOSPI200 가격지수", k2)
    print("연도별(전략은 종가체결 기준)")
    ann = {}
    for y in range(2013, 2025):
        ix = [k for k in range(START, len(cal)) if cal[k].year == y]
        ann[y] = {k: (v.iloc[ix[-1]] / v.iloc[ix[0] - 1] - 1) * 100 for k, v in yrs.items()}
    print(pd.DataFrame(ann).T.round(1).to_string())
