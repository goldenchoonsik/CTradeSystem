"""삼성전자·SK하이닉스 스윙 매매법 판정 — PREREGISTRATION.md 그대로.

실행: .venv/bin/python -I docs/research/20261021_samsung_hynix_swing/swing_test.py
출력: 같은 폴더 results.md, results.csv, episodes.csv
"""
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = "/home/kuzimo/projects/CTradeSystem/DataVault"
CREON = f"{ROOT}/staging/kr_equity/raw/creon/candle_1d"
YAHOO = f"{ROOT}/raw/yahoo"
DEV = ("2000-01-03", "2015-12-30")
HOLD = ("2016-01-04", "2026-09-11")
FULL = (DEV[0], HOLD[1])
BUY_C, SELL_C = 0.0012, 0.0033
W_S, W_H = 0.6, 0.4


# ---------- 데이터 ----------
def load_kr(code):
    d = pd.read_csv(f"{CREON}/A{code}_1d.csv")
    d.index = pd.to_datetime(d["timestamp"])
    d = d.rename(columns={"upbit_open": "open", "upbit_high": "high", "upbit_low": "low", "upbit_close": "close"})
    d = d[["open", "high", "low", "close"]].astype(float)
    return d[(d["close"] > 0) & (d["open"] > 0)]


def load_us(sym):
    d = pd.read_csv(f"{YAHOO}/candle_1d/{sym}_1d.csv")
    c = pd.Series(d["close"].astype(float).values, index=pd.to_datetime(d["timestamp"])).sort_index()
    ca = pd.read_csv(f"{YAHOO}/corporate_actions/{sym}.csv")
    for _, r in ca[ca["type"] == "split"].iterrows():
        c[c.index < pd.Timestamp(r["date"])] /= r["numerator"] / r["denominator"]
    return c


S = load_kr("005930")
H = load_kr("000660")
idx = S.index.intersection(H.index)
idx = idx[(idx >= "1997-01-01") & (idx <= HOLD[1])]
S, H = S.loc[idx], H.loc[idx]

MU = load_us("MU")
SOXX = load_us("SOXX")


def us_align(series):
    """KR t일 종가 판단에 쓸 수 있는 미국 값 = 미국 날짜 < t 의 마지막 값."""
    s = series[~series.index.duplicated()].sort_index()
    v = s.reindex(idx - pd.Timedelta(days=1), method="ffill")
    return pd.Series(v.values, index=idx)


# ---------- 지표 ----------
def ma(c, n):
    return c.rolling(n).mean()


def rsi(c, n):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def state(entry, exit_):
    pos, out = 0, []
    for e, x in zip(entry.fillna(False).values, exit_.fillna(False).values):
        if pos == 0 and e:
            pos = 1
        elif pos == 1 and x:
            pos = 0
        out.append(pos)
    return pd.Series(out, index=entry.index, dtype=float)


month_end = pd.Series(idx.to_period("M"), index=idx)
is_me = month_end != month_end.shift(-1)
week = pd.Series(idx.to_period("W"), index=idx)
is_we = week != week.shift(-1)


def at_points(cond, points):
    return cond.astype(float).where(points).ffill().fillna(0.0)


MU_A = us_align(MU)
MU_MA200 = us_align(MU.rolling(200).mean())
MU_R126 = us_align(MU / MU.shift(126) - 1)
SOXX_A = us_align(SOXX)
SOXX_MA200 = us_align(SOXX.rolling(200).mean())


def rules_single(d):
    c, hi, lo = d["close"], d["high"], d["low"]
    m5, m20, m50, m60, m200 = ma(c, 5), ma(c, 20), ma(c, 50), ma(c, 60), ma(c, 200)
    sd20 = c.rolling(20).std(ddof=0)
    bb_lo, bb_hi = m20 - 2 * sd20, m20 + 2 * sd20
    r2, r14 = rsi(c, 2), rsi(c, 14)
    vol20 = c.pct_change().rolling(20).std() * np.sqrt(252)
    med = vol20.expanding(min_periods=252).median()
    u2 = (SOXX_A > SOXX_MA200).astype(float).where(SOXX_MA200.notna(), 1.0)
    return {
        "T1 종가>200일선": (c > m200).astype(float),
        "T2 50>200일선": (m50 > m200).astype(float),
        "T3 20>60일선": (m20 > m60).astype(float),
        "T4 5>20일선": (m5 > m20).astype(float),
        "T5 돈치안20/10": state(c > hi.shift(1).rolling(20).max(), c < lo.shift(1).rolling(10).min()),
        "T6 돈치안55/20": state(c > hi.shift(1).rolling(55).max(), c < lo.shift(1).rolling(20).min()),
        "T7 12개월 모멘텀": at_points(c / c.shift(252) - 1 > 0, is_me),
        "T8 6개월 모멘텀": at_points(c / c.shift(126) - 1 > 0, is_me),
        "P1 Connors RSI2": state((c > m200) & (r2 < 10), c > m5),
        "P2 200선 위 볼린저하단": state((c > m200) & (c < bb_lo), c > m20),
        "M1 RSI14 30/70": state(r14 < 30, r14 > 70),
        "M2 볼린저 하단/상단": state(c < bb_lo, c > bb_hi),
        "M3 이격도90": state(c / m20 < 0.90, c >= m20),
        "U1 마이크론>200일선": (MU_A > MU_MA200).astype(float),
        "U2 SOXX>200일선": u2,
        "U3 마이크론 6개월 모멘텀": (MU_R126 > 0).astype(float),
        "V1 변동성 조절": (med / vol20).clip(upper=1.0).where(is_we).ffill().fillna(0.0),
    }


