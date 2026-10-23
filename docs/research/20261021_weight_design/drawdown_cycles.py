"""최대낙폭은 얼마나 자주 오나 (2026-10-21)

두 가지로 센다(가격 지수, 배당 제외 — 낙폭이 약간 크게 나옴):
1) 하락장 단위(지그재그): 고점 대비 X% 하락하면 하락장 하나, 저점에서 X% 반등하면 다음 국면 시작(X=20 이 통상 '약세장' 정의).
   → 횟수, 연 빈도, 하락장 시작(고점) 사이 간격의 최소/중앙/최대, 고점→저점 기간
2) 전고점 기준(물 아래): 전고점 회복 전까지를 한 구간 — 낙폭 깊이별 회복까지 걸린 기간
데이터: S&P500 1928~(Yahoo ^GSPC), 나스닥 종합 1971~, 나스닥100 1985~, 코스피 1980~·코스피200 1990~(Creon 지수),
       원화 QQQ·G2-60 혼합 2000~(sleeves_krw.csv).
실행: .venv/bin/python -I docs/research/20261021_weight_design/drawdown_cycles.py
"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
KR = "/home/kuzimo/projects/CTradeSystem/DataVault/staging/kr_equity/raw/creon/index_1d"


def yahoo(nm):
    d = pd.read_csv(os.path.join(HERE, 'data_proxy', f'yahoo_{nm}_daily.csv'), parse_dates=['date'])
    return d.set_index('date').close


def creon(code):
    d = pd.read_csv(f"{KR}/{code}_1d.csv"); d['date'] = pd.to_datetime(d.date.astype(str))
    return d.set_index('date').close


def zigzag(p, x):
    """고점 대비 x 하락 = 하락장, 저점 대비 x 반등 = 끝. 반환: [(고점일, 저점일, 깊이)]"""
    out = []; v = p.to_numpy(); idx = p.index
    peak_i = 0; trough_i = 0; state = 'up'
    for i in range(1, len(v)):
        if state == 'up':
            if v[i] > v[peak_i]: peak_i = i
            elif v[i] <= v[peak_i] * (1 - x): state = 'down'; trough_i = i
        else:
            if v[i] < v[trough_i]: trough_i = i
            elif v[i] >= v[trough_i] * (1 + x):
                out.append((idx[peak_i], idx[trough_i], v[trough_i] / v[peak_i] - 1)); state = 'up'; peak_i = i
    if state == 'down': out.append((idx[peak_i], idx[trough_i], v[trough_i] / v[peak_i] - 1))
    return out


def underwater(p):
    """전고점 기준 구간: [(고점일, 저점일, 회복일 or None, 깊이)]"""
    out = []; v = p.to_numpy(); idx = p.index; pk = 0; i = 1
    while i < len(v):
        if v[i] >= v[pk]: pk = i; i += 1; continue
        j = i; tr = i
        while j < len(v) and v[j] < v[pk]:
            if v[j] < v[tr]: tr = j
            j += 1
        out.append((idx[pk], idx[tr], idx[j] if j < len(v) else None, v[tr] / v[pk] - 1))
        pk = j if j < len(v) else pk; i = j + 1
    return out


def series():
    S = pd.read_csv(os.path.join(HERE, 'sleeves_krw.csv'), index_col=0, parse_dates=True)
    w = {'QQQ': .2, 'SMH': .2, 'LEV200': .2, 'K200': .4 / 3, 'IEF': .4 / 3, 'GLD': .4 / 3}
    from grid import sim
    A = list(w); p, ix = sim(np.array([w[a] for a in A])[None], A, '2000-01-03', '2026-08-31')
    return {'S&P500 (1928~)': yahoo('GSPC').loc['1928':], 'S&P500 (1950~)': yahoo('GSPC').loc['1950':],
            '나스닥 종합 (1971~)': yahoo('IXIC'), '나스닥100 (1985~)': yahoo('NDX'),
            '코스피 (1980~)': creon('U001'), '코스피200 (1990~)': creon('U180'),
            'QQQ 원화 (2000~)': (1 + S.loc['2000':, 'QQQ']).cumprod(),
            'G2-60 혼합 원화 (2000~)': pd.Series(p[:, 0], index=ix)}


if __name__ == '__main__':
    pd.set_option('display.width', 220)
    rows = []; detail = {}
    for nm, p in series().items():
        p = p.dropna(); yrs = (p.index[-1] - p.index[0]).days / 365.25
        for x in (0.10, 0.20, 0.30, 0.50):
            z = zigzag(p, x)
            gaps = np.diff([a.toordinal() for a, _, _ in z]) / 365.25 if len(z) > 1 else []
            fall = [(b - a).days / 365.25 for a, b, _ in z]
            rows.append(dict(지수=nm, 기간년=round(yrs), 기준=f"−{int(x * 100)}%", 횟수=len(z), 몇년에한번=yrs / len(z) if z else np.nan,
                             간격최소=np.min(gaps) if len(gaps) else np.nan, 간격중앙=np.median(gaps) if len(gaps) else np.nan,
                             간격최대=np.max(gaps) if len(gaps) else np.nan, 하락기간중앙=np.median(fall) if fall else np.nan,
                             평균깊이=np.mean([d for _, _, d in z]) * 100 if z else np.nan))
            if x == 0.20: detail[nm] = z
        uw = underwater(p)
        for lo, hi in [(0.10, 0.20), (0.20, 0.30), (0.30, 0.50), (0.50, 1.0)]:
            g = [(a, b, c, d) for a, b, c, d in uw if lo <= -d < hi]
            rec = [((c - a).days / 365.25) for a, b, c, d in g if c is not None]
            rows.append(dict(지수=nm, 기간년=round(yrs), 기준=f"회복 {int(lo * 100)}~{int(hi * 100)}%", 횟수=len(g),
                             하락기간중앙=np.median([(b - a).days / 365.25 for a, b, c, d in g]) if g else np.nan,
                             회복중앙=np.median(rec) if rec else np.nan, 회복최대=np.max(rec) if rec else np.nan,
                             미회복=sum(1 for *_, c, d in [(a, b, c, d) for a, b, c, d in g] if c is None)))
    df = pd.DataFrame(rows)
    zz = df[~df.기준.str.startswith('회복')].drop(columns=['회복중앙', '회복최대', '미회복'])
    print("=== 하락장 횟수·간격 (지그재그) ===")
    print(zz.round(1).to_string(index=False))
    print("\n=== 전고점 기준: 깊이별 회복 기간(고점→전고점 회복, 년) ===")
    print(df[df.기준.str.startswith('회복')][['지수', '기준', '횟수', '하락기간중앙', '회복중앙', '회복최대', '미회복']].round(1).to_string(index=False))
    print("\n=== −20% 이상 하락장 목록 ===")
    for nm in ['S&P500 (1928~)', '나스닥100 (1985~)', '코스피 (1980~)', 'G2-60 혼합 원화 (2000~)']:
        print(f"[{nm}] " + ", ".join(f"{a:%Y-%m}→{b:%Y-%m} {d * 100:.0f}%" for a, b, d in detail[nm]))
    df.round(2).to_csv(os.path.join(HERE, 'drawdown_cycles.csv'), index=False, encoding='utf-8-sig')
