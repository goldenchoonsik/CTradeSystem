"""'결론이 QQQ 라면 왜 기관·대가들은 QQQ 를 안 사나' 근거 (2026-10-21)

A) 나스닥100 vs S&P500 10년 굴리기(가격 지수 1985~, 매월 시작) + QQQ vs SPY 총수익(1999~)
B) 미국 10개 업종(Ken French, 가치가중, 배당 포함, 1926-07~2026-08): 10년 단위 1등 업종과 다음 10년 순위, 기술(HiTec) 순위
실행: .venv/bin/python -I docs/research/20261021_weight_design/why_not_qqq.py
"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '20261021_slot_swing_recompute'))
from drawdown_cycles import yahoo
import sleeves as sl


def rolling_win(a, b, years):
    a = a.resample('ME').last().dropna(); b = b.resample('ME').last().reindex(a.index).dropna(); a = a.reindex(b.index)
    n = years * 12; out = []
    for i in range(len(a) - n):
        ra = (a.iloc[i + n] / a.iloc[i]) ** (1 / years) - 1; rb = (b.iloc[i + n] / b.iloc[i]) ** (1 / years) - 1
        out.append((a.index[i], ra - rb))
    s = pd.Series(dict(out))
    return s


if __name__ == '__main__':
    pd.set_option('display.width', 220)
    ndx, spx = yahoo('NDX'), yahoo('GSPC').loc['1985':]
    q, _ = sl.tr_usd('QQQ'); s, _ = sl.tr_usd('SPY')
    qq = (1 + q.fillna(0)).cumprod().loc['1999-04':]; ss = (1 + s.fillna(0)).cumprod().loc['1999-04':]
    print("=== A) 나스닥100 − S&P500 연수익 차이, 굴리기 ===")
    for nm, a, b in [('NDX vs S&P 가격(1985~)', ndx, spx), ('QQQ vs SPY 총수익(1999~)', qq, ss)]:
        for y in (5, 10):
            d = rolling_win(a, b, y)
            print(f"{nm} {y}년: 나스닥 승률 {(d > 0).mean() * 100:.0f}% (n={len(d)}), 차이 중앙 {d.median() * 100:+.1f}%p, "
                  f"최악 {d.min() * 100:+.1f}%p({d.idxmin():%Y-%m} 시작), 최고 {d.max() * 100:+.1f}%p({d.idxmax():%Y-%m} 시작)")
        d = rolling_win(a, b, 10)
        by = d.groupby(d.index.year).mean() * 100
        print("  10년 차이(시작 연도별 평균, %p): " + ", ".join(f"{k}:{v:+.0f}" for k, v in by.items() if k % 2 == 1 or k == by.index[-1]))
    print("\n=== B) 미국 10개 업종, 10년 단위 연복리 순위 (Ken French, 배당 포함) ===")
    ind = pd.read_csv(os.path.join(HERE, 'data_proxy', 'ff_10ind_vw_monthly.csv'))
    ind.index = pd.to_datetime(ind.ym.astype(str), format='%Y%m'); ind = ind.drop(columns='ym').replace([-99.99, -999], np.nan) / 100
    ind.columns = [c.strip() for c in ind.columns]
    KO = {'NoDur': '필수소비재', 'Durbl': '내구재(자동차)', 'Manuf': '제조', 'Enrgy': '에너지', 'HiTec': '기술', 'Telcm': '통신',
          'Shops': '유통', 'Hlth': '헬스케어', 'Utils': '유틸리티', 'Other': '기타(금융 등)'}
    rows = []; prev_best = None
    for dec in range(1930, 2030, 10):
        seg = ind.loc[f'{dec}':f'{dec + 9}']
        if len(seg) < 24: continue
        y = len(seg) / 12
        c = ((1 + seg).prod() ** (1 / y) - 1) * 100
        rank = c.rank(ascending=False).astype(int)
        best = c.idxmax(); worst = c.idxmin()
        rows.append(dict(기간=f"{dec}s" if dec < 2020 else "2020~26.08", 일등=f"{KO[best]} {c[best]:.1f}%", 꼴등=f"{KO[worst]} {c[worst]:.1f}%",
                         기술순위=f"{rank['HiTec']}위 ({c['HiTec']:.1f}%)", 직전1등의순위=(f"{KO[prev_best]} {rank[prev_best]}위" if prev_best else '-')))
        prev_best = best
    print(pd.DataFrame(rows).to_string(index=False))
    full = ((1 + ind.loc['1926-07':]).prod() ** (12 / len(ind)) - 1) * 100
    print("\n1926-07~2026-08 전체 연복리: " + ", ".join(f"{KO[k]} {v:.1f}%" for k, v in full.sort_values(ascending=False).items()))
