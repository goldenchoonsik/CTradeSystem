"""K-Titan(K-Top5) 스윙 정정 재검증 (2026-10-21)

원본(backtest_theme_titan_swing.py 등)은 청산일에 진입 이후 누적 수익률을 한 번 더 더했다(이중 계산).
여기서는 슬롯별 실제 보유 수량·현금으로 계좌를 평가한다.
단계: L1 회계 수정 → L2 시총 오염(mcap_approx) 제거 → L3 시가 진입·장중 손절/익절(갭 반영)
      → L4 연도별 거래세·한국은행 기준금리 현금이자 → 대조군(신호 없음)·벤치마크·테마 풀·Top10
실행: .venv/bin/python docs/research/20261021_k_titan_theme_swing/audit_corrected_engine.py [--grid]
"""
import sys, importlib.util
import numpy as np, pandas as pd

ROOT = "/home/kuzimo/projects/CTradeSystem"
R = f"{ROOT}/DataVault/staging/kr_equity"
uni = pd.read_csv(f"{R}/universe.csv")
nm = dict(zip(uni.code, uni.name))
kospi = set(uni[(uni.market == 'KOSPI') & (uni.is_preferred == 0) & (uni.is_spac == 0)].code)
THEME = {
    "반도체": ["A005930", "A000660", "A042700"],
    "2차전지": ["A373220", "A006400", "A051910", "A086520"],
    "바이오": ["A207940", "A068270", "A196170"],
    "자동차": ["A005380", "A000270", "A012330"],
    "방산조선": ["A012450", "A042660", "A010140", "A267260"],
    "플랫폼": ["A035420", "A035720"],
    "전력원전": ["A267260", "A034020", "A052690"],
    "금융": ["A105560", "A055550", "A086790"],
}
codes = sorted(kospi | {c for v in THEME.values() for c in v})

idx = pd.read_parquet(f"{R}/derived/index_1d.parquet")
u180 = idx[idx.code == 'U180'].set_index('date').sort_index()['close']
cal = u180.loc['2013-01-01':'2024-12-31'].index

px = pd.read_parquet(f"{R}/derived/prices_1d.parquet", columns=['code', 'date', 'open', 'high', 'low', 'close'],
                     filters=[('date', '>=', pd.Timestamp('2012-01-01')), ('date', '<=', pd.Timestamp('2024-12-31'))])
px = px[px.code.isin(codes)]
ext = pd.read_parquet(f"{R}/derived/daily_ext.parquet",
                      columns=['code', 'date', 'market_cap', 'mcap_approx', 'foreign_hold_pct', 'inst_net_qty'],
                      filters=[('date', '>=', pd.Timestamp('2012-01-01')), ('date', '<=', pd.Timestamp('2024-12-31'))])
ext = ext[ext.code.isin(codes)]

def piv(df, col):
    return df.pivot(index='date', columns='code', values=col).sort_index()

C = piv(px, 'close'); cols = C.columns
O, H, L = (piv(px, k).reindex(columns=cols) for k in ('open', 'high', 'low'))
MC = piv(ext, 'market_cap').reindex(index=C.index, columns=cols)
AP = piv(ext, 'mcap_approx').reindex(index=C.index, columns=cols)
FG = piv(ext, 'foreign_hold_pct').reindex(index=C.index, columns=cols)
IN = piv(ext, 'inst_net_qty').reindex(index=C.index, columns=cols)

ma20 = C.rolling(20, min_periods=10).mean(); ma200 = C.rolling(200, min_periods=100).mean()
d_ = C.diff(); up = d_.clip(lower=0); dn = -d_.clip(upper=0)
rsi = 100 - 100 / (1 + up.ewm(com=13, adjust=False).mean() / dn.ewm(com=13, adjust=False).mean())
fg5 = FG.diff(5); in5 = IN.rolling(5, min_periods=3).sum()
sig = ((C / ma200 >= 0.85) & (((C / ma20 >= 0.95) & (C / ma20 <= 1.03)) | (rsi <= 42.0))
       & ((fg5 > 0) | (in5 > 0)) & C.notna() & ma20.notna() & ma200.notna())

