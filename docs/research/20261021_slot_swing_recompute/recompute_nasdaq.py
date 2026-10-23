"""나스닥 '시총 가속도 TOP10'·'슈퍼루키-3(+AZO)' 정정 재계산 (2026-10-21)

원본: 20261018_qqq_replication/acceleration_top10_slot_test.py (EXP-017, +319,336%)
      20261018_qqq_replication/super_rookie_azo_slot_test.py (EXP-020, +190,830% / 현금판)
원본 결함:
  공통  ① 분할 보정 `<= 분할일` → 분할일마다 −90%/+900% 가짜 봉
        ② 종목 명단 NAS_UNIVERSE = 2024~26 기준 나스닥100(+일부 퇴출주)을 1999년부터 그대로 씀(사후 선정·생존 편향)
        ③ 진입일(종가 매수) 당일 수익률을 계좌에 넣음
  가속도 ④ 포트폴리오 수익 = 매일 슬롯 수익 평균(매일 동일가중 재조정) — 월 1회 리밸런싱 계좌가 아님
         '시총'은 실제로는 60일 달러 거래대금
  슈퍼루키 ⑤ 손절한 날의 하락분을 계좌에 넣지 않음(당일 종가를 보고 전일 종가에 판 셈)
          ⑥ 2016년 이전 '신규 편입'은 공식 이력이 아니라 사후 명단 안의 거래대금 순위 진입
          ⑦ 빈 슬롯 대체 종목 AZO 는 결과를 알고 고른 종목. 공식 편입 중 명단 밖 4종목(AEP·ROP·CCEP·LIN) 제외
정정: us_data.load(분할 `< 분할일`), 주식 수 기반 계좌, 편도 0.1%, 미 국채 3개월 금리 현금이자.
가속도: 월초 시가(또는 종가) 체결 → 월중 보유. 대조군 = 같은 명단 무작위 10종목(300회)·같은 명단 동일가중.
슈퍼루키: 모든 매매는 종가, 그날 수익은 매매 전 보유분에 귀속. 대체 종목 = 현금/AZO/QQQ, 2016~2024 공식 편입만 쓴 판.
실행: .venv/bin/python docs/research/20261021_slot_swing_recompute/recompute_nasdaq.py
"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '20261018_qqq_replication'))
from us_data import load
from max_tenure_hybrid_backtest import NAS_UNIVERSE, OFFICIAL_ADDITIONS, OFFICIAL_REMOVALS


# 위키피디아 'Historical components of the Nasdaq-100' 2016~2024 편입(효력일)·퇴출 — 2026-10-21 조회.
# 원본 OFFICIAL_ADDITIONS 는 이 이력과 다르다(예: APP 실제 2024-11-18 ↔ 원본 09-23, DOCU 실제 2020-06-22 ↔ 원본 04-20,
# FSLR 은 2012 퇴출 후 재편입 없음 ↔ 원본 2022-06-20 편입). 확인: nasdaq.com 보도자료·뉴스 검색.
_W = """2016-02-01 AVGO|2016-02-16 CSX|2016-03-16 NTES|2016-04-18 BATRA|2016-04-18 BATRK|2016-06-20 XRAY|2016-07-18 MCHP|2016-10-19 SHPG|
2016-12-19 CTAS|2016-12-19 HAS|2016-12-19 HOLX|2016-12-19 KLAC|2017-02-07 JBHT|2017-03-20 IDXX|2017-04-24 WYNN|2017-06-19 MELI|2017-10-23 ALGN|
2017-12-18 ASML|2017-12-18 CDNS|2017-12-18 DISCK|2017-12-18 SNPS|2017-12-18 TTWO|2017-12-18 WDAY|2018-07-23 PEP|2018-11-05 NXPI|2018-11-19 XEL|
2018-12-24 AMD|2018-12-24 LULU|2018-12-24 NTAP|2018-12-24 UAL|2018-12-24 VRSN|2018-12-24 WTW|2019-11-21 EXC|2019-12-23 ANSS|2019-12-23 CDW|
2019-12-23 CPRT|2019-12-23 CSGP|2019-12-23 SGEN|2019-12-23 SPLK|2020-04-20 DXCM|2020-04-30 ZM|2020-06-22 DOCU|2020-07-20 MRNA|2020-08-24 PDD|
2020-10-19 KDP|2020-12-21 AEP|2020-12-21 LBTYK|2020-12-21 MRVL|2020-12-21 MTCH|2020-12-21 OKTA|2020-12-21 PTON|2020-12-21 TEAM|2021-07-21 HON|
2021-08-26 CRWD|2021-12-20 ABNB|2021-12-20 DDOG|2021-12-20 FOX|2021-12-20 FTNT|2021-12-20 LCID|2021-12-20 PANW|2021-12-20 ZS|2022-01-24 ODFL|
2022-02-02 CEG|2022-02-22 AZN|2022-11-21 ENPH|2022-12-19 BKR|2022-12-19 CSGP|2022-12-19 FANG|2022-12-19 GFS|2022-12-19 NTES|2022-12-19 RIVN|
2022-12-19 WBD|2023-06-07 GEHC|2023-06-20 ON|2023-07-17 TTD|2023-12-14 TTWO|2023-12-18 CCEP|2023-12-18 CDW|2023-12-18 DASH|2023-12-18 MDB|
2023-12-18 ROP|2023-12-18 SPLK|2024-03-18 LIN|2024-06-24 ARM|2024-07-22 SMCI|2024-11-18 APP|2024-12-23 AXON|2024-12-23 MSTR|2024-12-23 PLTR"""
_R = """2016-02-16 KLAC|2016-03-16 SNDK|2016-06-20 LMCA|2016-07-18 ENDP|2016-10-19 LLTC|2016-12-19 BBBY|2016-12-19 NTAP|2016-12-19 SRCL|2016-12-19 WFM|
2017-02-07 NXPI|2017-03-20 SBAC|2017-04-24 TRIP|2017-06-19 YHOO|2017-10-23 MAT|2017-12-18 AKAM|2017-12-18 DISCA|2017-12-18 NCLH|2017-12-18 TSCO|
2017-12-18 VIAB|2018-07-23 DISH|2018-11-05 CA|2018-11-19 XRAY|2018-12-24 ESRX|2018-12-24 HOLX|2018-12-24 QRTEA|2018-12-24 SHPG|2018-12-24 STX|
2018-12-24 VOD|2019-11-21 CELG|2019-12-23 HAS|2019-12-23 HSIC|2019-12-23 JBHT|2019-12-23 MYL|2019-12-23 NLOK|2019-12-23 WYNN|2020-04-20 AAL|
2020-04-30 WTW|2020-06-22 UAL|2020-07-20 CSGP|2020-08-24 NTAP|2020-10-19 WDC|2020-12-21 BMRN|2020-12-21 CTXS|2020-12-21 EXPE|2020-12-21 LBTYA|
2020-12-21 TTWO|2020-12-21 ULTA|2021-07-21 ALXN|2021-08-26 MXIM|2021-12-20 CDW|2021-12-20 INCY|2021-12-20 FOXA|2021-12-20 CHKP|2021-12-20 CERN|
2021-12-20 TCOM|2022-01-24 PTON|2022-02-22 XLNX|2022-11-21 OKTA|2022-12-19 MTCH|2022-12-19 VRSN|2022-12-19 DOCU|2022-12-19 BIDU|2022-12-19 SWKS|
2022-12-19 SPLK|2023-06-07 FI|2023-06-20 RIVN|2023-07-17 ATVI|2023-12-14 SGEN|2023-12-18 EBAY|2023-12-18 ALGN|2023-12-18 ENPH|2023-12-18 JD|
2023-12-18 LCID|2023-12-18 ZM|2024-03-18 SPLK|2024-06-24 SIRI|2024-07-22 WBA|2024-11-18 DLTR|2024-12-23 SMCI|2024-12-23 MRNA|2024-12-23 ILMN"""
_p = lambda txt: [(pd.Timestamp(x.split()[0]), x.split()[1]) for x in txt.replace('\n', '').split('|')]
WIKI_ADD, WIKI_REM = _p(_W), _p(_R)
SHARE_CLASS = {'AVGO', 'BATRA', 'BATRK', 'DISCK', 'LBTYK', 'FOX'}   # 개명·종류주·트래킹주(신규 기업 아님)

TB = {1999: 4.64, 2000: 5.82, 2001: 3.39, 2002: 1.60, 2003: 1.01, 2004: 1.37, 2005: 3.15, 2006: 4.73, 2007: 4.36,
      2008: 1.37, 2009: 0.15, 2010: 0.14, 2011: 0.05, 2012: 0.09, 2013: 0.06, 2014: 0.03, 2015: 0.05, 2016: 0.32,
      2017: 0.93, 2018: 1.94, 2019: 2.06, 2020: 0.37, 2021: 0.05, 2022: 2.02, 2023: 5.07, 2024: 4.97}
COST = 0.001

qqq = load('QQQ')
cal = qqq.loc['1999-03-10':'2024-12-31'].index
RF = np.array([TB[d.year] / 100 / 252 for d in cal])
EXTRA = ['AEP', 'ROP', 'CCEP', 'LIN', 'AZO', 'QQQ'] + [t for _, t in WIKI_ADD]
tick = sorted({t for t in set(NAS_UNIVERSE) | set(EXTRA) if load(t) is not None})
D = {t: load(t) for t in tick}
ci = {t: k for k, t in enumerate(tick)}
M = lambda k: np.column_stack([D[t][k].reindex(cal).to_numpy(dtype=float) for t in tick])
C, O = M('close'), M('open')
DV = pd.DataFrame({t: D[t]['dvol_60d'].reindex(cal).ffill() for t in tick}, index=cal)
univ = np.array([t in NAS_UNIVERSE for t in tick])      # 원본 명단(가속도·이벤트 후보)

def last_px(i, j):
    k = i
    while k >= 0 and np.isnan(C[k, j]): k -= 1
    return C[k, j] if k >= 0 else np.nan

def metrics(eq, a=None, b=None):
    e = eq.loc[a:b] if a or b else eq
    e = e / e.iloc[0]
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    r = e.pct_change().dropna()
    rf = RF[cal.get_indexer(r.index)]
    return dict(total=e.iloc[-1] - 1, cagr=e.iloc[-1] ** (1 / yrs) - 1, mdd=(e / e.cummax() - 1).min(),
                sharpe=(r.values - rf).mean() / r.std() * np.sqrt(252))

def fmt(name, eq):
    a, b = metrics(eq), metrics(eq, '2015-01-01')
    return (f"{name:<44} 전체 {a['total']*100:+11.1f}% CAGR {a['cagr']*100:+6.2f}% MDD {a['mdd']*100:6.1f}% 샤프 {a['sharpe']:5.2f}"
            f" | 2015~ {b['total']*100:+9.1f}% CAGR {b['cagr']*100:+6.2f}% MDD {b['mdd']*100:6.1f}%")

# ---------------------------------------------------------------- 가속도 TOP10 (월 1회)
months = [0] + [i for i in range(1, len(cal)) if cal[i].month != cal[i - 1].month]
mset = set(months)
dv = DV.to_numpy()
acc = ((DV / DV.shift(21) - 1) - (DV.shift(21) / DV.shift(42) - 1)).to_numpy()

def pool(i):
    """원본과 같은 후보: 전일 거래대금 있는 명단 종목 중 중앙값 이상."""
    row = np.where(univ, dv[i - 1], np.nan) if i > 0 else np.where(univ, dv[0], np.nan)
    ok = ~np.isnan(row)
    if ok.sum() > 10:
        ok &= row >= np.nanmedian(row)
    return [j for j in np.where(ok)[0] if not np.isnan(O[i, j]) and not np.isnan(C[i, j])]

def sel_acc(i):
    p = [j for j in pool(i) if not np.isnan(acc[i - 1 if i > 0 else 0, j])]
    return sorted(p, key=lambda j: -acc[i - 1 if i > 0 else 0, j])[:10]

def run_monthly(select, exe='open'):
    sh = {}; cash = 1.0; eq = np.empty(len(cal))
    for i in range(len(cal)):
        if i in mset:
            tgt = select(i)
            P = O[i] if exe == 'open' else C[i]
            px = {j: (P[j] if not np.isnan(P[j]) else last_px(i - 1, j)) for j in set(sh) | set(tgt)}
            val = cash + sum(s * px[j] for j, s in sh.items())
            w = val / len(tgt) if tgt else 0.0
            new = {j: w / px[j] for j in tgt}
            traded = sum(abs(new.get(j, 0) - sh.get(j, 0)) * px[j] for j in set(sh) | set(new))
            cash = val - sum(new[j] * px[j] for j in new) - traded * COST
            sh = new
        cash *= 1 + RF[i]
        eq[i] = cash + sum(s * last_px(i, j) for j, s in sh.items())
    return pd.Series(eq, index=cal)

# ---------------------------------------------------------------- 슈퍼루키-3
qtr = [0] + [i for i in range(1, len(cal)) if (cal[i].month - 1) // 3 != (cal[i - 1].month - 1) // 3]

def build_events(mode):
    """mode='orig': 원본 그대로(2016 전 거래대금 순위 진입 + 공식 편입 중 명단 안), 'official': 2016~ 공식 편입 전부."""
    ev = {}
    names = [t for t in tick if t in NAS_UNIVERSE]
    init = list(DV.iloc[0][names].dropna().sort_values(ascending=False).head(50).index)
    if mode == 'orig':
        known = set(init)
        for i in qtr:
            if cal[i] >= pd.Timestamp('2016-01-01'): break
            top = set(DV.iloc[i][names].dropna().sort_values(ascending=False).head(50).index)
            new = sorted(top - known)
            if new: ev[i] = list(new); known |= set(new)
    adds = OFFICIAL_ADDITIONS if mode in ('orig', 'official') else [(d, t) for d, t in WIKI_ADD if mode == 'wiki' or t not in SHARE_CLASS]
    for d, s in adds:
        if s not in ci or (mode == 'orig' and s not in NAS_UNIVERSE): continue
        k = int(np.searchsorted(cal, d))
        if k < len(cal): ev.setdefault(k, []); s in ev[k] or ev[k].append(s)
    return ev, init

def run_rookie(fill='cash', mode='orig', start='1999-03-10', n=3, stop=-0.15, tenure=912, log=None):
    ev, init = build_events(mode)
    rem = {t: d for d, t in OFFICIAL_REMOVALS}
    remw = {}
    for d, t in WIKI_REM: remw.setdefault(t, []).append(d)
    def removed(s, dt):
        if mode.startswith('wiki'):
            return any(hold[s]['d'] < r <= dt for r in remw.get(s, []))
        return not dt < rem.get(s, pd.Timestamp('2099-12-31'))
    i0 = int(np.searchsorted(cal, pd.Timestamp(start)))
    fj = ci.get(fill)
    hold = {}          # sym -> dict(sh, e, d)
    fsh = 0.0; cash = 1.0
    eq = np.full(len(cal), np.nan)
    stats = dict(stop=0, prune=0, enter=0)

    def val(i):
        return cash + fsh * (last_px(i, fj) if fj is not None else 0) + sum(h['sh'] * last_px(i, ci[s]) for s, h in hold.items())

    def rebalance(i, keep):
        nonlocal cash, fsh, hold
        v = val(i); traded = 0.0
        for s in list(hold):
            if s not in keep:
                traded += hold[s]['sh'] * last_px(i, ci[s])
                if log is not None: log.append(dict(sym=s, entry=hold[s]['d'], exit=cal[i], ret=last_px(i, ci[s]) / hold[s]['e'] - 1, why='교체/만기/퇴출'))
                del hold[s]
        for s in keep:
            p = last_px(i, ci[s]); tgt = v / n / p
            old = hold[s]['sh'] if s in hold else 0.0
            traded += abs(tgt - old) * p
            if s in hold: hold[s]['sh'] = tgt
            else: hold[s] = dict(sh=tgt, e=p, d=cal[i]); stats['enter'] += 1
        empty = v * (n - len(keep)) / n
        if fj is not None:
            p = last_px(i, fj); traded += abs(empty / p - fsh) * p; fsh = empty / p; c = 0.0
        else:
            c = empty
        cash = v - sum(h['sh'] * last_px(i, ci[s]) for s, h in hold.items()) - fsh * (last_px(i, fj) if fj is not None else 0) - traded * COST

    for i in range(i0, len(cal)):
        if i > i0: cash *= 1 + RF[i]
        if i == i0:
            first = [s for s in init if not np.isnan(C[i, ci[s]])][:n] if mode == 'orig' else []
            rebalance(i, first)
        else:
            # 손절: 오늘 종가 기준, 오늘 손익은 이미 반영된 상태에서 종가에 매도
            stopped = [s for s, h in hold.items() if last_px(i, ci[s]) / h['e'] - 1 <= stop]
            if stopped:
                stats['stop'] += len(stopped)
                keep = [s for s in hold if s not in stopped]
                # 원본처럼 나머지는 건드리지 않고 손절분만 대체 종목/현금으로
                v_st = sum(hold[s]['sh'] * last_px(i, ci[s]) for s in stopped)
                for s in stopped:
                    if log is not None: log.append(dict(sym=s, entry=hold[s]['d'], exit=cal[i], ret=last_px(i, ci[s]) / hold[s]['e'] - 1, why='손절'))
                    del hold[s]
                proceeds = v_st * (1 - COST)
                if fj is not None: fsh += proceeds * (1 - COST) / last_px(i, fj)
                else: cash += proceeds
            if i in ev:
                dt = cal[i]
                surv = [s for s in hold if not removed(s, dt) and (dt - hold[s]['d']).days < tenure]
                new = [s for s in ev[i] if s not in hold and not np.isnan(C[i, ci[s]])]
                free = n - len(surv)
                if len(new) > free and surv:
                    k = min(len(surv), len(new) - free)
                    worst = sorted(surv, key=lambda s: last_px(i, ci[s]) / hold[s]['e'])[:k]
                    surv = [s for s in surv if s not in worst]; stats['prune'] += k
                keep = surv + new[:n - len(surv)]
                rebalance(i, keep)
        eq[i] = val(i)
    if log is not None:
        for s, h in hold.items(): log.append(dict(sym=s, entry=h['d'], exit=cal[-1], ret=last_px(len(cal) - 1, ci[s]) / h['e'] - 1, why='보유 중'))
    out = pd.Series(eq, index=cal).loc[cal[i0]:]
    return out, stats

if __name__ == '__main__':
    print(f"종목 {univ.sum()}개(원본 명단 중 데이터 있는 것), 기간 {cal[0].date()} ~ {cal[-1].date()}")
    print("=== 시총(거래대금) 가속도 TOP10, 월 1회 ===")
    eqo = run_monthly(sel_acc, 'open'); print(fmt("가속도 TOP10 [월초 시가 체결]", eqo))
    eqc = run_monthly(sel_acc, 'close'); print(fmt("가속도 TOP10 [월초 종가 체결]", eqc))
    eqa = run_monthly(lambda i: pool(i), 'open'); print(fmt("대조: 같은 후보 전부 동일가중", eqa))
    rng = np.random.default_rng(7); sims = []
    for _ in range(300):
        picks = {i: list(rng.permutation(pool(i))[:10]) for i in months}
        sims.append(run_monthly(lambda i: picks[i], 'open'))
    tot = np.array([s.iloc[-1] / s.iloc[0] - 1 for s in sims]); tot10 = np.array([s.iloc[-1] / s.loc['2015-01-01':].iloc[0] - 1 for s in sims])
    r = eqo.iloc[-1] / eqo.iloc[0] - 1; r10 = eqo.iloc[-1] / eqo.loc['2015-01-01':].iloc[0] - 1
    print(f"대조: 같은 후보 무작위 10종목 300회 — 전체 중앙값 {np.median(tot)*100:+.0f}% (5~95%: {np.percentile(tot,5)*100:+.0f}~{np.percentile(tot,95)*100:+.0f}%), "
          f"가속도 순위 백분위 {np.mean(tot < r)*100:.0f}% | 2015~ 중앙값 {np.median(tot10)*100:+.0f}%, 백분위 {np.mean(tot10 < r10)*100:.0f}%")
    eqq = pd.Series(C[:, ci['QQQ']], index=cal); print(fmt("QQQ 보유", eqq))
    print("=== 슈퍼루키-3 ===")
    for fill in ('cash', 'AZO', 'QQQ'):
        e, st = run_rookie(fill=fill); print(fmt(f"슈퍼루키-3 원본 이벤트, 빈 슬롯={fill}", e), st)
    azo = pd.Series(C[:, ci['AZO']], index=cal); print(fmt("AZO 보유", azo))
    for mode, fill in [('official', 'cash'), ('official', 'QQQ'), ('wiki', 'cash'), ('wiki', 'QQQ'), ('wiki_core', 'QQQ')]:
        e, st = run_rookie(fill=fill, mode=mode, start='2016-01-04')
        m = metrics(e); mq = metrics(eqq, '2016-01-04')
        lab = {'official': '원본 편입 목록', 'wiki': '위키 실제 이력', 'wiki_core': '위키(종류주 제외)'}[mode]
        print(f"2016~2024 {lab}, 빈 슬롯={fill:<5} 누적 {m['total']*100:+7.1f}% CAGR {m['cagr']*100:+6.2f}% MDD {m['mdd']*100:6.1f}% 샤프 {m['sharpe']:.2f} {st}"
              f"  | 같은 기간 QQQ {mq['total']*100:+7.1f}% CAGR {mq['cagr']*100:+6.2f}% MDD {mq['mdd']*100:6.1f}%")
