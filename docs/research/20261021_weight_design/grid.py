"""비중 격자 탐색 (2026-10-21) — 세전(연금·ISA 근사), 원화, 연 1회(12월 마지막 거래일 종가) 재조정, 재조정 매매액 × 0.1%

사전 고정 규칙(결과 보기 전에 정함):
- 비중 10% 단위, 후보 A = QQQ·SPY·SMH·K200·LEV200·IEF·GLD, 후보 B = A + TOP5 (2010-01~)
- 낙폭 한도 c: 선정 구간 MDD ≥ c 인 조합 중 세전 연복리 최대 = '최적', 같은 조건 상위 20개 평균 비중 = '이웃'(한 점 최적의 우연을 줄임)
- 선정 구간 → 표본 밖: A1 2007-04~2023-12 → 2024~ / A2 2000-01~2023-12(닷컴 포함) → 2024~ / A3 2007-04~2015 → 2016~ (걷기)
  / A4 2000-01~2012 → 2013~ (걷기) / B1 2010~2023 → 2024~ / B2 2010~2016 → 2017~ (걷기)
- 공통 보고 열: ~2023(2007-04~2023-12), ~2026(2007-04~2026-08), 2000~(2000-01~2026-08, 닷컴 포함; TOP5 는 2010 전 현금이라 생략), 표본 밖
실행: .venv/bin/python -I docs/research/20261021_weight_design/grid.py
"""
import os, sys, itertools
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
S = pd.read_csv(os.path.join(HERE, 'sleeves_krw.csv'), index_col=0, parse_dates=True)
RFK = S.pop('RFK')
COST = 0.001