# align to calendar → numpy
def A(df): return df.reindex(cal).to_numpy(dtype=float)
Cn, On, Hn, Ln = A(C), A(O), A(H), A(L)
SIG = sig.reindex(cal).fillna(False).to_numpy(dtype=bool)
MCn = A(MC); APn = A(AP)
MC_clean = np.where(APn == 1, np.nan, MCn)
kospi_mask = np.array([c in kospi for c in cols])
ci = {c: i for i, c in enumerate(cols)}

months = [i for i in range(1, len(cal)) if cal[i].month != cal[i - 1].month and cal[i] >= pd.Timestamp('2013-03-01')]
START = months[0]
mset = set(months)

def topn(mc, n):
    def f(i):  # i = 오늘 index, 전일 시총 사용
        row = np.where(kospi_mask, mc[i - 1], np.nan)
        ok = ~np.isnan(row)
        order = np.argsort(-np.where(ok, row, -np.inf))
        return [int(j) for j in order[:min(n, ok.sum())]]
    return f
U_TOP5_ORIG = topn(MCn, 5)
U_TOP5_CLEAN = topn(MC_clean, 5)
U_TOP10_CLEAN = topn(MC_clean, 10)
def U_THEME(i):
    pool = []
    for cs in THEME.values():
        v = [(MCn[i - 1, ci[c]], ci[c]) for c in cs if c in ci and not np.isnan(MCn[i - 1, ci[c]])]
        if v:
            pool.append(max(v)[1])
    return pool

# 현금 금리: 한국은행 기준금리 연평균 근사
BOK = {2013: 2.60, 2014: 2.35, 2015: 1.65, 2016: 1.35, 2017: 1.30, 2018: 1.60, 2019: 1.60,
       2020: 0.65, 2021: 0.70, 2022: 2.30, 2023: 3.50, 2024: 3.45}
def cash_daily(i, real):
    return (BOK[cal[i].year] if real else 2.5) / 100 / 252
def sell_tax(i):  # 코스피 거래세+농특세
    d = cal[i]
    if d < pd.Timestamp('2019-05-30'): return 0.0030
    if d.year <= 2020: return 0.0025
    if d.year <= 2022: return 0.0023
    if d.year == 2023: return 0.0020
    return 0.0018

