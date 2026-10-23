"""최종 결정표: 지수 하나(또는 반반) + 예금·어음, 비중 하나만 고른다 (2026-10-21)

- 주식: SPY / QQQ / 반반 (원화 총수익, sleeves_krw.csv)
- 안전: 원화 예금·어음 = 한은 기준금리 + 1%p (2000~ 평균 약 3.9%, 지금 발행어음 5% 보다 낮게 잡음 = 보수적)
- 연 1회 재조정(어음 만기와 맞춤), 세전(연금·ISA 근사). 기간: 2007-04~2023-12 / ~2026-08 / 2000-01~2026-08(닷컴 정점 시작)
실행: .venv/bin/python -I docs/research/20261021_weight_design/simple_index_note.py
"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import grid
grid.S['SAFE'] = grid.RFK + 0.01 / 252
from grid import evaluate, sim


def recovery(p, idx):
    s = pd.Series(p, index=idx); dd = s / s.cummax() - 1; tr = dd.idxmin(); pk = s.loc[:tr].idxmax()
    rec = s.loc[tr:][s.loc[tr:] >= s.loc[pk]]
    return (rec.index[0] - pk).days / 365.25 if len(rec) else np.nan


if __name__ == '__main__':
    pd.set_option('display.width', 220)
    rows = []
    for eq_name, eq in [('S&P500', {'SPY': 1}), ('나스닥100', {'QQQ': 1}), ('반반', {'SPY': .5, 'QQQ': .5})]:
        for x in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
            A = ['SPY', 'QQQ', 'SAFE']; w = np.array([eq.get('SPY', 0) * x, eq.get('QQQ', 0) * x, 1 - x])
            d = dict(주식=eq_name, 주식비중=int(x * 100))
            for k, (a, b) in {'~2023': ('2007-04-02', '2023-12-29'), '~2026': ('2007-04-02', '2026-08-31'), '2000~': ('2000-01-03', '2026-08-31')}.items():
                m = evaluate(w[None], A, a, b)[0]
                d[f'{k}연'] = m['cagr'][0] * 100; d[f'{k}MDD'] = m['mdd'][0] * 100
                if k == '~2026': d['샤프'] = m['sharpe'][0]
                if k == '2000~':
                    P, ix = sim(w[None], A, a, b); d['2000~회복년'] = recovery(P[:, 0], ix)
            rows.append(d)
    df = pd.DataFrame(rows)
    print(df.round(1).to_string(index=False))
    df.round(2).to_csv(os.path.join(HERE, 'simple_index_note.csv'), index=False, encoding='utf-8-sig')
