"""'최적 N' 안정성 시험 (2026-10-21) — topn_backtest 의 데이터·엔진 재사용

① 연도별: 상위 N(시총가중) − 명단 전체(합성) 연 수익 차이, 해마다 1등 N
② 앞/뒤 나누기: 2010~2017 에서 가장 좋았던 N 을 2018-01~2026-08 에 그대로 적용했을 때 순위·QQQ 대비
③ 걷기 시험: 매년 1월 재조정 때 직전 3년(또는 5년) 수익 1등 N 으로 갈아탐(세후 포함, 두 종료 시점)
④ 2000년대 참고: 2000-03 당시 나스닥 시총 1~4위(MSFT·CSCO·INTC·ORCL) 동일가중 vs QQQ (Yahoo, 배당 제외 가격)
실행: .venv/bin/python -I docs/research/20261021_qqq_top_n/stability.py
"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import topn_backtest as tb

NS, END = tb.NS, tb.END
cur = {}
for sc in ['cap', 'eq']:
    for n in NS:
        cur[(sc, n)], _ = tb.run(n, sc, False)
q_e, _ = tb.bh('QQQ', False)


def cg(e, a, b):
    s = e.loc[a:b]
    y = (s.index[-1] - s.index[0]).days / 365.25
    return ((s.iloc[-1] / s.iloc[0]) ** (1 / y) - 1) * 100


out = []
# ① 연도별
yr = {}
for (sc, n), e in cur.items():
    a = e.resample('YE').last(); a = pd.concat([pd.Series([e.iloc[0]], index=[e.index[0]]), a]).pct_change().dropna() * 100
    yr[(sc, n)] = a.set_axis(a.index.year)
qa = q_e.resample('YE').last(); qa = pd.concat([pd.Series([q_e.iloc[0]], index=[q_e.index[0]]), qa]).pct_change().dropna() * 100
qa = qa.set_axis(qa.index.year)
Y = pd.DataFrame({f"{n}": yr[('cap', n)] - yr[('cap', 100)] for n in NS[:-1]})
Y['QQQ'] = qa - yr[('cap', 100)]
best = pd.DataFrame({n: yr[('cap', n)] for n in NS}).idxmax(axis=1)
Y['1등N'] = best
out.append("## ① 연도별 상위 N(시총가중) − 명단 전체(합성) 수익 차이 (%p, 원화 세전)\n" + Y.round(1).to_string())

# ② 앞/뒤
rows = []
for sc in ['cap', 'eq']:
    for n in NS:
        rows.append(dict(가중=sc, N=n, 앞_2010_2017=cg(cur[(sc, n)], '2010-01-01', '2017-12-31'),
                         뒤_2018_2026=cg(cur[(sc, n)], '2017-12-29', '2026-08-31'), 뒤_2018_2023=cg(cur[(sc, n)], '2017-12-29', '2023-12-29')))
D = pd.DataFrame(rows)
D['앞순위'] = D.groupby('가중').앞_2010_2017.rank(ascending=False).astype(int)
D['뒤순위'] = D.groupby('가중').뒤_2018_2026.rank(ascending=False).astype(int)
D['뒤순위_2023'] = D.groupby('가중').뒤_2018_2023.rank(ascending=False).astype(int)
qq = dict(앞=cg(q_e, '2010-01-01', '2017-12-31'), 뒤=cg(q_e, '2017-12-29', '2026-08-31'), 뒤23=cg(q_e, '2017-12-29', '2023-12-29'))
out.append(f"\n## ② 앞(2010~2017)·뒤(2018~) 연복리 (%, 원화 세전) — QQQ 앞 {qq['앞']:.1f} / 뒤 {qq['뒤']:.1f} / 뒤~2023 {qq['뒤23']:.1f}\n" + D.round(1).to_string(index=False))

# ③ 걷기 시험
def walk(sc, look, tax, end):
    jan = {i: q for i, q in tb.REB.items() if q.month == 12}
    choice = {}
    for i, q in sorted(tb.REB.items()):
        if q.month == 12 or not choice:
            past = {n: cur[(sc, n)].loc[:q] for n in NS}
            ref = q - pd.DateOffset(years=look)
            if ref < cur[(sc, NS[0])].index[0] - pd.Timedelta(days=10):
                choice[i] = 100                                        # 이력 부족 → 명단 전체
            else:
                base = lambda n: past[n].loc[:ref].iloc[-1] if len(past[n].loc[:ref]) else past[n].iloc[0]
                choice[i] = max(NS, key=lambda n: past[n].iloc[-1] / base(n))
        else:
            choice[i] = choice[max(k for k in choice if k < i)]
    e, fin = tb.run(0, sc, tax, end, sel=lambda i: choice[i])
    return e, fin, choice


rows = []
for end in [pd.Timestamp('2023-12-29'), END]:
    for sc in ['cap', 'eq']:
        for look in [3, 5]:
            e0, _, ch = walk(sc, look, False, end); _, f1, _ = walk(sc, look, True, end)
            st = tb.stats(e0, f1)
            picks = pd.Series({tb.REB[i].year + 1: n for i, n in ch.items() if tb.REB[i].month == 12})
            rows.append(dict(종료=end.date(), 가중=sc, 직전=f"{look}년", **{k: round(v, 2) for k, v in st.items()},
                             고른N=' '.join(f"{y % 100:02d}:{n}" for y, n in picks.items())))
    for t in ['QQQ']:
        e0, _ = tb.bh(t, False, end); _, f1 = tb.bh(t, True, end)
        rows.append(dict(종료=end.date(), 가중='QQQ', 직전='-', **{k: round(v, 2) for k, v in tb.stats(e0, f1).items()}))
W = pd.DataFrame(rows)
out.append("\n## ③ 걷기 시험(매년 1월, 직전 3·5년 1등 N 으로 갈아탐)\n" + W.to_string(index=False))

# ④ 2000년대
from us_data import load
names = ['MSFT', 'CSCO', 'INTC', 'ORCL']
P = pd.DataFrame({t: load(t).close for t in names + ['QQQ']}).loc['2000-03-01':'2016-01-05']
for a, b in [('2000-03-01', '2009-12-31'), ('2000-03-01', '2015-12-31')]:
    s = P.loc[a:b]; m = s.iloc[-1] / s.iloc[0]
    out.append(f"\n## ④ {a}~{b} 가격 배수: " + ', '.join(f"{t} {m[t]:.2f}" for t in names) +
               f" | 4종목 동일가중 {m[names].mean():.2f} vs QQQ {m['QQQ']:.2f}")
txt = '\n'.join(out)
open(os.path.join(HERE, 'stability.txt'), 'w').write(txt)
print(txt)
