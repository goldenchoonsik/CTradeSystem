"""재조정 시점 민감도 — 상위 5 (2026-10-21)

A 달력 분기말(기본: 3·6·9·12월 말 종가 순위 → 다음 거래일 시가)
B 나스닥100 일정(3·6·9·12월 셋째 금요일 종가 순위 → 다음 거래일 시가; 실제 NDX 분기 재조정·12월 정기 변경과 같은 날)
C 연 1회(12월 셋째 금요일)   D 월말
시총은 build_caps 와 같은 방식으로 각 날짜에 다시 계산(그날까지 공시된 주식 수 × 그날 종가).
실행: .venv/bin/python -I docs/research/20261021_qqq_top_n/timing_check.py
"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import build_caps as bc
import topn_backtest as tb
from us_data import load

ticks = list(tb.caps.columns)
SER = {}
for t in ticks:
    df = bc.clean_series(t, [])
    if df.empty: continue
    last = df.sort_values('end').val.iloc[-1]
    k = bc.NOW[t] / last if t in bc.NOW else bc.ADS_PER_ORD.get(t, 1.0)
    if t in bc.NOW and 1 / 1.6 <= k <= 1.6: k = 1.0
    SER[t] = (df, k, load(t)['close'])


def caps_on(dates):
    out = pd.DataFrame(index=dates, columns=list(SER), dtype=float)
    for t, (df, k, close) in SER.items():
        col = []
        for q in dates:
            s, _ = bc.asof(df, q, t); c = close.loc[:q]
            col.append(s * k * c.iloc[-1] / 1e9 if len(c) and not np.isnan(s) and (q - c.index[-1]).days < 10 else np.nan)
        out[t] = col
    return out


def third_fridays(months):
    d = []
    for y in range(2009, 2027):
        for m in months:
            fr = pd.date_range(f'{y}-{m:02d}-01', periods=31, freq='D')
            fr = fr[(fr.month == m) & (fr.weekday == 4)]
            if len(fr) >= 3: d.append(fr[2])
    return [x for x in d if pd.Timestamp('2009-12-01') <= x <= tb.END]


cal = tb.cal
SCHED = {
    'A 달력 분기말': list(pd.date_range('2009-12-31', tb.END, freq='QE')),
    'B NDX 일정(분기 셋째 금)': third_fridays([3, 6, 9, 12]),
    'C 연 1회(12월 셋째 금)': third_fridays([12]),
    'D 월말': list(pd.date_range('2009-12-31', tb.END, freq='ME')),
}
rows, changes = [], {}
for name, dates in SCHED.items():
    dates = sorted({cal[0]} | {cal[cal <= d][-1] for d in dates if d >= cal[0]})   # 출발은 모두 2009-12-31, 휴일이면 직전 거래일
    tb.caps = caps_on(dates)
    tb.REB = {}
    for q in dates:
        k = int(np.searchsorted(cal, q, side='right'))
        if k < len(cal): tb.REB[k] = q
    tb.MEM = {q: {tb.RENAME.get(t, t).replace('.', '-') for t in tb.members_at(q)} for q in dates}
    prev, ch = None, []
    for i, q in sorted(tb.REB.items()):
        top = list(tb.ranked(q, i).index[:5])
        if prev is not None and set(top) != set(prev):
            ch.append(f"{cal[i].date()} +{','.join(sorted(set(top) - set(prev)))} −{','.join(sorted(set(prev) - set(top)))}")
        prev = top
    changes[name] = ch
    for end in [pd.Timestamp('2023-12-29'), tb.END]:
        for sc in ['cap', 'eq']:
            e0, _ = tb.run(5, sc, False, end); _, f1 = tb.run(5, sc, True, end)
            st = tb.stats(e0, f1)
            rows.append(dict(종료=end.date(), 일정=name, 가중=sc, 재조정횟수=len(dates), 종목교체=len(ch),
                             **{k: round(v, 2) for k, v in st.items() if k in ('세전연', '세후연', 'MDD', '샤프')}))
            print(rows[-1], flush=True)
R = pd.DataFrame(rows)
txt = R.to_string(index=False) + "\n\n## 상위 5 종목 교체 이력 (B NDX 일정, 시총 순위)\n" + '\n'.join(changes['B NDX 일정(분기 셋째 금)'])
open(os.path.join(HERE, 'timing_check.txt'), 'w').write(txt)
print(txt)
