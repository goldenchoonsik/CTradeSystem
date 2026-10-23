"""시총(거래대금) 가속도 TOP10 — 당시 기준 명단 시험 (2026-10-21)

원본 명단(NAS_UNIVERSE)은 2024~26 나스닥100 이라 사후 선정이다. 여기서는 매월 초 직전 60일 달러 거래대금 상위 100개
나스닥 상장 보통주(ETF·워런트·유닛·우선주 제외)를 '당시 나스닥100 대용'으로 쓴다.
한계: us_equity 원천에는 2026-10 현재 상장 종목만 있다(상장폐지 종목 없음 → 생존 편향, 특히 2000~2002).
      그래서 가속도가 무작위를 못 이기면 강한 반증, 이기면 '확정 아님'.
실행: .venv/bin/python docs/research/20261021_slot_swing_recompute/recompute_nasdaq_pit.py
"""
import os, sys, re
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from us_data import load, ROOT
from recompute_nasdaq import cal, RF, metrics, fmt, COST, months, mset, last_px as _unused  # noqa: F401

u = pd.read_csv(f"{ROOT}/staging/us_equity/reference/universe.csv")
bad = re.compile(r'warrant|\bunits?\b|\brights?\b|preferred|depositary shares? .*(series|%)|notes due|%', re.I)
u = u[(u.exchange == 'NASDAQ') & (u.etf == 'N') & ~u.name.fillna('').str.contains(bad)]
syms = [s for s in u.yahoo_symbol.astype(str) if os.path.exists(f"{ROOT}/raw/yahoo/candle_1d/{s}_1d.csv")]
T = len(cal)
Cl, Op, Dv = [], [], []
keep = []
for s in syms:
    df = load(s)
    if df is None: continue
    df = df.loc['1998-06-01':'2024-12-31']
    if len(df) < 60: continue
    c = df['close'].reindex(cal).to_numpy(np.float32)
    if np.isnan(c).all(): continue
    keep.append(s); Cl.append(c); Op.append(df['open'].reindex(cal).to_numpy(np.float32))
    Dv.append(df['dvol_60d'].reindex(cal).ffill().to_numpy(np.float32))
C = np.column_stack(Cl); O = np.column_stack(Op); DVn = np.column_stack(Dv)
DV = pd.DataFrame(DVn, index=cal)
acc = ((DV / DV.shift(21) - 1) - (DV.shift(21) / DV.shift(42) - 1)).to_numpy()
print(f"나스닥 상장 보통주 {len(keep)}개 적재")

def lp(i, j):
    k = i
    while k >= 0 and np.isnan(C[k, j]): k -= 1
    return C[k, j] if k >= 0 else np.nan

def pool(i):
    """전일 기준 거래대금 상위 100 → 그중 중앙값 이상(=상위 50), 오늘 가격 있는 것."""
    row = DVn[i - 1] if i > 0 else DVn[0]
    ok = ~np.isnan(row)
    top = np.argsort(-np.where(ok, row, -np.inf))[:100]
    top = top[row[top] >= np.nanmedian(row[top])]
    return [int(j) for j in top if not np.isnan(O[i, j]) and not np.isnan(C[i, j]) and C[i, j] >= 1.0]

def sel_acc(i):
    p = [j for j in pool(i) if not np.isnan(acc[max(i - 1, 0), j])]
    return sorted(p, key=lambda j: -acc[max(i - 1, 0), j])[:10]

def run_monthly(select):
    sh = {}; cash = 1.0; eq = np.empty(T)
    for i in range(T):
        if i in mset:
            tgt = select(i)
            px = {j: (O[i, j] if not np.isnan(O[i, j]) else lp(i - 1, j)) for j in set(sh) | set(tgt)}
            val = cash + sum(s * px[j] for j, s in sh.items())
            w = val / len(tgt) if tgt else 0.0
            new = {j: w / px[j] for j in tgt}
            traded = sum(abs(new.get(j, 0) - sh.get(j, 0)) * px[j] for j in set(sh) | set(new))
            cash = val - sum(new[j] * px[j] for j in new) - traded * COST
            sh = new
        cash *= 1 + RF[i]
        eq[i] = cash + sum(s * lp(i, j) for j, s in sh.items())
    return pd.Series(eq, index=cal)

if __name__ == '__main__':
    eqa = run_monthly(sel_acc); print(fmt("[당시 명단] 가속도 TOP10", eqa))
    eqp = run_monthly(pool); print(fmt("[당시 명단] 후보 50 동일가중", eqp))
    rng = np.random.default_rng(7); tot, tot10 = [], []
    for _ in range(300):
        pk = {i: list(rng.permutation(pool(i))[:10]) for i in months}
        s = run_monthly(lambda i: pk[i]); tot.append(s.iloc[-1] / s.iloc[0] - 1); tot10.append(s.iloc[-1] / s.loc['2015-01-01':].iloc[0] - 1)
    tot, tot10 = np.array(tot), np.array(tot10)
    r = eqa.iloc[-1] / eqa.iloc[0] - 1; r10 = eqa.iloc[-1] / eqa.loc['2015-01-01':].iloc[0] - 1
    print(f"[당시 명단] 무작위 10종목 300회 — 전체 중앙값 {np.median(tot)*100:+.0f}% (5~95%: {np.percentile(tot,5)*100:+.0f}~{np.percentile(tot,95)*100:+.0f}%), "
          f"가속도 백분위 {np.mean(tot < r)*100:.0f}% | 2015~ 중앙값 {np.median(tot10)*100:+.0f}% (5~95%: {np.percentile(tot10,5)*100:+.0f}~{np.percentile(tot10,95)*100:+.0f}%), 백분위 {np.mean(tot10 < r10)*100:.0f}%")
    # 가속도 상위 보유 종목 빈도
    cnt = {}
    for i in months:
        for j in sel_acc(i): cnt[keep[j]] = cnt.get(keep[j], 0) + 1
    print("가장 자주 뽑힌 종목:", sorted(cnt.items(), key=lambda x: -x[1])[:15])
