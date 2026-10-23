"""단순 구조 계열 — '성장 엔진 비중 x' 하나로 공격/방어를 조절 (2026-10-21)

격자 최적은 표본에 뜬 자산을 쫓으므로, 결과를 보기 전에 구조를 정했다.
- 성장 엔진(x): G1 = QQQ 단독 / G2 = QQQ·SMH·LEV200 1/3씩 / G3 = QQQ·LEV200 1/2씩
- 분산 자산(1−x): K200·IEF·GLD 1/3씩 (다른 시장·원화 / 위기 방어 채권 / 위기 방어 금)
- x = 40~100%, 연 1회 재조정, 세전·원화(grid.py sim)
실행: .venv/bin/python -I docs/research/20261021_weight_design/families.py
"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from grid import evaluate, fmt_w
from regimes import REG, FULL

A = ['QQQ', 'SPY', 'SMH', 'K200', 'LEV200', 'IEF', 'GLD']
G = {'G1 QQQ': {'QQQ': 1}, 'G2 QQQ·SMH·LEV': {'QQQ': 1 / 3, 'SMH': 1 / 3, 'LEV200': 1 / 3}, 'G3 QQQ·LEV': {'QQQ': .5, 'LEV200': .5}}
D = {'K200': 1 / 3, 'IEF': 1 / 3, 'GLD': 1 / 3}


def row(w):
    d = {}
    for k, (a, b) in REG.items():
        d[k] = evaluate(w[None], A, a, b)[0]['cagr'][0] * 100
    for k, (a, b) in {'00~': FULL, '~23': ('2007-04-02', '2023-12-29'), '~26': ('2007-04-02', '2026-08-31')}.items():
        m = evaluate(w[None], A, a, b)[0]
        d[f'{k}연'] = m['cagr'][0] * 100; d[f'{k}MDD'] = m['mdd'][0] * 100; d[f'{k}샤프'] = m['sharpe'][0]
        d[f'{k}최악년'] = m['worst'][0] * 100
    return d


if __name__ == '__main__':
    pd.set_option('display.width', 250)
    rows = []
    for gname, g in G.items():
        for x in (0.4, 0.5, 0.6, 0.7, 0.8, 1.0):
            w = np.array([g.get(a, 0) * x + D.get(a, 0) * (1 - x) for a in A])
            rows.append(dict(계열=gname, 성장=int(x * 100), 비중=fmt_w(w, A), **row(w)))
    df = pd.DataFrame(rows)
    cols = ['계열', '성장', '비중'] + list(REG) + ['00~연', '00~MDD', '00~샤프', '~23연', '~23MDD', '~23샤프', '~26연', '~26MDD', '~26샤프', '~26최악년']
    print(df[cols].round(2).to_string(index=False))
    df.round(2).to_csv(os.path.join(HERE, 'families.csv'), index=False, encoding='utf-8-sig')
