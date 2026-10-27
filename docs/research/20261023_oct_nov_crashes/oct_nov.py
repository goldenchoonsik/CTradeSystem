"""SPY(1993~) 연도별 10~11월 구간: 9월말 대비 수익·구간 내 고점 대비 최대 낙폭·최악의 하루. 가격 수익(배당 제외).
실행: .venv/bin/python -I docs/research/20261023_oct_nov_crashes/oct_nov.py"""
import pandas as pd
d = pd.read_csv('DataVault/raw/yahoo/candle_1d/SPY_1d.csv', parse_dates=['timestamp']).set_index('timestamp').close
r = d.pct_change()
rows = []
for y in range(1993, 2027):
    base = d[:f'{y}-09-30']
    if len(base) == 0 or base.index[-1].year != y: continue
    w = d[f'{y}-10-01':f'{y}-11-30']
    if len(w) < 30: continue
    s = pd.concat([base.iloc[[-1]], w])
    rows.append({'연도': y, '9월말대비': w.iloc[-1]/s.iloc[0]-1, '구간최대낙폭': (s/s.cummax()-1).min(),
                 '최악의하루': r[w.index].min(), '최악일': r[w.index].idxmin().strftime('%m-%d')})
T = pd.DataFrame(rows).set_index('연도')
pd.set_option('display.width', 200)
print(T.round(3).to_string())
print('\n구간 낙폭 -10% 이하 연도:', T.index[T.구간최대낙폭<=-.10].tolist())
print('9월말 대비 10~11월 수익 음수 연도 수', int((T['9월말대비']<0).sum()), '/', len(T))
print('중앙값', T['9월말대비'].median().round(3), ' 구간낙폭 중앙값', T.구간최대낙폭.median().round(3))