def combos(k, step=10):
    out = []
    for c in itertools.combinations(range(100 // step + k - 1), k - 1):
        b = (-1,) + c + (100 // step + k - 1,)
        out.append([(b[i + 1] - b[i] - 1) * step / 100 for i in range(k)])
    return np.array(out)


def sim(W, assets, a, b):
    """W: M×K 비중. 반환: 경로 (T×M), 날짜."""
    R = S.loc[a:b, assets].to_numpy()
    idx = S.loc[a:b].index
    R[0] = 0.0                                          # 첫날 종가에 매수
    yr = idx.year.to_numpy()
    bounds = [0] + [i for i in range(len(idx) - 1) if yr[i] != yr[i + 1]] + [len(idx) - 1]
    bounds = sorted(set(bounds))
    W = np.atleast_2d(W); M = W.shape[0]
    P = np.empty((len(idx), M)); V = np.ones(M)
    P[0] = V
    for s, e in zip(bounds[:-1], bounds[1:]):
        G = np.cumprod(1 + R[s + 1:e + 1], axis=0)       # (e−s)×K
        P[s + 1:e + 1] = (G @ W.T) * V
        end = G[-1] * W                                  # M×K 금액 비율
        V = P[e]
        if e != len(idx) - 1:
            wd = end / end.sum(1, keepdims=True)
            V = V * (1 - COST * np.abs(W - wd).sum(1))
            P[e] = V
    return P, idx


def metrics(P, idx):
    yrs = (idx[-1] - idx[0]).days / 365.25
    cagr = P[-1] ** (1 / yrs) - 1
    mdd = (P / np.maximum.accumulate(P, axis=0) - 1).min(0)
    r = P[1:] / P[:-1] - 1
    rk = RFK.reindex(idx).to_numpy()[1:, None]
    sharpe = (r - rk).mean(0) / r.std(0) * np.sqrt(252)
    yr = idx.year.to_numpy()
    ends = [i for i in range(len(idx) - 1) if yr[i] != yr[i + 1]] + [len(idx) - 1]
    lv = np.vstack([P[0:1], P[ends]])
    worst = (lv[1:] / lv[:-1] - 1).min(0)
    return dict(cagr=cagr, mdd=mdd, sharpe=sharpe, worst=worst)


def sub_cagr(P, idx, a, b):
    m = (idx >= pd.Timestamp(a)) & (idx <= pd.Timestamp(b))
    ii = np.where(m)[0]
    i0 = max(ii[0] - 1, 0); i1 = ii[-1]
    y = (idx[i1] - idx[i0]).days / 365.25
    return (P[i1] / P[i0]) ** (1 / y) - 1


def evaluate(W, assets, a, b):
    P, idx = sim(W, assets, a, b)
    return metrics(P, idx), P, idx


CAPS = [-0.20, -0.25, -0.30, -0.35, -0.40, -0.45, -1.0]


def pick(W, m, cap, top=20):
    ok = np.where(m['mdd'] >= cap)[0]
    if not len(ok): return None, None
    order = ok[np.argsort(-m['cagr'][ok])]
    return W[order[0]], W[order[:top]].mean(0)


def fmt_w(w, assets):
    return ' '.join(f"{a}{int(round(x * 100))}" for a, x in zip(assets, w) if x > 0.004)


PERIODS = {'~2023': ('2007-04-02', '2023-12-29'), '~2026': ('2007-04-02', '2026-08-31'), '2000~': ('2000-01-03', '2026-08-31')}
CAPS = [-0.20, -0.25, -0.30, -0.35, -0.40, -0.50, -0.60, -1.0]


def describe(w, assets, oos=None):
    d = {}
    for k, (a, b) in PERIODS.items():
        if k == '2000~' and 'TOP5' in assets and w[assets.index('TOP5')] > 0: continue
        m, _, _ = evaluate(w[None], assets, a, b)
        d[f'{k}연'] = m['cagr'][0] * 100; d[f'{k}MDD'] = m['mdd'][0] * 100
        if k != '2000~': d[f'{k}샤프'] = m['sharpe'][0]
    if oos:
        m, _, _ = evaluate(w[None], assets, *oos)
        d['밖연'] = m['cagr'][0] * 100; d['밖MDD'] = m['mdd'][0] * 100
    return d


BASE = {'QQQ 100': {'QQQ': 1}, 'SPY 100': {'SPY': 1}, 'K200 50/SPY 50': {'K200': .5, 'SPY': .5}, 'QQQ 60/IEF 40': {'QQQ': .6, 'IEF': .4},
        'QQQ 50/K200 25/GLD 25': {'QQQ': .5, 'K200': .25, 'GLD': .25}, 'LEV200 100': {'LEV200': 1}}


def run_job(tag, assets, sel, oos):
    W = combos(len(assets))
    m, _, _ = evaluate(W, assets, *sel)
    rows = []
    for cap in CAPS:
        w1, w20 = pick(W, m, cap)
        lab = '없음' if cap < -0.9 else f"{int(cap * 100)}%"
        if w1 is None: continue
        for kind, w in [('최적', w1), ('이웃', w20)]:
            k = int(np.argmin(np.abs(W - w).sum(1))) if kind == '최적' else None
            mm, _, _ = evaluate(w[None], assets, *sel)
            rows.append(dict(job=tag, 한도=lab, 종류=kind, 비중=fmt_w(w, assets), 선정연=mm['cagr'][0] * 100, 선정MDD=mm['mdd'][0] * 100,
                             **describe(w, assets, oos)))
    for nm, dd in {**BASE, '균등(전 후보)': {a: 1 / len(assets) for a in assets}}.items():
        w = np.array([dd.get(a, 0) for a in assets])
        mm, _, _ = evaluate(w[None], assets, *sel)
        rows.append(dict(job=tag, 한도='기준', 종류=nm, 비중=fmt_w(w, assets), 선정연=mm['cagr'][0] * 100, 선정MDD=mm['mdd'][0] * 100,
                         **describe(w, assets, oos)))
    return pd.DataFrame(rows), W, m


if __name__ == '__main__':
    pd.set_option('display.width', 280); pd.set_option('display.max_colwidth', 48)
    A = ['QQQ', 'SPY', 'SMH', 'K200', 'LEV200', 'IEF', 'GLD']
    B = A + ['TOP5']
    jobs = [('A1', A, ('2007-04-02', '2023-12-29'), ('2024-01-02', '2026-08-31')),
            ('A2', A, ('2000-01-03', '2023-12-29'), ('2024-01-02', '2026-08-31')),
            ('A3', A, ('2007-04-02', '2015-12-31'), ('2016-01-04', '2026-08-31')),
            ('A4', A, ('2000-01-03', '2012-12-31'), ('2013-01-02', '2026-08-31')),
            ('B1', B, ('2010-01-04', '2023-12-29'), ('2024-01-02', '2026-08-31')),
            ('B2', B, ('2010-01-04', '2016-12-30'), ('2017-01-03', '2026-08-31'))]
    outs = []
    for tag, assets, sel, oos in jobs:
        df, W, m = run_job(tag, assets, sel, oos)
        print(f"\n=== {tag}: 선정 {sel[0]}~{sel[1]} → 표본 밖 {oos[0]}~{oos[1]} ===")
        print(df.drop(columns=['job']).round(2).to_string(index=False), flush=True)
        outs.append(df)
        if tag in ('A2', 'B1'):
            full = {k: evaluate(W, assets, a, b)[0] for k, (a, b) in PERIODS.items() if not (tag == 'B1' and k == '2000~')}
            f = pd.DataFrame(W, columns=assets)
            for k, mm in full.items():
                f[f'{k}연'] = mm['cagr'] * 100; f[f'{k}MDD'] = mm['mdd'] * 100; f[f'{k}샤프'] = mm['sharpe']
            f.round(3).to_csv(os.path.join(HERE, f"grid_all_{tag}.csv"), index=False)
    pd.concat(outs).round(2).to_csv(os.path.join(HERE, 'grid_picks.csv'), index=False, encoding='utf-8-sig')
