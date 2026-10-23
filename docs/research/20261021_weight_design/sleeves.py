"""비중 설계용 구성 요소(슬리브) 원화 일수익 (2026-10-21)

같은 잣대(비교판 board.py 와 같음): 원화 평가, 미국 ETF 배당 재투자(총수익), 세전, 미국 달력(SPY 거래일).
슬리브:
- QQQ·SPY·SMH·IEF·GLD·SPMO : Yahoo 분할 보정 가격 + 배당(총수익) × 원달러
- K200 : 코스피200 TR 지수(U184, 2011~), 그 전은 가격 지수(U180) + 배당 연 1.3%(2011~13 실측 1.1~1.4%), KODEX200 보수 연 0.15% 차감.
         한국 종가를 미국 날짜에 맞춰 직전 값으로 씀(원화 그대로).
- LEV200 / LEV250 : QQQ 종가 > 200일(250일) 이평이면 다음 날 나스닥 2배(QLD 실물, 2006-06 이전은 합성 2배) 보유, 아니면 미 국채 3개월.
         합성 2배 = 2×QQQ 일수익 − 단기금리 − 보수 0.95%/252 (leverage_long.py 에서 실제 QLD 와 ±1%p 검증). 전환 1회 0.1%.
- TOP5 / TOP10 : 당시 나스닥100 시총 상위 5·10 시총가중, 분기 재조정(20261021_qqq_top_n/topn_backtest.run, 비용 포함), 2010-01~
- 상장 전 구간: IEF = 미 10년 국채 금리(FRED DGS10) 대용치(이자 + 듀레이션 7.5 × 금리 변화 − 보수 0.15% + 겹친 24년 차이 0.78%/년 보정, 월수익 상관 0.989),
  GLD = 월평균 금값(datasets/gold-prices) 로그 보간 − 보수 0.4%, SMH(2000-06 전)·SPMO·TOP 은 미 국채 3개월(현금). 원본은 data_proxy/.
실행: .venv/bin/python -I docs/research/20261021_weight_design/sleeves.py  → sleeves_krw.csv
"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '20261021_slot_swing_recompute'))
from us_data import load, ROOT

END = pd.Timestamp('2026-08-31')
TB = {1999: 4.64, 2000: 5.82, 2001: 3.39, 2002: 1.60, 2003: 1.01, 2004: 1.37, 2005: 3.15, 2006: 4.73, 2007: 4.36, 2008: 1.37,
      2009: 0.15, 2010: 0.14, 2011: 0.05, 2012: 0.09, 2013: 0.06, 2014: 0.03, 2015: 0.05, 2016: 0.32, 2017: 0.93, 2018: 1.94,
      2019: 2.06, 2020: 0.37, 2021: 0.05, 2022: 2.02, 2023: 5.07, 2024: 4.97, 2025: 4.20, 2026: 3.90}
BOK = {1999: 4.75, 2000: 5.10, 2001: 4.50, 2002: 4.20, 2003: 3.90, 2004: 3.50,       # 1999~2004 은 콜금리 목표 연평균 근사
       2005: 3.30, 2006: 4.30, 2007: 4.80, 2008: 4.70, 2009: 2.00, 2010: 2.20, 2011: 3.10, 2012: 3.00, 2013: 2.60, 2014: 2.35,
       2015: 1.65, 2016: 1.35, 2017: 1.30, 2018: 1.60, 2019: 1.60, 2020: 0.65, 2021: 0.70, 2022: 2.30, 2023: 3.50,
       2024: 3.45, 2025: 2.70, 2026: 2.50}
K_DIV_PRE2011 = 0.013
K_FEE = 0.0015

cal = load('SPY').loc['1999-03-10':END].index
fx = pd.read_csv(f"{ROOT}/staging/fx_usdkrw/usdkrw_daily.csv", parse_dates=['date']).set_index('date')['usdkrw']
FX = fx.reindex(fx.index.union(cal)).ffill().reindex(cal)
RF = pd.Series([TB[d.year] / 100 / 252 for d in cal], index=cal)          # 달러 현금 일이자
RFK = pd.Series([BOK[d.year] / 100 / 252 for d in cal], index=cal)        # 원화 무위험(샤프용)
fxr = FX / FX.shift(1)


def tr_usd(t):
    """달러 총수익 일수익(상장 전 NaN)."""
    df = load(t)
    p = df.close
    cp = f"{ROOT}/raw/yahoo/corporate_actions/{t}.csv"
    dv = pd.Series(0.0, index=p.index)
    if os.path.exists(cp):
        ca = pd.read_csv(cp, parse_dates=['date'])
        mult = pd.Series(1.0, index=p.index)
        sp = ca[ca.type == 'split']
        for d, n, m in zip(sp.date, sp.numerator, sp.denominator):
            mult[p.index < d] *= n / m
        d_ = ca[ca.type == 'dividend'].groupby('date').amount.sum()
        dv = (d_ / mult.reindex(d_.index).fillna(1.0)).reindex(p.index).fillna(0.0)
    r = (p + dv) / p.shift(1) - 1
    return r.reindex(cal), p.reindex(cal)


def to_krw(r_usd):
    """달러 일수익 → 원화 일수익, 상장 전은 달러 현금."""
    r = r_usd.copy()
    r[r.isna()] = RF[r.isna()]
    return (1 + r) * fxr - 1


def ief_proxy():
    y = pd.read_csv(os.path.join(HERE, 'data_proxy', 'DGS10.csv'), parse_dates=['observation_date']).set_index('observation_date')['DGS10']
    y = pd.to_numeric(y, errors='coerce').ffill()
    y = y.reindex(y.index.union(cal)).ffill().reindex(cal) / 100
    return y.shift(1) / 252 - 7.5 * (y - y.shift(1)) - 0.0015 / 252 + 0.0078 / 252    # +0.78%/년 = 실물과 겹친 24년 차이(볼록성·만기 구조) 보정


def gold_proxy():
    g = pd.read_csv(os.path.join(HERE, 'data_proxy', 'gold_monthly.csv'))
    g.index = pd.to_datetime(g.Date + '-15')
    lg = np.log(g.Price).reindex(g.index.union(cal)).interpolate(method='time').reindex(cal)
    return np.exp(lg.diff()) - 1 - 0.004 / 252


def kospi200():
    def rd(c):
        d = pd.read_csv(f"{ROOT}/staging/kr_equity/raw/creon/index_1d/{c}_1d.csv")
        d['date'] = pd.to_datetime(d.date.astype(str)); return d.set_index('date').close
    tr, px = rd('U184'), rd('U180')
    r_px = px.pct_change()
    r_tr = tr.pct_change().reindex(px.index)
    kd = K_DIV_PRE2011 / 252
    r = r_tr.where(px.index > tr.index[0], r_px + kd) - K_FEE / 252
    lvl = (1 + r.fillna(0)).cumprod()
    lvl = lvl.reindex(lvl.index.union(cal)).ffill().reindex(cal)     # 미국 날짜에 직전 한국 종가
    return lvl / lvl.shift(1) - 1


def lev_trend(n):
    rq, pq = tr_usd('QQQ')
    rl, _ = tr_usd('QLD')
    syn = 2 * rq - RF - 0.0095 / 252
    r2 = rl.where(rl.notna(), syn)
    on = (pq > pq.rolling(n).mean()).shift(1).fillna(False).astype(bool)    # 어제 종가 신호 → 오늘 보유
    sw = on.astype(int).diff().abs().fillna(0)
    r = pd.Series(np.where(on, r2, RF), index=cal) - sw * 0.001
    return (1 + r) * fxr - 1, on


def build():
    R, inc = {}, {}
    PROXY = {'IEF': ief_proxy(), 'GLD': gold_proxy()}
    for t in ['QQQ', 'SPY', 'SMH', 'IEF', 'GLD', 'SPMO']:
        r, p = tr_usd(t)
        inc[t] = p.first_valid_index().date()
        big = int((r.abs() > 0.4).sum())
        if big: print(f"[경고] {t} 하루 ±40% 봉 {big}개")
        if t in PROXY:
            px_ = PROXY[t]; both = r.notna() & px_.notna()
            a_, b_ = (1 + r[both]).prod(), (1 + px_[both]).prod(); yy = both.sum() / 252
            ma, mb = (1 + r[both]).resample('ME').prod() - 1, (1 + px_[both]).resample('ME').prod() - 1
            print(f"[대용치 검증] {t} 겹친 {yy:.1f}년: 실물 {a_ ** (1 / yy) * 100 - 100:.2f}% vs 대용 {b_ ** (1 / yy) * 100 - 100:.2f}%/년, 월수익 상관 {ma.corr(mb):.3f}")
            r = r.where(r.notna() | (r.index > p.first_valid_index()), px_)
        R[t] = to_krw(r)
    R['K200'] = kospi200(); inc['K200'] = '1990 (TR 2011~)'
    for n in (200, 250):
        R[f'LEV{n}'], on = lev_trend(n); inc[f'LEV{n}'] = 'QLD 2006-06, 그 전 합성'
    sys.path.insert(0, os.path.join(HERE, '..', '20261021_qqq_top_n'))
    import topn_backtest as tb
    for n in (5, 10):
        eq, _ = tb.run(n, 'cap', False, END)
        r = eq.pct_change().reindex(cal)
        r[r.isna()] = ((1 + RF) * fxr - 1)[r.isna()]
        R[f'TOP{n}'] = r; inc[f'TOP{n}'] = str(eq.index[1].date())
    R['CASH'] = (1 + RF) * fxr - 1; inc['CASH'] = '-'
    df = pd.DataFrame(R).loc['2000-01-03':]
    df['RFK'] = RFK
    return df, inc


if __name__ == '__main__':
    df, inc = build()
    df.to_csv(os.path.join(HERE, 'sleeves_krw.csv'))
    print('상장(실데이터 시작):', inc)
    for a, b in [('2000-01-03', '2026-08-31'), ('2007-04-02', '2023-12-29'), ('2007-04-02', '2026-08-31'), ('2010-01-04', '2026-08-31')]:
        s = df.loc[a:b]; y = (s.index[-1] - s.index[0]).days / 365.25
        e = (1 + s.drop(columns='RFK')).cumprod()
        print(f"--- {a}~{b}: " + ", ".join(f"{c} {((e[c].iloc[-1]) ** (1 / y) - 1) * 100:.1f}%/MDD {((e[c] / e[c].cummax() - 1).min()) * 100:.0f}%" for c in e.columns))
