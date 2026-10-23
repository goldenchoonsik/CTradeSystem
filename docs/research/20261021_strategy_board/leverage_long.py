"""레버리지+추세 전략 장기 검증 (2026-10-21) — 합성 2배·3배로 닷컴(1999~)·금융위기 포함
합성 L배 일수익 = L × 기초 일수익 − (L−1) × 단기금리 − 운용보수(연 0.95%) / 252. 실제 QLD/TQQQ 와 겹치는 기간으로 검증.
신호: 기초자산 종가 > N일 이평 → 다음 날 시가... 단순화를 위해 다음 날 종가 기준 진입(1일 지연), 이탈 시 현금(국채 3개월).
비용: 전환 1회 편도 0.1%. 세금: 이 표는 세전(달러). 세후·원화는 board.py 와 같은 장부로 별도.
"""
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '20261021_slot_swing_recompute'))
from us_data import load
TB = {1993: 3.0, 1994: 4.3, 1995: 5.5, 1996: 5.0, 1997: 5.1, 1998: 4.8, 1999: 4.64, 2000: 5.82, 2001: 3.39, 2002: 1.60, 2003: 1.01,
      2004: 1.37, 2005: 3.15, 2006: 4.73, 2007: 4.36, 2008: 1.37, 2009: 0.15, 2010: 0.14, 2011: 0.05, 2012: 0.09, 2013: 0.06,
      2014: 0.03, 2015: 0.05, 2016: 0.32, 2017: 0.93, 2018: 1.94, 2019: 2.06, 2020: 0.37, 2021: 0.05, 2022: 2.02, 2023: 5.07,
      2024: 4.97, 2025: 4.20, 2026: 3.90}
END = next((a.split('=')[1] for a in sys.argv if a.startswith('--end=')), '2026-08-31')
def tr_ret(t):
    df = load(t); p = df.close
    ca = pd.read_csv(f"/home/kuzimo/projects/CTradeSystem/DataVault/raw/yahoo/corporate_actions/{t}.csv", parse_dates=['date'])
    mult = pd.Series(1.0, index=p.index)
    sp = ca[ca.type == 'split']
    for d, n, m in zip(sp.date, sp.numerator, sp.denominator): mult[p.index < d] *= n / m
    dv = ca[ca.type == 'dividend'].groupby('date').amount.sum()
    dv = (dv / mult.reindex(dv.index).fillna(1)).reindex(p.index).fillna(0)
    return ((p + dv) / p.shift(1) - 1).loc[:END], p.loc[:END]
def synth(r, L):
    rf = pd.Series([TB[d.year] / 100 / 252 for d in r.index], index=r.index)
    return L * r - (L - 1) * rf - 0.0095 / 252, rf
def stats(r, rf):
    e = (1 + r.fillna(0)).cumprod(); y = (e.index[-1] - e.index[0]).days / 365.25
    return e.iloc[-1] ** (1 / y) - 1, (e / e.cummax() - 1).min(), (r - rf).mean() / r.std() * np.sqrt(252)
def trend(r_lev, px, rf, n, cost=0.001):
    on = (px > px.rolling(n).mean()).shift(1).fillna(False)      # 어제 종가 신호 → 오늘 보유
    sw = on.astype(int).diff().abs().fillna(0)
    return np.where(on, r_lev, rf) - sw * cost
if __name__ == '__main__':
    for base, real2, real3 in [('QQQ', 'QLD', 'TQQQ'), ('SPY', 'SSO', 'UPRO')]:
        r, px = tr_ret(base)
        for L, real in [(2, real2), (3, real3)]:
            rs, rf = synth(r, L); rr, _ = tr_ret(real)
            common = rr.dropna().index[1:]
            a = stats(rs.loc[common], rf.loc[common]); b = stats(rr.loc[common], rf.loc[common])
            print(f"[검증] 합성 {L}x {base} vs 실제 {real} ({common[0].date()}~): 연 {a[0]*100:.1f}% vs {b[0]*100:.1f}%, MDD {a[1]*100:.0f}% vs {b[1]*100:.0f}%")
        start = r.index[0] + pd.Timedelta(days=420)
        print(f"=== {base} 기준, {start.date()} ~ {END} (세전·달러) ===")
        rows = []
        for L in (1, 2, 3):
            rs, rf = synth(r, L) if L > 1 else (r, synth(r, 2)[1])
            c, m, s_ = stats(rs.loc[start:], rf.loc[start:]); rows.append((f"{L}배 보유", c, m, s_))
            for n in (150, 200, 250):
                t = pd.Series(trend(rs, px, rf, n), index=rs.index).loc[start:]
                c, m, s_ = stats(t, rf.loc[start:]); rows.append((f"{L}배 + {n}일선", c, m, s_))
        for nm, c, m, s in rows: print(f"  {nm:<14} 연 {c*100:6.1f}%  MDD {m*100:6.1f}%  샤프 {s:5.2f}")
        # 구간별
        for L in (2, 3):
            rs, rf = synth(r, L); t = pd.Series(trend(rs, px, rf, 200), index=rs.index)
            out = []
            for a, b in [('2000-01-01', '2009-12-31'), ('2010-01-01', '2019-12-31'), ('2020-01-01', min(END, '2023-12-31')), ('2024-01-01', END)]:
                if a > END: continue
                c1 = stats(r.loc[a:b], rf.loc[a:b])[0]; c2 = stats(t.loc[a:b], rf.loc[a:b])[0]; c3 = stats(rs.loc[a:b], rf.loc[a:b])[0]
                out.append(f"{a[:4]}~{b[:4]}: 1배보유 {c1*100:+.1f}% / {L}배보유 {c3*100:+.1f}% / {L}배+200일 {c2*100:+.1f}%")
            print(f"  [{L}배 구간별] " + " | ".join(out))