def run(ufn, mode='intraday', entry='open', signal=True, real=False, tp1=.10, tp2=.20, sl=-.06, trail=-.05, hold=40,
        nslot=5, rng=None, p_enter=None):
    """mode: 'close' = 종가 판정·종가 체결(원본 판정 규칙 그대로, 회계만 수정)
             'intraday' = 손절/익절은 장중 지정가(갭이면 시가), 트레일링·만기는 종가."""
    sl_ = [dict(val=1.0 / nslot, sh=0.0, cash=1.0 / nslot, t=None) for _ in range(nslot)]
    eq = np.full(len(cal), np.nan); eq[:START] = 1.0
    trades = []; universe = []
    for i in range(START, len(cal)):
        if i in mset or not universe:
            universe = ufn(i)
        buy_c = 0.00035 + (0.00015 if real else 0.0)
        sell_c = (sell_tax(i) + 0.0007) if real else 0.0027
        r_cash = cash_daily(i, real)
        # 1) 진입 (전일 종가 신호 → 오늘 시가 또는 종가)
        held = {s['t'] for s in sl_ if s['t'] is not None}
        empty = [s for s in sl_ if s['t'] is None]
        if empty:
            cand = [j for j in universe if j not in held and not np.isnan(Cn[i - 1, j])
                    and (SIG[i - 1, j] if signal else True)]
            if rng is not None:
                cand = [j for j in cand if rng.random() < p_enter]
                rng.shuffle(cand)
            for s, j in zip(empty, cand):
                p = On[i, j] if entry == 'open' else Cn[i, j]
                if np.isnan(p) or p <= 0:
                    continue
                s.update(t=j, e=p, pk=p, d=0, half=False, cin=s['cash'], i0=i)
                s['sh'] = s['cash'] * (1 - buy_c) / p; s['cash'] = 0.0
        # 2) 보유 관리
        for s in sl_:
            if s['t'] is None:
                s['cash'] *= 1 + r_cash; continue
            j = s['t']; o, h, l, c = On[i, j], Hn[i, j], Ln[i, j], Cn[i, j]
            if np.isnan(c):
                continue
            s['cash'] *= 1 + r_cash
            if s.get('i0') == i:   # 진입 당일: 시가 진입이면 종가까지 손익만 반영
                continue
            s['d'] += 1
            e = s['e']; why = None
            def sell(frac, price):
                q = s['sh'] * frac
                s['cash'] += q * price * (1 - sell_c); s['sh'] -= q
            if mode == 'intraday':
                stop, t1, t2 = e * (1 + sl), e * (1 + tp1), e * (1 + tp2)
                if o <= stop: sell(1, o); why = 'SL(갭)'
                elif l <= stop: sell(1, stop); why = 'SL'
                else:
                    if not s['half'] and h >= t1:
                        sell(0.5, max(o, t1)); s['half'] = True
                    if h >= t2:
                        sell(1, max(o, t2)); why = 'TP2'
                if why is None:
                    s['pk'] = max(s['pk'], c)
                    g = c / e - 1; dd = c / s['pk'] - 1
                    if g >= tp1 and dd <= trail: sell(1, c); why = 'TRAIL'
                    elif s['d'] >= hold: sell(1, c); why = 'TIME'
            else:  # close: 원본과 같은 순서·같은 판정, 체결은 실제 종가
                s['pk'] = max(s['pk'], c); g = c / e - 1; dd = c / s['pk'] - 1
                if g >= tp2: sell(1, c); why = 'TP2'
                elif g >= tp1 and not s['half']:
                    sell(0.5, c); s['half'] = True
                elif g >= tp1 and dd <= trail: sell(1, c); why = 'TRAIL'
                elif g <= sl: sell(1, c); why = 'SL'
                elif s['d'] >= hold: sell(1, c); why = 'TIME'
            if why:
                trades.append(dict(entry=cal[s['i0']], exit=cal[i], code=cols[j], name=nm.get(cols[j], cols[j]),
                                   ret=s['cash'] / s['cin'] - 1, days=s['d'], why=why))
                s['t'] = None; s['sh'] = 0.0
        # 3) 평가
        tot = 0.0
        for s in sl_:
            v = s['cash']
            if s['t'] is not None:
                c = Cn[i, s['t']]
                if np.isnan(c):  # 거래정지 등: 마지막 종가
                    k = i
                    while np.isnan(Cn[k, s['t']]): k -= 1
                    c = Cn[k, s['t']]
                v += s['sh'] * c
            tot += v
        eq[i] = tot
    return pd.Series(eq, index=cal), pd.DataFrame(trades)

def metrics(eq, real=False):
    e = eq.iloc[START:] / eq.iloc[START]
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    r = e.pct_change().dropna()
    rf = np.array([cash_daily(cal.get_loc(d), real) for d in r.index])
    return dict(total=e.iloc[-1] - 1, cagr=e.iloc[-1] ** (1 / yrs) - 1, mdd=(e / e.cummax() - 1).min(),
                sharpe=(r - rf).mean() / r.std() * np.sqrt(252), vol=r.std() * np.sqrt(252))

def tstats(t):
    if t is None or len(t) == 0: return {}
    w = t[t.ret > 0].ret; l = t[t.ret <= 0].ret
    return dict(n=len(t), win=len(w) / len(t), pf=w.sum() / -l.sum() if len(l) else np.inf,
                avg=t.ret.mean(), tstat=t.ret.mean() / t.ret.std() * np.sqrt(len(t)))

def bh_ew(ufn):
    eq = np.full(len(cal), np.nan); eq[:START] = 1.0; universe = []
    for i in range(START, len(cal)):
        if i in mset or not universe: universe = ufn(i)
        rr = [Cn[i, j] / Cn[i - 1, j] - 1 for j in universe if not np.isnan(Cn[i, j]) and not np.isnan(Cn[i - 1, j])]
        eq[i] = eq[i - 1] * (1 + (np.mean(rr) if rr else 0))
    return pd.Series(eq, index=cal)