def rules_rotation():
    cs, ch = S["close"], H["close"]
    r63s, r63h = cs / cs.shift(63) - 1, ch / ch.shift(63) - 1
    r252s, r252h = cs / cs.shift(252) - 1, ch / ch.shift(252) - 1
    out = {}
    pick = (r63s >= r63h)
    out["R1 63일 강한 종목"] = (at_points(pick, is_me), at_points(~pick & r63h.notna(), is_me))
    win_s = r252s >= r252h
    ws = (win_s & (r252s > 0))
    wh = (~win_s & (r252h > 0) & r252h.notna())
    out["R2 듀얼 모멘텀 12개월"] = (at_points(ws, is_me), at_points(wh, is_me))
    lr = np.log(cs / ch)
    z = (lr - lr.rolling(60).mean()) / lr.rolling(60).std()
    st, a, b = 0, [], []
    for zz in z.values:
        if np.isnan(zz):
            st = 0
        elif st == 0 and zz > 2:
            st = 1
        elif st == 0 and zz < -2:
            st = -1
        elif st == 1 and zz <= 0:
            st = 0
        elif st == -1 and zz >= 0:
            st = 0
        a.append(W_S if st == 0 else (0.0 if st == 1 else 1.0))
        b.append(W_H if st == 0 else (1.0 if st == 1 else 0.0))
    out["R3 가격비 z±2"] = (pd.Series(a, index=idx), pd.Series(b, index=idx))
    return out


# ---------- 수익 계산 ----------
R_S = (S["open"].shift(-1) / S["open"] - 1)
R_H = (H["open"].shift(-1) / H["open"] - 1)


def leg(pos, R, k=1.0, cash=0.0):
    a = pos.shift(1).fillna(0.0)
    dlt = a.diff().fillna(a)
    cost = k * (BUY_C * dlt.clip(lower=0) + SELL_C * (-dlt).clip(lower=0))
    r = (1 - cost) * (1 + a * R + (1 - a) * cash / 252) - 1
    return r, a


def build(target, pos, k=1.0, cash=0.0):
    """target: SAM/HYN/PAIR/ROT. pos: Series (단일) 또는 (wS,wH)."""
    if target == "SAM":
        r, a = leg(pos, R_S, k, cash)
    elif target == "HYN":
        r, a = leg(pos, R_H, k, cash)
    elif target == "PAIR":
        rs, as_ = leg(pos[0], R_S, k, cash)
        rh, ah = leg(pos[1], R_H, k, cash)
        r, a = W_S * rs + W_H * rh, W_S * as_ + W_H * ah
    else:  # ROT: 한 계좌에서 두 종목 비중
        ws, wh = pos
        a_s, a_h = ws.shift(1).fillna(0.0), wh.shift(1).fillna(0.0)
        cost = 0
        for aa in (a_s, a_h):
            dlt = aa.diff().fillna(aa)
            cost = cost + k * (BUY_C * dlt.clip(lower=0) + SELL_C * (-dlt).clip(lower=0))
        a = a_s + a_h
        r = (1 - cost) * (1 + a_s * R_S + a_h * R_H + (1 - a) * cash / 252) - 1
    return r, a


BENCH = {"SAM": R_S, "HYN": R_H, "PAIR": W_S * R_S + W_H * R_H, "ROT": W_S * R_S + W_H * R_H}


def sl(x, per):
    return x.loc[per[0]:per[1]].dropna()


