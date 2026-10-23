"""후보 혼합의 세전·세후 (2026-10-21) — 비교판(board.py)과 같은 장부·잣대

- 계좌 하나(미국 직투)에 전부 담는다고 가정: 원화 1억, 편도 0.1%, 배당 재투자(세후 15% 원천징수),
  세후 = 연 250만 공제 후 22%(다음 해 5월 납부), 마지막 날 청산 정산. K200(국내 주식형 ETF) 매매차익 비과세(배당 과세는 생략).
- 실물만 사용: QQQ·SPY·SMH·QLD·IEF·GLD(Yahoo), K200 = 코스피200 TR − 보수 0.15%(sleeves.py 와 같음). 시작 2007-03-30.
- 연 1회: 12월 마지막 거래일 종가 신호 → 다음 거래일 시가로 목표 비중. LEV200: 매일 QQQ 종가 vs 200일선 → 바뀌면 다음 날 시가에
  QLD 사거나 팔아 현금(미 국채 3개월, 세후면 이자 15.4% 원천징수).
- 세전 = 연금·ISA 에 같은 지수의 국내 상장 ETF 를 담았을 때의 근사(국내 상장 ETF 보수·추적오차·환헤지 차이는 무시).
실행: .venv/bin/python -I docs/research/20261021_weight_design/aftertax.py
"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '20261021_slot_swing_recompute'))
from us_data import load, ROOT
from us_tax_book import Book
import sleeves as sl

START = pd.Timestamp('2007-03-30')
END = pd.Timestamp('2026-08-31')
cal = load('SPY').loc['2005-01-01':END].index
T = len(cal)
FX = sl.FX.reindex(cal).to_numpy()
RF = sl.RF.reindex(cal).to_numpy()
RFK = sl.RFK.reindex(cal).to_numpy()
SEC = ['QQQ', 'SPY', 'SMH', 'QLD', 'IEF', 'GLD', 'K200']
J = {s: k for k, s in enumerate(SEC)}


def divs(t):
    cp = f"{ROOT}/raw/yahoo/corporate_actions/{t}.csv"
    if not os.path.exists(cp): return np.zeros(T)
    ca = pd.read_csv(cp, parse_dates=['date']); raw = load(t)
    mult = pd.Series(1.0, index=raw.index)
    sp = ca[ca.type == 'split']
    for d, n, m in zip(sp.date, sp.numerator, sp.denominator):
        mult[raw.index < d] *= n / m
    dv = ca[ca.type == 'dividend'].groupby('date').amount.sum()
    return (dv / mult.reindex(dv.index).fillna(1.0)).reindex(cal).fillna(0.0).to_numpy()


C = np.full((T, len(SEC)), np.nan); O = C.copy(); DV = np.zeros_like(C)
for s in SEC[:-1]:
    df = load(s).reindex(cal)
    C[:, J[s]] = df.close.to_numpy(); O[:, J[s]] = df.open.to_numpy(); DV[:, J[s]] = divs(s)
kr = (1 + sl.kospi200().reindex(cal).fillna(0)).cumprod().to_numpy()      # 원화 TR 수준(미국 날짜)
C[:, J['K200']] = kr / FX; O[:, J['K200']] = C[:, J['K200']]              # 직전 한국 종가로 체결(board.py 와 같은 근사)
CF = pd.DataFrame(C).ffill().to_numpy()
qqq = C[:, J['QQQ']]
SMA = pd.Series(qqq).rolling(200).mean().to_numpy()
DEC = {i for i in range(T - 1) if cal[i].year != cal[i + 1].year}
px = lambda i, j: CF[i, j]


def run(weights, tax, end=END):
    """weights: {'QQQ':.., 'LEV200':.., ...} (LEV200 은 QLD+200일선 슬리브)."""
    i0 = int(np.searchsorted(cal, START)); i_end = int(np.searchsorted(cal, end, side='right')) - 1
    b = Book(cal, FX, RF, px, i0, cost=0.001, tax=tax, div=DV, taxfree={J['K200']})
    lev_state = bool(qqq[i0] > SMA[i0]); lev_cash = 0.0
    pend_rebal, pend_lev = True, None
    eq = np.full(T, np.nan)
    for i in range(i0 + 1, i_end + 1):
        b.start_day(i)
        if not lev_state and 'LEV200' in weights:
            lev_cash = min(lev_cash * (1 + RF[i] * ((1 - 0.154) if tax else 1.0)), max(b.cash, 0.0))
        prices = {j: (O[i, j] if not np.isnan(O[i, j]) else CF[i - 1, j]) for j in range(len(SEC))}
        if pend_lev is not None:
            lev_state = pend_lev
        if pend_rebal:
            V = b.value(i, prices); tgt = {}
            for s, w in weights.items():
                if s == 'LEV200':
                    if lev_state: tgt[J['QLD']] = tgt.get(J['QLD'], 0) + V * w * 0.998
                    else: lev_cash = V * w * 0.998
                else:
                    tgt[J[s]] = tgt.get(J[s], 0) + V * w * 0.998
            b.set_targets(i, tgt, prices)
        elif pend_lev is not None and 'LEV200' in weights:
            cur = {j: q * prices[j] for j, q in b.sh.items()}
            if lev_state:
                cur[J['QLD']] = cur.get(J['QLD'], 0) + min(lev_cash, max(b.cash, 0.0)); lev_cash = 0.0
                b.set_targets(i, cur, prices)
            else:
                c0 = b.cash; cur.pop(J['QLD'], None)
                b.set_targets(i, cur, prices); lev_cash = b.cash - c0
        pend_rebal, pend_lev = False, None
        sig = bool(qqq[i] > SMA[i])
        if sig != lev_state: pend_lev = sig
        if i in DEC: pend_rebal = True
        eq[i] = b.equity_krw(i)
    fin, taxpaid = b.final_after_tax(i_end)
    e = pd.Series(eq[i0 + 1:i_end + 1], index=cal[i0 + 1:i_end + 1])
    return e, fin, taxpaid


def stats(e, fin):
    e = e / e.iloc[0]; y = (e.index[-1] - e.index[0]).days / 365.25
    r = e.pct_change().dropna(); rk = pd.Series(RFK, index=cal).reindex(r.index).to_numpy()
    return dict(세전연=(e.iloc[-1] ** (1 / y) - 1) * 100, 세후연=(fin ** (1 / y) - 1) * 100, 세후억=fin,
                MDD=(e / e.cummax() - 1).min() * 100, 샤프=(r.to_numpy() - rk).mean() / r.std() * np.sqrt(252))


def mix(g, x):
    D = {'K200': 1 / 3, 'IEF': 1 / 3, 'GLD': 1 / 3}
    out = {k: v * x for k, v in g.items()}
    for k, v in D.items(): out[k] = out.get(k, 0) + v * (1 - x)
    return out


G1 = {'QQQ': 1}; G2 = {'QQQ': 1 / 3, 'SMH': 1 / 3, 'LEV200': 1 / 3}; G3 = {'QQQ': .5, 'LEV200': .5}
PORT = {'QQQ 100': {'QQQ': 1}, 'SPY 100': {'SPY': 1}, 'LEV200 100 (QLD+200일선)': {'LEV200': 1},
        'G1-40 (QQQ40 + 분산60)': mix(G1, .4), 'G1-60 (QQQ60 + 분산40)': mix(G1, .6),
        'G2-40 (QQQ·SMH·LEV 각13 + 분산60)': mix(G2, .4), 'G2-60 (QQQ·SMH·LEV 각20 + 분산40)': mix(G2, .6),
        'G2-80 (QQQ·SMH·LEV 각27 + 분산20)': mix(G2, .8), 'G3-60 (QQQ30·LEV30 + 분산40)': mix(G3, .6),
        'QQQ50/K200 25/GLD25': {'QQQ': .5, 'K200': .25, 'GLD': .25}, 'K200 50/SPY 50': {'K200': .5, 'SPY': .5}}

if __name__ == '__main__':
    pd.set_option('display.width', 220)
    rows = []
    for nm, w in PORT.items():
        for end in (pd.Timestamp('2023-12-29'), END):
            e0, f0, _ = run(w, False, end); e1, f1, tx = run(w, True, end)
            rows.append(dict(포트=nm, 종료=str(end.date()), **stats(e0, f1), 세금억=tx))
        print(rows[-2]['포트'], f"~2023 세후 {rows[-2]['세후연']:.1f}% / ~2026 세후 {rows[-1]['세후연']:.1f}%", flush=True)
    df = pd.DataFrame(rows)
    wide = df.pivot(index='포트', columns='종료', values=['세전연', '세후연', 'MDD', '샤프']).round(2)
    wide = wide.reindex(list(PORT))
    print(wide.to_string())
    df.round(3).to_csv(os.path.join(HERE, 'aftertax.csv'), index=False, encoding='utf-8-sig')
