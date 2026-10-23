"""무작위 N종목 포트폴리오 vs SPY·QQQ — 당시 S&P500 명단 기준 (2026-10-21)

질문: 종목 고르는 실력이 0일 때, 미국 개별종목 N개로 직접 짠 포트폴리오는 지수 ETF 대비 어떤 분포를 갖나?
      (집중할수록 중앙값이 지수 아래로 내려가는가 — Bessembinder 2018 의 왜도 효과를 우리 데이터로 확인)

명단: data_sp500_membership/sp500_components_updated.csv (github fja05680/sp500, 1996~2026-08-18, 날짜별 당시 티커)
가격: DataVault raw/yahoo (현재 상장 종목만) — us_data.load(분할 `<` 정정판) + 배당 재투자(총수익)
상폐·피인수 종목은 Yahoo 에 없다 → 두 처리로 범위를 잡는다
  E 제외   : 살아남은 종목에서만 뽑음 → 개별종목에 유리한 상한(생존 편향)
  Q 지수대체: 명단 전체에서 뽑고 데이터 없는 종목은 SPY 수익으로 채움 → 분산이 줄어 지수 쪽으로 끌림
티커 변경(BK→BNY, ANTM→ELV 등)은 RENAME 으로 이어 붙이고, 티커 재사용(T·CB·IR·FB)은 날짜 조건으로 막는다.

시작: 2000~2021 매년 첫 거래일, 종료: 2023-12-29 / 2026-08-31 (반도체 급등 제외·포함), 보유 5년 이상만 집계.
보유: 시작일 동일가중 매수 후 보유(B&H). 한국 세금은 마지막 날 22% 정산이라 B&H 끼리는 승패 순서가 안 바뀐다.
추가: 매년 초 동일가중 재조정(R) — 편도 0.1%, 원화 실현이익 연 250만 공제 후 22% 를 다음 해 초 납부.
실행: .venv/bin/python -I docs/research/20261021_random_portfolio_vs_qqq/random_portfolios.py
"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '20261021_slot_swing_recompute'))
from us_data import load, ROOT

MEMB = os.path.join(HERE, 'data_sp500_membership', 'sp500_components_updated.csv')
ENDS = [pd.Timestamp('2023-12-29'), pd.Timestamp('2026-08-31')]
STARTS = list(range(2000, 2022))
NS = [1, 5, 10, 20, 30, 50, 100]
DRAWS = 10000
R_NS, R_DRAWS = [10, 20, 30], 2000
COST, TAX, DEDUCT = 0.001, 0.22, 0.025          # 원화 1억 = 1.0, 공제 250만 = 0.025
rng = np.random.default_rng(20261021)

# 티커 변경: (명단 티커, 이 날짜 '전'에만 적용 또는 None) → Yahoo 현재 티커
RENAME = {'FB': 'META', 'ANTM': 'ELV', 'ABC': 'COR', 'BLL': 'BALL', 'BK': 'BNY', 'MMC': 'MRSH', 'WLTW': 'WTW',
          'TMK': 'GL', 'JEC': 'J', 'CTL': 'LUMN', 'MYL': 'VTRS', 'NLOK': 'GEN', 'SYMC': 'GEN', 'PKI': 'RVTY',
          'RE': 'EG', 'PEAK': 'DOC', 'HCP': 'DOC', 'FLT': 'CPAY', 'BHGE': 'BKR', 'HRS': 'LHX', 'UTX': 'RTX',
          'ARNC': 'HWM', 'DWDP': 'DD', 'GPS': 'GAP', 'HFC': 'DINO', 'FBHS': 'FBIN', 'HCN': 'WELL', 'PX': 'LIN',
          'ACE': 'CB', 'PCLN': 'BKNG', 'KFT': 'MDLZ', 'BBT': 'TFC', 'SBC': 'T'}
REUSED = {'T': pd.Timestamp('2005-11-21'),       # 그 전 'T' = 옛 AT&T Corp(피인수), Yahoo T = SBC 이력
          'CB': pd.Timestamp('2016-01-15'),      # 그 전 'CB' = 옛 Chubb Corp(피인수), Yahoo CB = ACE 이력
          'IR': pd.Timestamp('2020-03-02')}      # 그 전 'IR' = 옛 Ingersoll-Rand(→TT), Yahoo IR = Gardner Denver
REUSED_TO = {'IR': 'TT'}                         # 재사용 전 기간을 이어 붙일 곳(없으면 상폐 취급)


def yahoo_ticker(t, d):
    t = t.replace('.', '-')
    if t in REUSED and d < REUSED[t]:
        return REUSED_TO.get(t)
    return RENAME.get(t, t)


def tr_index(t, cal):
    """총수익 지수(배당 재투자) — gross, net(배당 15% 원천징수)."""
    df = load(t)
    if df is None:
        return None, None
    df = df.reindex(cal)
    c = df['close'].ffill()
    dv = pd.Series(0.0, index=cal)
    cp = f"{ROOT}/raw/yahoo/corporate_actions/{t}.csv"
    if os.path.exists(cp):
        ca = pd.read_csv(cp, parse_dates=['date'])
        raw = load(t)
        mult = pd.Series(1.0, index=raw.index)
        sp = ca[ca.type == 'split']
        for d, n, m in zip(sp.date, sp.numerator, sp.denominator):
            mult[raw.index < d] *= n / m
        g = ca[ca.type == 'dividend'].groupby('date').amount.sum()
        g = g / mult.reindex(g.index).fillna(1.0)
        dv = g.reindex(cal).fillna(0.0)
    prev = c.shift(1)
    rg = ((c + dv) / prev).where(prev.notna(), 1.0).fillna(1.0)
    rn = ((c + 0.85 * dv) / prev).where(prev.notna(), 1.0).fillna(1.0)
    first = c.first_valid_index()
    gi, ni = rg.cumprod(), rn.cumprod()
    if first is not None:
        gi[gi.index < first] = np.nan; ni[ni.index < first] = np.nan
    return gi, ni


# ------------------------------------------------------------------ 데이터
spy_raw = load('SPY')
cal = spy_raw.loc['1999-12-01':ENDS[-1]].index
memb = pd.read_csv(MEMB, parse_dates=['date'])
fx = pd.read_csv(f"{ROOT}/staging/fx_usdkrw/usdkrw_daily.csv", parse_dates=['date']).set_index('date')['usdkrw']
FX = fx.reindex(fx.index.union(cal)).ffill().reindex(cal)

ENDS = [cal[cal <= e][-1] for e in ENDS]
start_days = {y: cal[cal >= pd.Timestamp(f'{y}-01-01')][0] for y in STARTS}
members = {}       # y → [(명단 티커, yahoo 티커 or None)]
need = set()
for y, d in start_days.items():
    row = memb[memb.date <= d].iloc[-1]
    lst = []
    for t in row.tickers.split(','):
        yt = yahoo_ticker(t, d)
        lst.append((t, yt)); need.add(yt) if yt else None
    members[y] = lst
need |= {'SPY', 'QQQ'}
G, N_, DVOL = {}, {}, {}
for t in sorted(need):
    g, n = tr_index(t, cal)
    if g is not None:
        G[t], N_[t] = g, n
        DVOL[t] = load(t)['dvol_60d'].reindex(cal)
print(f"명단 시점 {len(STARTS)}개, 필요 티커 {len(need)}, Yahoo 적재 {len(G)}")

# 품질 점검: 하루 총수익 +150% 이상 / −75% 이하 봉
bad = []
for t, g in G.items():
    r = g.pct_change()
    for d, v in r[(r > 1.5) | (r < -0.75)].items():
        bad.append((t, d.date(), round(v, 3)))
print(f"극단 일수익 봉 {len(bad)}건:", bad[:30])


REUSE_LOG = {}


def avail(y):
    """시작일에 가격 있는 종목 / 없는 종목.
    티커 재사용 차단: 시작일 60일 달러 거래대금이 그날 명단 중앙값의 10% 미만이면 다른(소형) 회사가 같은 티커를 쓰는 것으로 보고 제외
    (실측: EP=옛 El Paso ↔ 현재 거래대금 0 인 다른 회사, CCU=옛 Clear Channel ↔ 칠레 맥주 ADR, CSR=옛 Ceridian ↔ Centerspace)."""
    d = start_days[y]
    cand = [(t, yt) for t, yt in members[y] if yt in G and not np.isnan(G[yt].loc[d])]
    dv = {yt: DVOL[yt].loc[d] for _, yt in cand}
    floor = 0.10 * np.nanmedian(list(dv.values()))
    ok, miss = [], []
    for t, yt in members[y]:
        if yt in dv and dv[yt] >= floor:
            ok.append(yt)
        else:
            if yt in dv:
                REUSE_LOG.setdefault(y, set()).add(f"{t}({dv[yt] / 1e6:.1f}M<{floor / 1e6:.1f}M)")
            miss.append(t)
    return ok, miss


def cagr(mult, yrs):
    return mult ** (1 / yrs) - 1


rows, rrows, miss_log = [], [], []
for end in ENDS:
    for y in STARTS:
        d0 = start_days[y]
        yrs = (end - d0).days / 365.25
        if yrs < 5:
            continue
        ok, miss = avail(y)
        if end == ENDS[-1]:
            miss_log.append((y, len(ok) + len(miss), len(miss), ' '.join(sorted(miss))))
        mult = np.array([G[t].loc[end] / G[t].loc[d0] for t in ok])
        spy = G['SPY'].loc[end] / G['SPY'].loc[d0]
        qqq = G['QQQ'].loc[end] / G['QQQ'].loc[d0]
        full = np.concatenate([mult, np.full(len(miss), spy)])          # Q: 없는 종목 = SPY
        for mode, pool in (('E', mult), ('Q', full)):
            ew = pool.mean()
            for n in NS:
                idx = np.argsort(rng.random((DRAWS, len(pool))), axis=1)[:, :n]
                pm = pool[idx].mean(axis=1)
                rows.append(dict(end=end.date(), start=y, mode=mode, N=n, yrs=round(yrs, 1), pool=len(pool),
                                 missing=len(miss), p_beat_spy=(pm > spy).mean(), p_beat_qqq=(pm > qqq).mean(),
                                 med_cagr=cagr(np.median(pm), yrs), mean_cagr=cagr(pm.mean(), yrs),
                                 p10_cagr=cagr(np.percentile(pm, 10), yrs), p90_cagr=cagr(np.percentile(pm, 90), yrs),
                                 spy_cagr=cagr(spy, yrs), qqq_cagr=cagr(qqq, yrs), ew_cagr=cagr(ew, yrs),
                                 single_beat_spy=(pool > spy).mean()))

        # ---- R: 매년 초 동일가중 재조정 + 원화 양도세 (E 만), 비교는 같은 N종목 B&H 세후·SPY B&H 세후
        bounds = [cal[cal >= pd.Timestamp(f'{yy}-01-01')][0] for yy in range(y, end.year + 1)] + [end]
        NI = np.array([[N_[t].loc[b] for t in ok] for b in bounds])              # (경계, 종목)
        fxb = np.array([FX.loc[b] for b in bounds])
        spy_n = N_['SPY'].loc[end] / N_['SPY'].loc[d0]
        def after_tax_bh(m_usd):
            v = m_usd * (1 - COST) * fxb[-1] / fxb[0]
            return v - np.maximum(0, v - 1 - DEDUCT) * TAX                         # 원금 1억 대비 최종(억)
        spy_at = after_tax_bh(spy_n)
        for n in R_NS:
            sel = np.argsort(rng.random((R_DRAWS, len(ok))), axis=1)[:, :n]
            v = np.full((R_DRAWS, n), (1 - COST) / n / fxb[0])                    # 달러 단위(1억 = 1/fx0 달러)
            basis = np.full((R_DRAWS, n), 1.0 / n)                                # 원화(억)
            realized_prev = np.zeros(R_DRAWS)
            for k in range(1, len(bounds)):
                v = v * NI[k][sel] / NI[k - 1][sel]
                if k == len(bounds) - 1:
                    break
                fxk = fxb[k]
                # 전년도 실현분 세금 납부(비례 매도, 그 매도 이익도 올해 실현)
                tax_krw = np.maximum(0, realized_prev - DEDUCT) * TAX
                V = v.sum(1)
                f = np.clip(tax_krw / fxk / np.maximum(V, 1e-12), 0, 1)[:, None]
                realized = (f * (v * fxk - basis)).sum(1)
                basis = basis * (1 - f); v = v * (1 - f)
                # 동일가중 재조정
                V = v.sum(1, keepdims=True); tgt = V / n
                sellf = np.where(v > tgt, (v - tgt) / np.maximum(v, 1e-12), 0.0)
                realized += (sellf * (v * fxk - basis)).sum(1)
                basis = basis * (1 - sellf) + np.where(v < tgt, (tgt - v) * fxk, 0.0)
                traded = np.abs(v - tgt).sum(1, keepdims=True)
                v = np.repeat((V - traded * COST) / n, n, axis=1)
                realized_prev = realized
            final_krw = v.sum(1) * fxb[-1]
            gain = realized_prev + (final_krw - basis.sum(1))
            r_at = final_krw - np.maximum(0, gain - DEDUCT) * TAX
            bh_m = (NI[-1][sel] / NI[0][sel]).mean(1)
            bh_at = after_tax_bh(bh_m)
            rrows.append(dict(end=end.date(), start=y, N=n, yrs=round(yrs, 1),
                              med_rebal_at=np.median(r_at), med_bh_at=np.median(bh_at), spy_at=spy_at,
                              p_rebal_beat_spy=(r_at > spy_at).mean(), p_bh_beat_spy=(bh_at > spy_at).mean(),
                              p_rebal_beat_bh=(r_at > bh_at).mean(),
                              med_drag_cagr=np.median(cagr(np.maximum(r_at, 1e-9), yrs) - cagr(np.maximum(bh_at, 1e-9), yrs))))

print('티커 재사용 의심 제외:', {y: sorted(v) for y, v in REUSE_LOG.items()})
df = pd.DataFrame(rows); rdf = pd.DataFrame(rrows)
df.to_csv(os.path.join(HERE, 'random_bh.csv'), index=False)
rdf.to_csv(os.path.join(HERE, 'random_rebal_tax.csv'), index=False)
pd.DataFrame(miss_log, columns=['start', 'members', 'missing', 'tickers']).to_csv(os.path.join(HERE, 'missing_by_start.csv'), index=False)

pd.set_option('display.width', 250); pd.set_option('display.max_columns', 30)
print("\n=== 시작연도 평균(각 시작 동등) — 보유 5년 이상 ===")
agg = df.groupby(['end', 'mode', 'N']).agg(starts=('start', 'size'), p_beat_spy=('p_beat_spy', 'mean'),
                                           p_beat_qqq=('p_beat_qqq', 'mean'),
                                           med_minus_spy=('med_cagr', lambda s: (s - df.loc[s.index, 'spy_cagr']).mean()),
                                           mean_minus_spy=('mean_cagr', lambda s: (s - df.loc[s.index, 'spy_cagr']).mean()),
                                           p10_minus_spy=('p10_cagr', lambda s: (s - df.loc[s.index, 'spy_cagr']).mean()),
                                           p90_minus_spy=('p90_cagr', lambda s: (s - df.loc[s.index, 'spy_cagr']).mean()),
                                           med_minus_mean=('med_cagr', lambda s: (s - df.loc[s.index, 'mean_cagr']).mean()))
print(agg.round(3).to_string())
print("\n=== 대표 시작연도 (E, 끝 2026-08) ===")
print(df[(df['mode'] == 'E') & (df.end == ENDS[-1].date()) & df.start.isin([2000, 2005, 2010, 2015, 2019])]
      [['start', 'N', 'yrs', 'pool', 'missing', 'p_beat_spy', 'p_beat_qqq', 'med_cagr', 'mean_cagr', 'p10_cagr', 'p90_cagr', 'spy_cagr', 'qqq_cagr', 'ew_cagr', 'single_beat_spy']].round(3).to_string(index=False))
print("\n=== 재조정+세금(R) vs 보유(B&H) 세후 — 시작연도 평균 ===")
print(rdf.groupby(['end', 'N'])[['p_rebal_beat_spy', 'p_bh_beat_spy', 'p_rebal_beat_bh', 'med_drag_cagr']].mean().round(3).to_string())