def metrics(r):
    eq = (1 + r).cumprod()
    yrs = (r.index[-1] - r.index[0]).days / 365.25
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    mdd = (eq / np.maximum(eq.cummax(), 1.0) - 1).min()
    sh = r.mean() / r.std() * np.sqrt(252)
    return cagr, mdd, sh


def monthly(r):
    return (1 + r).resample("ME").prod() - 1


def tstat(x):
    x = x.dropna()
    return x.mean() / x.std(ddof=1) * np.sqrt(len(x))


def evaluate(target, name, pos):
    row = {"target": target, "rule": name}
    r, a = build(target, pos)
    r2, _ = build(target, pos, k=2.0)
    rc, _ = build(target, pos, cash=0.025)
    b = BENCH[target]
    for lab, per in (("dev", DEV), ("hold", HOLD), ("full", FULL)):
        rr, bb, aa = sl(r, per), sl(b, per), sl(a, per).reindex(sl(r, per).index)
        bb = bb.reindex(rr.index)
        f = aa.mean()
        cg, md, sh = metrics(rr)
        bcg, bmd, bsh = metrics(bb)
        mcg, mmd, _ = metrics(f * bb)
        em_a = monthly(rr) - monthly(bb)
        em_b = monthly(rr) - monthly(f * bb)
        yrs = (rr.index[-1] - rr.index[0]).days / 365.25
        turns = aa.diff().abs().sum() / 2 / yrs
        row.update({f"{lab}_cagr": cg, f"{lab}_hold_cagr": bcg, f"{lab}_mdd": md, f"{lab}_hold_mdd": bmd,
                    f"{lab}_sharpe": sh, f"{lab}_hold_sharpe": bsh, f"{lab}_expo": f, f"{lab}_match_cagr": mcg,
                    f"{lab}_match_mdd": mmd, f"{lab}_exA_mean": em_a.mean(), f"{lab}_tA": tstat(em_a),
                    f"{lab}_exB_mean": em_b.mean(), f"{lab}_tB": tstat(em_b), f"{lab}_rt_per_yr": turns})
    rr2, bb2 = sl(r2, FULL), sl(b, FULL)
    bb2 = bb2.reindex(rr2.index)
    f_full = sl(a, FULL).reindex(rr2.index).mean()
    row["full_cagr_2x"] = metrics(rr2)[0]
    row["full_exB_2x"] = (monthly(rr2) - monthly(f_full * bb2)).mean()
    row["full_cagr_cash25"] = metrics(sl(rc, FULL))[0]
    row["pass_A"] = bool(row["dev_cagr"] > row["dev_hold_cagr"] and row["hold_cagr"] > row["hold_hold_cagr"]
                         and row["full_tA"] >= 2.0 and row["full_cagr_2x"] > row["full_hold_cagr"])
    row["pass_B"] = bool(row["dev_exB_mean"] > 0 and row["hold_exB_mean"] > 0 and row["full_tB"] >= 2.0
                         and row["full_mdd"] > row["full_match_mdd"] and row["full_exB_2x"] > 0)
    return row, r, a


def episodes(b):
    """보유 기준 −30% 이상 고점→저점 구간."""
    eq = (1 + sl(b, FULL)).cumprod()
    dd = eq / eq.cummax() - 1
    out, in_ep, pk = [], False, None
    for d, v in dd.items():
        if not in_ep and v < -0.30:
            in_ep, pk = True, eq[:d].idxmax()
        if in_ep and v == 0:
            out.append((pk, eq[pk:d].idxmin()))
            in_ep = False
    if in_ep:
        out.append((pk, eq[pk:].idxmin()))
    return out


