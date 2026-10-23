"""미국 ETF 넓게 비교 (2026-10-21) — 수익률로 고르지 않고 카테고리별 대표 ETF 를 미리 정해 같은 잣대로 비교

A그룹: 2007-04 시작(상장 2007-03 이전), B그룹: 2016-01 시작(2015년 이전 상장). 둘 다 SPY·QQQ 를 같이 넣는다.
조건은 board.py 와 같다: 원화 1억, 시작일 시가 매수 후 보유, 편도 0.1%, 배당 재투자(세후는 15% 원천징수),
세후 = 미국 직투 22%(마지막 날 청산 정산). --end=2023-12-31 로 반도체 급등 제외판.
주의: 이 목록 자체가 '지금 살아 있고 유명한 ETF' 라서 생존·인기 편향이 있다. 같은 카테고리의 사라진 ETF 는 없다.
실행: .venv/bin/python docs/research/20261021_strategy_board/etf_scan.py [--end=2023-12-31]
"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board as bd
from us_data import load
from us_tax_book import Book

GROUP_A = {
    '반도체': ['SMH', 'SOXX'], '기술': ['XLK', 'VGT', 'IYW'], '소프트웨어·인터넷': ['IGV', 'FDN'],
    '성장': ['VUG', 'IWF', 'IWO'], '지수': ['SPY', 'QQQ', 'VTI', 'DIA', 'RSP'], '중소형': ['MDY', 'IWM'],
    '가치·배당': ['VTV', 'VIG'], '헬스케어': ['XBI', 'IBB', 'IHI'], '방산': ['ITA', 'PPA'],
    '원자재': ['GDX', 'SLV'], '경기·금융': ['XHB', 'KRE'], '레버리지 2배': ['QLD', 'USD', 'ROM'],
}
GROUP_B = {
    '지수': ['SPY', 'QQQ', 'VOO'], '성장': ['SCHG', 'MGK'], '기술': ['FTEC', 'SKYY', 'PNQI', 'CIBR', 'HACK'],
    '배당': ['SCHD', 'DGRO', 'NOBL'], '팩터': ['QUAL', 'MTUM', 'USMV', 'SPLV'], '테마': ['ARKK', 'ROBO', 'TAN', 'ICLN', 'XAR'],
    '레버리지 3배': ['TQQQ', 'UPRO', 'SOXL', 'TECL'],
}
cal, T, FX, RF, RFK = bd.cal, bd.T, bd.FX, bd.RF, bd.RFK

def series(t):
    df = load(t).reindex(cal)
    return df.close.to_numpy(), df.open.to_numpy(), bd.divs(t, cal).to_numpy()

def bh(t, start, tax):
    c, o, d = series(t)
    C = c[:, None]; O = o[:, None]; D = d[:, None]
    def lp(i, j):
        k = i
        while k >= 0 and np.isnan(C[k, j]): k -= 1
        return C[k, j]
    i0 = int(np.searchsorted(cal, pd.Timestamp(start)))
    b = Book(cal, FX, RF, lp, i0, cost=0.001, tax=tax, div=D)
    eq = pd.Series(np.nan, index=cal)
    for i in range(i0, T):
        if i > i0: b.start_day(i)
        if i == i0 + 1:
            b.set_targets(i, {0: b.value(i, {0: O[i, 0]}) * (1 - 0.002)}, {0: O[i, 0]})
        eq.iloc[i] = b.equity_krw(i)
    fin, _ = b.final_after_tax(T - 1)
    jumps = int((np.abs(np.diff(c[i0:]) / c[i0:-1]) > 0.4).sum())
    return eq.iloc[i0:], fin, jumps

def table(groups, start):
    rows = []
    for cat, ts in groups.items():
        for t in ts:
            e0, f0, jm = bh(t, start, False); _, f1, _ = bh(t, start, True)
            m = bd.metrics(e0, RFK); yrs = (e0.index[-1] - e0.index[0]).days / 365.25
            rows.append(dict(분류=cat, ETF=t, 세전연=m['cagr'] * 100, 세후연=(f1 ** (1 / yrs) - 1) * 100, 세후억=f1, MDD=m['mdd'] * 100,
                             샤프=m['sharpe'], 최악년=m['worst'] * 100, **{f"{k}~": v * 100 for k, v in m['sub'].items() if k >= start[:4]},
                             이상봉=jm))
    df = pd.DataFrame(rows).sort_values('세후연', ascending=False)
    return df.round(2)

if __name__ == '__main__':
    tag = bd.END.strftime('%Y%m')
    for nm, g, st in [('A', GROUP_A, '2007-03-30'), ('B', GROUP_B, '2015-12-31')]:
        df = table(g, st)
        print(f"=== {nm}그룹: {st} ~ {bd.END.date()} ===")
        print(df.to_string(index=False))
        df.to_csv(os.path.join(HERE, f"etf_scan_{nm}_end{tag}.csv"), index=False, encoding='utf-8-sig')
