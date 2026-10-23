"""국면별 성적과 '최소 후회' 비중 (2026-10-21)

grid.py 결론: 한 구간 최적화는 그 구간에 뜬 자산(2007~ 레버리지·반도체, 2000~ 금)에 몰리고, 걷기 시험에서 뒤집힌다.
그래서 결과를 보기 전에 다음 기준을 정했다.
- 국면 4개: R1 2000-01~2006-12(닷컴 붕괴·회복) / R2 2007~2012(금융위기·유럽위기) / R3 2013~2019(미국 기술주 강세·한국 정체)
  / R4 2020~2026-08(코로나·2022 인플레·AI). 각 6~7년.
- 후회 = 그 국면 격자 최고 연복리 − 내 연복리. '최소 후회' = 4개 국면 중 가장 큰 후회가 가장 작은 비중(minimax regret).
- 함께: 최악 국면 연복리 최대(max-min), 2000~ 전체 MDD 한도(−35/−45/−55%)별 최소 후회.
- 후보 A(QQQ·SPY·SMH·K200·LEV200·IEF·GLD), 10% 단위. 세전·원화·연 1회 재조정(grid.py 와 같은 sim).
실행: .venv/bin/python -I docs/research/20261021_weight_design/regimes.py
"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from grid import combos, sim, metrics, evaluate, fmt_w, S

A = ['QQQ', 'SPY', 'SMH', 'K200', 'LEV200', 'IEF', 'GLD']
REG = {'R1 00~06': ('2000-01-03', '2006-12-29'), 'R2 07~12': ('2007-01-03', '2012-12-31'),
       'R3 13~19': ('2013-01-02', '2019-12-31'), 'R4 20~26': ('2020-01-02', '2026-08-31')}
FULL = ('2000-01-03', '2026-08-31')

if __name__ == '__main__':
    pd.set_option('display.width', 250)
    W = combos(len(A))
    C = {}
    for k, (a, b) in REG.items():
        C[k] = evaluate(W, A, a, b)[0]['cagr'] * 100
    mf = evaluate(W, A, *FULL)[0]
    m07 = evaluate(W, A, '2007-04-02', '2026-08-31')[0]
    m23 = evaluate(W, A, '2007-04-02', '2023-12-29')[0]
    df = pd.DataFrame(W, columns=A)
    for k in REG: df[k] = C[k]
    reg = pd.DataFrame({k: C[k].max() - C[k] for k in REG})
    df['최대후회'] = reg.max(1); df['최악국면'] = df[list(REG)].min(1)
    df['00~연'] = mf['cagr'] * 100; df['00~MDD'] = mf['mdd'] * 100; df['00~샤프'] = mf['sharpe']
    df['~23연'] = m23['cagr'] * 100; df['~23MDD'] = m23['mdd'] * 100
    df['~26연'] = m07['cagr'] * 100; df['~26MDD'] = m07['mdd'] * 100; df['~26샤프'] = m07['sharpe']
    print('국면별 격자 최고 연복리:', {k: round(C[k].max(), 1) for k in REG})
    print('국면별 1등 비중:', {k: fmt_w(W[C[k].argmax()], A) for k in REG})
    df['비중'] = [fmt_w(w, A) for w in W]
    show = ['비중'] + list(REG) + ['최대후회', '최악국면', '00~연', '00~MDD', '00~샤프', '~23연', '~23MDD', '~26연', '~26MDD', '~26샤프']
    picks = []
    def add(label, sub):
        if not len(sub): return
        i = sub['최대후회'].idxmin(); picks.append(df.loc[i, show].to_dict() | {'선정': label})
        top = sub.nsmallest(20, '최대후회')
        avg = top[A].mean().to_numpy()
        m = {k: evaluate(avg[None], A, a, b)[0] for k, (a, b) in REG.items()}
        r = {k: m[k]['cagr'][0] * 100 for k in REG}
        f = evaluate(avg[None], A, *FULL)[0]; g = evaluate(avg[None], A, '2007-04-02', '2026-08-31')[0]
        h = evaluate(avg[None], A, '2007-04-02', '2023-12-29')[0]
        picks.append(dict(선정=label + ' 이웃20', 비중=fmt_w(avg, A), **r, 최대후회=max(C[k].max() - r[k] for k in REG), 최악국면=min(r.values()),
                          **{'00~연': f['cagr'][0] * 100, '00~MDD': f['mdd'][0] * 100, '00~샤프': f['sharpe'][0],
                             '~23연': h['cagr'][0] * 100, '~23MDD': h['mdd'][0] * 100,
                             '~26연': g['cagr'][0] * 100, '~26MDD': g['mdd'][0] * 100, '~26샤프': g['sharpe'][0]}))
    add('최소후회', df)
    for cap in (-35, -45, -55):
        add(f'최소후회·00~MDD≥{cap}%', df[df['00~MDD'] >= cap])
    i = df['최악국면'].idxmax(); picks.append(df.loc[i, show].to_dict() | {'선정': '최악국면 최대'})
    i = df['00~샤프'].idxmax(); picks.append(df.loc[i, show].to_dict() | {'선정': '00~ 샤프 최대'})
    for nm, d in {'QQQ 100': {'QQQ': 1}, 'SPY 100': {'SPY': 1}, 'K200 100': {'K200': 1}, 'GLD 100': {'GLD': 1}, 'LEV200 100': {'LEV200': 1},
                  'SMH 100': {'SMH': 1}, '균등 7': {a: 1 / 7 for a in A}, 'QQQ50/K200 25/GLD25': {'QQQ': .5, 'K200': .25, 'GLD': .25},
                  'QQQ60/IEF40': {'QQQ': .6, 'IEF': .4}, 'K200 50/SPY 50': {'K200': .5, 'SPY': .5}}.items():
        w = np.array([d.get(a, 0) for a in A]); k = int(np.argmin(np.abs(W - w).sum(1)))
        if np.abs(W[k] - w).sum() < 1e-9:
            picks.append(df.loc[k, show].to_dict() | {'선정': '기준·' + nm})
        else:
            m = {kk: evaluate(w[None], A, a, b)[0]['cagr'][0] * 100 for kk, (a, b) in REG.items()}
            f = evaluate(w[None], A, *FULL)[0]; g = evaluate(w[None], A, '2007-04-02', '2026-08-31')[0]; h = evaluate(w[None], A, '2007-04-02', '2023-12-29')[0]
            picks.append(dict(선정='기준·' + nm, 비중=fmt_w(w, A), **m, 최대후회=max(C[kk].max() - m[kk] for kk in REG), 최악국면=min(m.values()),
                              **{'00~연': f['cagr'][0] * 100, '00~MDD': f['mdd'][0] * 100, '00~샤프': f['sharpe'][0], '~23연': h['cagr'][0] * 100,
                                 '~23MDD': h['mdd'][0] * 100, '~26연': g['cagr'][0] * 100, '~26MDD': g['mdd'][0] * 100, '~26샤프': g['sharpe'][0]}))
    out = pd.DataFrame(picks)[['선정'] + show]
    print(out.round(2).to_string(index=False))
    out.round(2).to_csv(os.path.join(HERE, 'regime_picks.csv'), index=False, encoding='utf-8-sig')
    df.round(3).to_csv(os.path.join(HERE, 'regime_all.csv'), index=False)
