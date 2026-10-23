"""폭락이 언제 와야 본전인가 (2026-10-21)

A) 공식: 지금 사고 T년 뒤 깊이 D 폭락 → 저점 가치 = (1+r)^T × (1−D). 본전 T* = ln(1/(1−D)) / ln(1+r).
   현금(연 c)으로 기다리다 폭락 저점에 사는 경우와 비교하면 T* = ln(1/(1−D)) / ln((1+r)/(1+c)) — 이보다 늦게 오면 지금 산 쪽이 이김.
B) 역사: 전고점 대비 −20% 이상 하락 구간마다, 저점 가격이 '몇 년 전 가격'까지 되돌렸나
   = 저점일 − (그 전 마지막으로 가격이 저점 이하였던 날). 이보다 일찍 산 사람은 저점에서도 이익.
실행: .venv/bin/python -I docs/research/20261021_weight_design/breakeven.py
"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from drawdown_cycles import series, underwater

if __name__ == '__main__':
    pd.set_option('display.width', 220)
    D = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    print("=== A) 지금 사서 본전 되려면 폭락이 최소 몇 년 뒤 와야 하나 (연수익 r) ===")
    tab = pd.DataFrame({f"r={int(r * 100)}%": [np.log(1 / (1 - d)) / np.log(1 + r) for d in D] for r in (0.06, 0.08, 0.10, 0.12, 0.15)},
                       index=[f"−{int(d * 100)}%" for d in D])
    print(tab.round(1).to_string())
    print("\n=== A') 현금(연 3%)으로 기다리는 쪽이 이기려면 폭락이 몇 년 안에 와야 하나 ===")
    tab2 = pd.DataFrame({f"r={int(r * 100)}%": [np.log(1 / (1 - d)) / np.log((1 + r) / 1.03) for d in D] for r in (0.06, 0.08, 0.10, 0.12, 0.15)},
                        index=[f"−{int(d * 100)}%" for d in D])
    print(tab2.round(1).to_string())
    print("\n=== B) 역대 −20% 이상 하락: 저점이 몇 년 전 가격까지 되돌렸나 ===")
    rows = []
    S = series()
    for nm in ['S&P500 (1950~)', '나스닥100 (1985~)', '코스피 (1980~)', 'QQQ 원화 (2000~)', 'G2-60 혼합 원화 (2000~)']:
        p = S[nm].dropna()
        for a, b, c, d in underwater(p):
            if d > -0.20: continue
            before = p.loc[:a]
            hit = before[before <= p.loc[b]]
            back = (b - hit.index[-1]).days / 365.25 if len(hit) else np.nan
            rows.append(dict(지수=nm, 고점=a.date(), 저점=b.date(), 낙폭=d * 100, 몇년전가격=back,
                             하락기간=(b - a).days / 365.25, 회복=((c - a).days / 365.25) if c is not None else np.nan))
    df = pd.DataFrame(rows)
    print(df.round(1).to_string(index=False))
    df.round(2).to_csv(os.path.join(HERE, 'breakeven_history.csv'), index=False, encoding='utf-8-sig')


def wait_test(p, cash_daily, x, cap_y=5, horizon_y=10):
    """매월 첫 거래일 t 에서: (1) 지금 매수 vs (2) 현금으로 기다리다 오늘 가격 대비 −x 가 되는 첫날 매수(cap_y 년 안에 안 오면 그때 매수).
    horizon_y 년 뒤 가치 비교. 반환: 기다림 승률, 중앙 연 차이(%p), 하락이 실제 온 비율."""
    v = p.to_numpy(); idx = p.index; cc = np.cumprod(1 + cash_daily.reindex(idx).fillna(0).to_numpy())
    starts = [i for i in range(1, len(idx)) if idx[i].month != idx[i - 1].month]
    res = []
    for i in starts:
        end = idx[i] + pd.DateOffset(years=horizon_y)
        if end > idx[-1]: break
        j_end = int(np.searchsorted(idx, end)); j_cap = int(np.searchsorted(idx, idx[i] + pd.DateOffset(years=cap_y)))
        hit = np.where(v[i:j_cap + 1] <= v[i] * (1 - x))[0]
        k = i + hit[0] if len(hit) else j_cap
        now = v[j_end] / v[i]; wait = (cc[k] / cc[i]) * v[j_end] / v[k]
        res.append((wait > now, (wait / now) ** (1 / horizon_y) - 1, len(hit) > 0))
    r = np.array(res, dtype=float)
    return r[:, 0].mean() * 100, np.median(r[:, 1]) * 100, r[:, 2].mean() * 100, len(r)


if __name__ == '__main__':
    print("\n=== C) '−x% 빠지면 사겠다'며 현금으로 기다리면? (5년 안에 안 빠지면 그때 매수, 10년 뒤 비교, 매월 시작) ===")
    S = series()
    sl = pd.read_csv(os.path.join(HERE, 'sleeves_krw.csv'), index_col=0, parse_dates=True)
    rows = []
    for nm, cash in [('S&P500 (1950~)', 0.0), ('나스닥100 (1985~)', 0.0), ('코스피 (1980~)', 0.0),
                     ('QQQ 원화 (2000~)', 'RFK'), ('G2-60 혼합 원화 (2000~)', 'RFK')]:
        p = S[nm].dropna()
        c = sl['RFK'] if cash == 'RFK' else pd.Series(0.0, index=p.index)
        for x in (0.10, 0.20, 0.30):
            win, med, came, n = wait_test(p, c, x)
            rows.append(dict(지수=nm, 기다림=f"−{int(x * 100)}%", 기다림승률=win, 중앙연차이=med, 하락이옴=came, 표본=n))
    print(pd.DataFrame(rows).round(1).to_string(index=False))
    print("(가격 지수 3개는 배당·현금이자 모두 뺌 — 둘이 대략 상쇄; 원화 2개는 총수익·원화 현금 기준금리)")


def ff_market():
    """Ken French 미국 전체 시장(CRSP 가치가중, 배당 포함) 일수익과 1개월 국채 일수익, 1926~"""
    d = pd.read_csv(os.path.join(HERE, 'data_proxy', 'ff_market_daily.csv'), parse_dates=['date']).set_index('date')
    tr = (d.mkt_rf + d.rf) / 100; rf = d.rf / 100
    return (1 + tr).cumprod(), rf


def median_gap(p, cash, x):
    return wait_test(p, cash, x)[1]


if __name__ == '__main__':
    print("\n=== D) 현금 대신 예금·단기채 이자를 받으며 기다리면 (총수익 기준, 5년 안에 안 빠지면 그때 매수, 10년 뒤 비교) ===")
    sl = pd.read_csv(os.path.join(HERE, 'sleeves_krw.csv'), index_col=0, parse_dates=True)
    S = series()
    mk, rf = ff_market()
    cases = [('미국 전체 시장 총수익 (1950~)', mk.loc['1950':], rf.loc['1950':], '국채1개월'),
             ('QQQ 원화 총수익 (2000~)', S['QQQ 원화 (2000~)'].dropna(), sl['RFK'], '기준금리'),
             ('G2-60 혼합 원화 (2000~)', S['G2-60 혼합 원화 (2000~)'].dropna(), sl['RFK'], '기준금리')]
    rows = []
    for nm, p, base, bname in cases:
        base = base.reindex(p.index).fillna(0)
        avg = (1 + base).prod() ** (252 / len(base)) - 1
        for extra in (0.0, 0.01, 0.02):
            cash = base + extra / 252
            for x in (0.10, 0.20, 0.30):
                win, med, came, n = wait_test(p, cash, x)
                rows.append(dict(대상=nm, 이자=f"{bname}+{int(extra * 100)}%p (평균 연 {(avg + extra) * 100:.1f}%)", 기다림=f"−{int(x * 100)}%",
                                 기다림승률=win, 중앙연차이=med, 하락이옴=came))
        # 기다림이 중앙값 기준 본전이 되는 추가 이자(−20%)
        lo, hi = 0.0, 0.30
        for _ in range(25):
            mid = (lo + hi) / 2
            if median_gap(p, base + mid / 252, 0.20) < 0: lo = mid
            else: hi = mid
        rows.append(dict(대상=nm, 이자=f"→ −20% 기다림 본전 이자 = 평균 연 {(avg + hi) * 100:.1f}% ({bname}+{hi * 100:.1f}%p)", 기다림='', 기다림승률=np.nan,
                         중앙연차이=np.nan, 하락이옴=np.nan))
    print(pd.DataFrame(rows).round(1).to_string(index=False))