def main():
    # 데이터 점검
    lines = ["# 삼성전자·SK하이닉스 스윙 매매법 판정 결과", "",
             f"사전등록: PREREGISTRATION.md (sha256 {open(os.path.join(HERE, 'PREREGISTRATION.sha256')).read().split()[0][:16]}…)", ""]
    for nm, d in (("삼성", S), ("하이닉스", H)):
        cc = d["close"].loc[FULL[0]:FULL[1]].pct_change().abs()
        big = cc[cc > 0.30]
        lines.append(f"- 데이터 점검 {nm}: {FULL[0]}~{FULL[1]} {len(cc)}일, 일간 |수익|>30% {len(big)}일 {list(big.index.strftime('%Y-%m-%d'))[:5]}")
    lines.append(f"- 마이크론 분할 보정 점검: 2000-04-28 {MU['2000-04-24':'2000-05-01'].iloc[-1]:.2f} → 2000-05-02 {MU['2000-05-02':'2000-05-05'].iloc[0]:.2f}; "
                 f"SOXX 2024-03-06 {SOXX['2024-03-01':'2024-03-06'].iloc[-1]:.2f} → 03-07 {SOXX['2024-03-07':'2024-03-10'].iloc[0]:.2f}")
    lines.append("")

    rows, rets = [], {}
    single = {"SAM": rules_single(S), "HYN": rules_single(H)}
    for name in single["SAM"]:
        for t in ("SAM", "HYN"):
            row, r, a = evaluate(t, name, single[t][name])
            rows.append(row)
            rets[(t, name)] = r
        row, r, a = evaluate("PAIR", name, (single["SAM"][name], single["HYN"][name]))
        rows.append(row)
        rets[("PAIR", name)] = r
    for name, (ws, wh) in rules_rotation().items():
        row, r, a = evaluate("ROT", name, (ws, wh))
        rows.append(row)
        rets[("ROT", name)] = r
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(HERE, "results.csv"), index=False)

    # 보유 기준선
    lines.append("## 기준선 (보유, 비용 없음)")
    lines.append("| 대상 | 개발 CAGR | 검증 CAGR | 전체 CAGR | 전체 MDD | 전체 샤프 |")
    lines.append("|---|---|---|---|---|---|")
    for t in ("SAM", "HYN", "PAIR"):
        x = df[df.target == t].iloc[0]
        lines.append(f"| {t} | {x.dev_hold_cagr:.1%} | {x.hold_hold_cagr:.1%} | {x.full_hold_cagr:.1%} | {x.full_hold_mdd:.0%} | {x.full_hold_sharpe:.2f} |")
    lines.append("")

    lines.append(f"## 판정: A 합격 {int(df.pass_A.sum())} / B 합격 {int(df.pass_B.sum())} (총 {len(df)})")
    lines.append("")
    hdr = ("| 대상 | 규칙 | 개발 CAGR(보유) | 검증 CAGR(보유) | 전체 CAGR | 전체 MDD(노출맞춘보유) | 샤프(보유) | 노출 | 왕복/년 | tA | tB | 비용2배 CAGR | 현금2.5% CAGR | A | B |")
    for t in ("SAM", "HYN", "PAIR", "ROT"):
        lines.append(f"### {t}")
        lines.append(hdr)
        lines.append("|" + "---|" * 15)
        for _, x in df[df.target == t].iterrows():
            lines.append(
                f"| {t} | {x.rule} | {x.dev_cagr:.1%} ({x.dev_hold_cagr:.1%}) | {x.hold_cagr:.1%} ({x.hold_hold_cagr:.1%}) | "
                f"{x.full_cagr:.1%} | {x.full_mdd:.0%} ({x.full_match_mdd:.0%}) | {x.full_sharpe:.2f} ({x.full_hold_sharpe:.2f}) | "
                f"{x.full_expo:.0%} | {x.full_rt_per_yr:.1f} | {x.full_tA:.2f} | {x.full_tB:.2f} | {x.full_cagr_2x:.1%} | "
                f"{x.full_cagr_cash25:.1%} | {'O' if x.pass_A else ''} | {'O' if x.pass_B else ''} |")
        lines.append("")

    # 메모리 하락 국면(PAIR 보유 기준)
    eps = episodes(BENCH["PAIR"])
    ep_rows = []
    for name in list(single["SAM"]) + list(rules_rotation()):
        key = ("PAIR", name) if ("PAIR", name) in rets else ("ROT", name)
        r = rets[key]
        rec = {"rule": name}
        for pk, tr in eps:
            rec[f"{pk.date()}→{tr.date()}"] = (1 + r.loc[pk:tr].iloc[1:]).prod() - 1
        ep_rows.append(rec)
    hold_rec = {"rule": "보유 6:4"}
    for pk, tr in eps:
        hold_rec[f"{pk.date()}→{tr.date()}"] = (1 + BENCH["PAIR"].loc[pk:tr].iloc[1:]).prod() - 1
    ep = pd.DataFrame([hold_rec] + ep_rows)
    ep.to_csv(os.path.join(HERE, "episodes.csv"), index=False)
    lines.append("## 메모리 하락 국면별 (6:4 보유 −30% 이상 구간, PAIR/교대 규칙 손익)")
    cols = [c for c in ep.columns if c != "rule"]
    lines.append("| 규칙 | " + " | ".join(cols) + " |")
    lines.append("|" + "---|" * (len(cols) + 1))
    for _, x in ep.iterrows():
        lines.append(f"| {x.rule} | " + " | ".join(f"{x[c]:.0%}" for c in cols) + " |")
    lines.append("")

    with open(os.path.join(HERE, "results.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