def fmt(name, m, ts=None):
    s = f"{name:<44} 누적 {m['total']*100:+8.1f}%  CAGR {m['cagr']*100:+6.2f}%  MDD {m['mdd']*100:6.1f}%  샤프 {m['sharpe']:5.2f}"
    if ts: s += f" | 매매 {ts['n']:4d} 승률 {ts['win']*100:4.1f}% PF {ts['pf']:4.2f} 평균 {ts['avg']*100:+5.2f}% t {ts['tstat']:4.1f}"
    return s


def annual(rows):
    ann = {}
    for y in range(2013, 2025):
        ix = [k for k in range(START, len(cal)) if cal[k].year == y]
        k0 = ix[0] - 1
        ann[y] = {k: (v.iloc[ix[-1]] / v.iloc[k0] - 1) * 100 for k, v in rows.items()}
    return pd.DataFrame(ann).T.round(1)

if __name__ == '__main__':
    cases = [
        ("L1 회계 수정(종가판정·종가체결, 원본 유니버스)", dict(ufn=U_TOP5_ORIG, mode='close', entry='close')),
        ("L2 + 시총 오염 제거", dict(ufn=U_TOP5_CLEAN, mode='close', entry='close')),
        ("L3 + 시가 진입·장중 손절/익절(갭 반영)", dict(ufn=U_TOP5_CLEAN, mode='intraday', entry='open')),
        ("L4 + 연도별 거래세·실제 금리", dict(ufn=U_TOP5_CLEAN, mode='intraday', entry='open', real=True)),
        ("대조: L4 규칙, 진입 신호 없음(빈 슬롯 즉시)", dict(ufn=U_TOP5_CLEAN, mode='intraday', entry='open', real=True, signal=False)),
        ("L4 테마 대장주 풀(사후 선정 명단)", dict(ufn=U_THEME, mode='intraday', entry='open', real=True)),
        ("L4 코스피 Top10(정상 시총)", dict(ufn=U_TOP10_CLEAN, mode='intraday', entry='open', real=True)),
    ]
    rows = {}
    for name, kw in cases:
        eq, t = run(**kw); rows[name[:20]] = eq
        print(fmt(name, metrics(eq, kw.get('real', False)), tstats(t)), flush=True)
        if kw['ufn'] is U_THEME:
            print(t.groupby('name').ret.agg(['count', 'mean', 'sum']).sort_values('sum').round(3).to_string())
    bh = bh_ew(U_TOP5_CLEAN); rows['Top5 보유(정상)'] = bh
    print(fmt("벤치: Top5(정상 시총) 동일가중 보유", metrics(bh, True)))
    print(fmt("벤치: Top5(오염 시총) 동일가중 보유", metrics(bh_ew(U_TOP5_ORIG), True)))
    k2 = u180.reindex(cal) / u180.reindex(cal).iloc[START]; rows['K200'] = k2
    print(fmt("벤치: KOSPI200 가격지수", metrics(k2, True)))
    print(annual(rows).to_string())
    if '--grid' in sys.argv:
        import itertools
        g = []
        for tp1, tp2, sl, hold in itertools.product([.05, .07, .10], [.10, .15, .20], [-.04, -.06, -.08, -.10], [30, 40, 60]):
            if tp2 < tp1: continue
            eq, t = run(ufn=U_TOP5_CLEAN, mode='intraday', entry='open', real=True, tp1=tp1, tp2=tp2, sl=sl, hold=hold)
            g.append(dict(tp1=tp1, tp2=tp2, sl=sl, hold=hold, **metrics(eq, True), n=len(t)))
        g = pd.DataFrame(g).sort_values('cagr', ascending=False)
        print(len(g), "조합 | CAGR>0:", (g.cagr > 0).sum(), "| K200 초과:", (g.cagr > 0.0151).sum())
        print(g.head(5).round(3).to_string())
