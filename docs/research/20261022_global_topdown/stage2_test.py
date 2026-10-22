"""2단계 — 미국 시장 방향(A1~A5)·업종 순위(B1~B5)·결합(E) 검정. PREREGISTRATION_stage2.md 그대로.

입력: DataVault/staging/global_macro (series/, derived/us_industry/)
출력: results_stage2.md, results_stage2.json, DataVault/staging/global_macro/signals/registry.json
  .venv/bin/python -I docs/research/20261022_global_topdown/stage2_test.py
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../..")))
from shared.data_library.global_macro import catalog, store  # noqa: E402
from shared.data_library.global_macro import panel as gp  # noqa: E402

DEV_END = pd.Timestamp("1999-06-30")
CONF = (pd.Timestamp("2000-01-31"), pd.Timestamp("2026-02-28"))
H = 6
D = store.path("derived", "us_industry")
RET = pd.read_parquet(os.path.join(D, "ret_vw.parquet"))
BM = pd.read_parquet(os.path.join(D, "bm.parquet"))
FF = pd.read_parquet(os.path.join(D, "ff3.parquet"))
IP = pd.read_parquet(os.path.join(D, "fund_ip.parquet"))
CAP = pd.read_parquet(os.path.join(D, "fund_cap.parquet"))
MONTHS = RET.index
MKT = FF["Mkt-RF"] + FF["RF"]


# ---------------------------------------------------------------- 공통
def nw_t(y: np.ndarray, x: np.ndarray, lag: int):
    X = np.column_stack([np.ones(len(x)), x])
    b = np.linalg.lstsq(X, y, rcond=None)[0]
    e = y - X @ b
    XtXi = np.linalg.inv(X.T @ X)
    S = (X * e[:, None]).T @ (X * e[:, None])
    for L in range(1, lag + 1):
        G = (X[L:] * e[L:, None]).T @ (X[:-L] * e[:-L, None])
        S += (1 - L / (lag + 1)) * (G + G.T)
    se = np.sqrt(np.diag(XtXi @ S @ XtXi))
    return b, se


def mean_nw(v: pd.Series, lag: int):
    v = v.dropna()
    if len(v) < 24:
        return np.nan, np.nan, len(v)
    x = v.values
    m = x.mean()
    e = x - m
    s = (e @ e) / len(x)
    for L in range(1, lag + 1):
        s += 2 * (1 - L / (lag + 1)) * (e[L:] @ e[:-L]) / len(x)
    return m, m / np.sqrt(s / len(x)), len(x)


def fwd_log(r: pd.DataFrame, h: int) -> pd.DataFrame:
    """t 다음 h개월 누적 로그수익(t+1..t+h), 하나라도 결측이면 NaN."""
    lr = np.log1p(r)
    s = lr[::-1].rolling(h, min_periods=h).sum()[::-1].shift(-1)
    return s


def pit_series(key: str, at: pd.DatetimeIndex, transform=None) -> pd.Series:
    s = gp.load(key)
    if transform is not None:
        s = transform(s)
    v, _ = gp.point_in_time(s.dropna(), catalog.BY_KEY[key]["lag_days"], at)
    return v


def pit_frame(df: pd.DataFrame, lag: int, at: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame({c: gp.point_in_time(df[c].dropna(), lag, at)[0] for c in df.columns})


def in_period(idx, which):
    if which == "dev":
        return idx <= DEV_END
    return (idx >= CONF[0]) & (idx <= CONF[1])


# ---------------------------------------------------------------- 신호
def signals_B():
    lr = np.log1p(RET)
    b1 = lr.shift(1).rolling(11, min_periods=11).sum()                        # t−11..t−1
    yr = pd.Series([d.year if d.month >= 7 else d.year - 1 for d in MONTHS], index=MONTHS)
    b2 = pd.DataFrame(BM.reindex(yr.values).values, index=MONTHS, columns=BM.columns)
    # B3 경기 국면
    cli = pit_series("us_cli", MONTHS)
    chg = cli - cli.shift(6)
    phase = ((cli >= 100).astype(int) * 2 + (chg >= 0).astype(int)).where(cli.notna() & chg.notna())
    fwd6 = fwd_log(RET, H)
    ex6 = fwd6.sub(fwd_log(MKT.to_frame("m"), H)["m"], axis=0)
    b3 = pd.DataFrame(np.nan, index=MONTHS, columns=RET.columns)
    first = phase.first_valid_index()
    for i, t in enumerate(MONTHS):
        if pd.isna(phase.get(t)) or t < first + pd.DateOffset(months=120):
            continue
        past = MONTHS[(MONTHS <= t - pd.DateOffset(months=H)) & (MONTHS >= first)]
        same = past[phase.reindex(past).values == phase[t]]
        if len(same) >= 12:
            b3.loc[t] = ex6.loc[same].mean()
    # B4·B5 업종 생산능력·생산
    capy = np.log(CAP / CAP.shift(12))
    b4 = pit_frame(capy, catalog.BY_KEY["us_indpro"]["lag_days"], MONTHS)
    ipy = np.log(IP / IP.shift(12)).rolling(3).mean()
    b5 = pit_frame(ipy - ipy.shift(3), catalog.BY_KEY["us_indpro"]["lag_days"], MONTHS)
    return {"B1": (b1, +1, "업종 모멘텀(12−1개월)"), "B2": (b2, +1, "업종 밸류(BE/ME)"),
            "B3": (b3, +1, "경기 국면별 업종(선행지수 4국면, 확장 창)"), "B4": (b4, -1, "공급 반응: 생산능력 전년비"),
            "B5": (b5, +1, "생산 가속: 산업생산 전년비의 3개월 변화")}, phase


def signals_A():
    at = MONTHS
    a1 = np.log1p(FF["Mkt-RF"]).rolling(12, min_periods=12).sum()
    cli = pit_series("us_cli", at)
    a2 = cli - cli.shift(6)
    baa, g10, tb = pit_series("us_baa_m", at), pit_series("us_gs10", at), pit_series("us_tb3m", at)
    spr = baa - g10
    a3 = spr - spr.shift(6)
    a4 = g10 - tb
    a5 = pit_series("us_cape", at)
    return {"A1": (a1, +1, "시장 12개월 초과수익(추세)"), "A2": (a2, +1, "미국 선행지수 6개월 변화"),
            "A3": (a3, -1, "신용 스프레드 6개월 변화"), "A4": (a4, +1, "장단기 금리차(10년−3개월)"), "A5": (a5, -1, "CAPE 밸류에이션")}


# ---------------------------------------------------------------- 검정
def cross_section(sig: pd.DataFrame, h: int):
    fl = fwd_log(RET, h)
    ics, spr, hit = {}, {}, {}
    for t in sig.index:
        s = sig.loc[t]
        f = fl.loc[t] if t in fl.index else None
        if f is None:
            continue
        ok = s.notna() & f.notna()
        if ok.sum() < 8:
            continue
        a, b = s[ok], f[ok]
        ics[t] = a.rank().corr(b.rank())
        n = len(a) // 3
        order = a.sort_values().index
        top, bot = np.expm1(b[order[-n:]]).mean(), np.expm1(b[order[:n]]).mean()
        spr[t], hit[t] = top - bot, float(top > bot)
    return pd.Series(ics), pd.Series(spr), pd.Series(hit)


def test_B(B):
    rows = []
    for k, (sig, sign, name) in B.items():
        rec = {"id": k, "family": "B", "name": name, "sign": sign}
        for h in (3, 6, 12):
            ic, sp, hit = cross_section(sig, h)
            for per in ("dev", "conf"):
                m = in_period(ic.index, per) if h == 6 else (ic.index <= DEV_END - pd.DateOffset(months=h - 6) if per == "dev" else (ic.index >= CONF[0]) & (ic.index <= CONF[1] + pd.DateOffset(months=6 - h)))
                icm, t, n = mean_nw(ic[m], h)
                rec[f"{per}_ic{h}"], rec[f"{per}_t{h}"], rec[f"{per}_n{h}"] = icm, t, n
                rec[f"{per}_spread{h}"] = float(sp[m].mean()) if m.any() else np.nan
                rec[f"{per}_hit{h}"] = float(hit[m].mean()) if m.any() else np.nan
            if h == 6:
                dv = ic[in_period(ic.index, "dev")].dropna()
                thirds = np.array_split(dv, 3) if len(dv) >= 30 else []
                rec["dev_thirds"] = f"{sum(1 for x in thirds if np.sign(x.mean()) == sign)}/{len(thirds)}"
                rec["start"] = str(ic.dropna().index.min().date()) if len(ic.dropna()) else None
                rec["n_ind"] = int(sig.notna().sum(axis=1).max())
        rec["verdict"] = verdict(sign, rec["dev_ic6"], rec["dev_t6"], rec["conf_ic6"])
        rows.append(rec)
    return rows


def test_A(A):
    rows = []
    rf = np.log1p(FF["RF"])
    ex = {h: (fwd_log(MKT.to_frame("m"), h)["m"] - rf[::-1].rolling(h, min_periods=h).sum()[::-1].shift(-1)) for h in (3, 6, 12)}
    for k, (sig, sign, name) in A.items():
        rec = {"id": k, "family": "A", "name": name, "sign": sign}
        dev_sig = sig[in_period(sig.index, "dev")].dropna()
        mu, sd, med = dev_sig.mean(), dev_sig.std(), dev_sig.median()
        rec["dev_mean"], rec["dev_sd"], rec["dev_median"] = float(mu), float(sd), float(med)
        for h in (3, 6, 12):
            for per in ("dev", "conf"):
                m = in_period(sig.index, per)
                d = pd.DataFrame({"x": (sig - mu) / sd, "y": ex[h], "raw": sig}).loc[m].dropna()
                if len(d) < 24:
                    continue
                b, se = nw_t(d.y.values, d.x.values, h)
                rec[f"{per}_slope{h}"], rec[f"{per}_t{h}"], rec[f"{per}_n{h}"] = float(b[1]), float(b[1] / se[1]), len(d)
                fav = sign * (d.raw - med) > 0
                rec[f"{per}_pup_fav{h}"] = float((d.y[fav] > 0).mean())
                rec[f"{per}_pup_unfav{h}"] = float((d.y[~fav] > 0).mean())
                rec[f"{per}_pup_all{h}"] = float((d.y > 0).mean())
                rec[f"{per}_n_fav{h}"], rec[f"{per}_n_unfav{h}"] = int(fav.sum()), int((~fav).sum())
                rec[f"{per}_ret_fav{h}"] = float(np.expm1(d.y[fav]).mean())
                rec[f"{per}_ret_unfav{h}"] = float(np.expm1(d.y[~fav]).mean())
        rec["start"] = str(sig.dropna().index.min().date())
        rec["verdict"] = verdict(sign, rec.get("dev_slope6"), rec.get("dev_t6"), rec.get("conf_slope6"))
        rows.append(rec)
    return rows


def verdict(sign, dev, t, conf):
    if dev is None or conf is None or np.isnan(dev) or np.isnan(conf):
        return "판정 불가"
    if sign * dev <= 0 or sign * conf <= 0:
        return "버림"
    if sign * t >= 2 and sign * conf >= 0.5 * sign * dev:
        return "실전 후보"
    return "작게 실험"


# ---------------------------------------------------------------- 결합
def perf(r: pd.Series):
    r = r.dropna()
    eq = (1 + r).cumprod()
    yrs = len(r) / 12
    return {"cagr": float(eq.iloc[-1] ** (1 / yrs) - 1), "vol": float(r.std() * np.sqrt(12)),
            "mdd": float((eq / eq.cummax() - 1).min()), "n": len(r)}


def combo_B(B, rows):
    pick = [r for r in rows if r["sign"] * (r.get("dev_ic6") or 0) > 0 and r["sign"] * (r.get("dev_t6") or 0) >= 1.5]
    out = {"picked": [r["id"] for r in pick]}
    if not pick:
        return out
    zs = []
    for r in pick:
        s = B[r["id"]][0] * r["sign"]
        zs.append(s.sub(s.mean(axis=1), axis=0).div(s.std(axis=1), axis=0))
    score = sum(z.fillna(0) for z in zs) / sum(z.notna().astype(int) for z in zs).replace(0, np.nan)
    W = pd.DataFrame(0.0, index=MONTHS, columns=RET.columns)
    cohorts = {}
    for t in MONTHS:
        s = score.loc[t].dropna()
        s = s[RET.loc[t, s.index].notna()]
        if len(s) < 12:
            continue
        n = len(s) // 3
        cohorts[t] = list(s.sort_values().index[-n:])
    port, ew, cost = {}, {}, {}
    prev_w = None
    for i, m in enumerate(MONTHS[1:], 1):
        held = [cohorts.get(MONTHS[i - k]) for k in range(1, H + 1) if i - k >= 0]
        held = [c for c in held if c]
        if len(held) < H:
            continue
        w = pd.Series(0.0, index=RET.columns)
        for c in held:
            w[c] += 1.0 / (H * len(c))
        r = RET.loc[m].fillna(0)
        port[m] = float((w * r).sum())
        cost[m] = 0.002 * float((w - prev_w).abs().sum()) if prev_w is not None else 0.0
        prev_w = w
        ew[m] = float(RET.loc[m].mean())
    P = pd.DataFrame({"rule": pd.Series(port) - pd.Series(cost), "rule_gross": pd.Series(port), "ew49": pd.Series(ew), "mkt": MKT})
    for per, (a, b) in {"dev": (P.index.min(), pd.Timestamp("1999-12-31")), "conf": (pd.Timestamp("2000-01-31"), pd.Timestamp("2026-08-31"))}.items():
        Q = P.loc[a:b].dropna()
        out[per] = {c: perf(Q[c]) for c in Q.columns}
        out[per]["turnover_cost_yr"] = float(pd.Series(cost).loc[a:b].mean() * 12)
    return out


def combo_A(A, rows):
    pick = [r for r in rows if r["sign"] * (r.get("dev_slope6") or 0) > 0 and r["sign"] * (r.get("dev_t6") or 0) >= 1.5]
    out = {"picked": [r["id"] for r in pick]}
    if not pick:
        return out
    z = sum(((A[r["id"]][0] - r["dev_mean"]) / r["dev_sd"] * r["sign"]).fillna(0) for r in pick) / \
        sum(A[r["id"]][0].notna().astype(int) for r in pick).replace(0, np.nan)
    w = (1 + 0.5 * z).clip(0.25, 1.5).fillna(1.0)
    wl = w.shift(1)                                      # t 말 신호 → t+1 월 수익
    rule = wl * MKT + (1 - wl) * FF["RF"] - 0.001 * wl.diff().abs()
    for per, (a, b) in {"dev": (pd.Timestamp("1927-01-31"), pd.Timestamp("1999-12-31")), "conf": (pd.Timestamp("2000-01-31"), pd.Timestamp("2026-08-31"))}.items():
        mw = float(wl.loc[a:b].mean())
        Q = pd.DataFrame({"rule": rule, "hold": MKT, "matched": mw * MKT + (1 - mw) * FF["RF"]}).loc[a:b].dropna()
        out[per] = {c: perf(Q[c]) for c in Q.columns}
        out[per]["avg_w"] = mw
    out["w_now"] = float(w.dropna().iloc[-1])
    out["z_now"] = float(z.dropna().iloc[-1])
    out["w_series"] = [[str(i.date()), round(float(v), 3)] for i, v in w.loc["1990":].items()]
    return out


# ---------------------------------------------------------------- 현재값
def current_B(B, rows):
    cur = {}
    for r in rows:
        s = B[r["id"]][0].dropna(how="all")
        if s.empty:
            continue
        t = s.index[-1]
        v = (s.loc[t] * r["sign"]).dropna().sort_values()
        cur[r["id"]] = {"date": str(t.date()), "top": list(v.index[-5:][::-1]), "bottom": list(v.index[:5])}
    return cur


def current_A(A, rows):
    cur = {}
    for r in rows:
        s = A[r["id"]][0].dropna()
        v = float(s.iloc[-1])
        cur[r["id"]] = {"date": str(s.index[-1].date()), "value": v, "z": (v - r["dev_mean"]) / r["dev_sd"],
                        "favorable": bool(r["sign"] * (v - r["dev_median"]) > 0)}
    return cur


def main():
    B, phase = signals_B()
    A = signals_A()
    rb, ra = test_B(B), test_A(A)
    cb, ca = combo_B(B, rb), combo_A(A, ra)
    res = {"generated_at": datetime.now().isoformat(timespec="seconds"), "prereg_sha256": open(os.path.join(HERE, "PREREGISTRATION_stage2.sha256")).read().split()[0],
           "A": ra, "B": rb, "E_A": ca, "E_B": cb, "current_A": current_A(A, ra), "current_B": current_B(B, rb),
           "phase_now": None if phase.dropna().empty else {"date": str(phase.dropna().index[-1].date()), "phase": int(phase.dropna().iloc[-1])}}
    json.dump(res, open(os.path.join(HERE, "results_stage2.json"), "w"), ensure_ascii=False, indent=1, default=lambda o: None)
    report(res)
    write_registry(res)


def _combo_verdict(e, base_keys):
    """사전등록 E 판정: 비교 대비 연 초과 > 0 이고 MDD 가 더 나쁘지 않으면 작게 실험, +2%p 이상이면 실전 후보."""
    c = e.get("conf")
    if not c:
        return "판정 불가", ""
    r = c["rule"]
    parts, ok_all, strong = [], True, True
    for k in base_keys:
        ex = r["cagr"] - c[k]["cagr"]
        parts.append(f"{k} 대비 연 {ex * 100:+.1f}%p, MDD {r['mdd'] * 100:.0f}% vs {c[k]['mdd'] * 100:.0f}%")
        if not (ex > 0 and r["mdd"] >= c[k]["mdd"]):
            ok_all = False
        if ex < 0.02:
            strong = False
    return ("실전 후보" if ok_all and strong else "작게 실험" if ok_all else "버림"), " · ".join(parts)


def write_registry(res):
    """화면 '신호 상태' 탭이 읽는 신호 원장 — 사전등록 검정 결과와 현재 읽기."""
    sig = []
    for r in res["A"]:
        cur = res["current_A"].get(r["id"], {})
        sig.append({"id": r["id"], "family": "미국 시장 방향", "name": r["name"], "verdict": r["verdict"], "horizon": "6개월",
                    "effect_unit": "1σ당 6개월 초과수익 %p",
                    "dev": {"effect": r.get("dev_slope6"), "t": r.get("dev_t6"), "p_up_fav": r.get("dev_pup_fav6"), "p_up_unfav": r.get("dev_pup_unfav6"),
                            "n_fav": r.get("dev_n_fav6"), "n_unfav": r.get("dev_n_unfav6")},
                    "conf": {"effect": r.get("conf_slope6"), "t": r.get("conf_t6"), "p_up_fav": r.get("conf_pup_fav6"), "p_up_unfav": r.get("conf_pup_unfav6"),
                             "n_fav": r.get("conf_n_fav6"), "n_unfav": r.get("conf_n_unfav6")},
                    "current": {"date": cur.get("date"), "text": f"값 {cur.get('value', float('nan')):.2f}, z {cur.get('z', float('nan')):+.2f} → {'유리' if cur.get('favorable') else '불리'}"}})
    for r in res["B"]:
        cur = res["current_B"].get(r["id"], {})
        sig.append({"id": r["id"], "family": "미국 업종 순위", "name": r["name"], "verdict": r["verdict"], "horizon": "6개월",
                    "effect_unit": "순위상관 IC · 상위−하위 6개월 %p",
                    "dev": {"effect": r.get("dev_ic6"), "t": r.get("dev_t6"), "spread": r.get("dev_spread6"), "hit": r.get("dev_hit6")},
                    "conf": {"effect": r.get("conf_ic6"), "t": r.get("conf_t6"), "spread": r.get("conf_spread6"), "hit": r.get("conf_hit6")},
                    "current": {"date": cur.get("date"), "text": f"상위 {', '.join(cur.get('top', []))} / 하위 {', '.join(cur.get('bottom', []))}"}})
    va, ta = _combo_verdict(res["E_A"], ["hold", "matched"])
    vb, tb = _combo_verdict(res["E_B"], ["ew49", "mkt"])
    reg = {"stage": 2, "generated_at": res["generated_at"], "prereg_sha256": res["prereg_sha256"],
           "source": "docs/research/20261022_global_topdown/stage2_test.py", "periods": ["~1999", "2000~"], "signals": sig,
           "combos": [{"id": "E-A", "name": f"시장 비중 조절({'·'.join(res['E_A'].get('picked') or [])})", "verdict": va, "text": ta,
                       "now": f"현재 비중 {res['E_A'].get('w_now', float('nan')):.2f}"},
                      {"id": "E-B", "name": f"업종 상위 1/3({'·'.join(res['E_B'].get('picked') or [])})", "verdict": vb, "text": tb}]}
    fp = store.path("signals", "registry_stage2.json")
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=1, default=lambda o: None)
    print("신호 원장:", fp, [(x["id"], x["verdict"]) for x in sig], [(c["id"], c["verdict"]) for c in reg["combos"]])


def f(v, p=1, pct=False):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "–"
    return f"{v * 100:+.{p}f}" if pct else f"{v:+.{p}f}"


def report(res):
    L = ["# 2단계 결과 — 미국 시장 방향(A)·업종 순위(B)", "", f"사전등록 sha256 {res['prereg_sha256'][:12]}…, 개발 ~1999-06 / 확인 2000-01~2026-02, 주 지평 6개월. 계산 {res['generated_at']}", "",
         "## A. 미국 시장 방향 (6개월 초과수익)", "",
         "| # | 신호 | 예측 | 개발 기울기(%p/σ) | 개발 t | 확인 기울기 | 확인 t | 유리할 때 상승 확률(개발/확인) | 불리할 때(개발/확인) | 전체(개발/확인) | 판정 |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in res["A"]:
        L.append(f"| {r['id']} | {r['name']} | {'+' if r['sign'] > 0 else '−'} | {f(r.get('dev_slope6'), 1, True)} | {f(r.get('dev_t6'))} | {f(r.get('conf_slope6'), 1, True)} | {f(r.get('conf_t6'))} | "
                 f"{f(r.get('dev_pup_fav6'), 0, True)}/{f(r.get('conf_pup_fav6'), 0, True)} | {f(r.get('dev_pup_unfav6'), 0, True)}/{f(r.get('conf_pup_unfav6'), 0, True)} | "
                 f"{f(r.get('dev_pup_all6'), 0, True)}/{f(r.get('conf_pup_all6'), 0, True)} | {r['verdict']} |")
    L += ["", "보조(기울기 %p/σ, t): 3개월 / 12개월", ""]
    for r in res["A"]:
        L.append(f"- {r['id']}: 개발 3m {f(r.get('dev_slope3'), 1, True)}({f(r.get('dev_t3'))}) 12m {f(r.get('dev_slope12'), 1, True)}({f(r.get('dev_t12'))}) · "
                 f"확인 3m {f(r.get('conf_slope3'), 1, True)}({f(r.get('conf_t3'))}) 12m {f(r.get('conf_slope12'), 1, True)}({f(r.get('conf_t12'))}) · 유리/불리 6개월 평균수익 개발 {f(r.get('dev_ret_fav6'), 1, True)}/{f(r.get('dev_ret_unfav6'), 1, True)} 확인 {f(r.get('conf_ret_fav6'), 1, True)}/{f(r.get('conf_ret_unfav6'), 1, True)}")
    L += ["", "## B. 업종 순위 (6개월)", "",
          "| # | 신호 | 예측 | 업종 수 | 시작 | 개발 IC | 개발 t | 개발 상위−하위 | 개발 적중 | 토막 | 확인 IC | 확인 t | 확인 상위−하위 | 확인 적중 | 판정 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in res["B"]:
        L.append(f"| {r['id']} | {r['name']} | {'+' if r['sign'] > 0 else '−'} | {r.get('n_ind')} | {r.get('start')} | {f(r.get('dev_ic6'), 3)} | {f(r.get('dev_t6'))} | {f(r.get('dev_spread6'), 1, True)} | "
                 f"{f(r.get('dev_hit6'), 0, True)} | {r.get('dev_thirds')} | {f(r.get('conf_ic6'), 3)} | {f(r.get('conf_t6'))} | {f(r.get('conf_spread6'), 1, True)} | {f(r.get('conf_hit6'), 0, True)} | {r['verdict']} |")
    L += ["", "보조 IC(t): 3개월 / 12개월", ""]
    for r in res["B"]:
        L.append(f"- {r['id']}: 개발 {f(r.get('dev_ic3'), 3)}({f(r.get('dev_t3'))}) / {f(r.get('dev_ic12'), 3)}({f(r.get('dev_t12'))}) · 확인 {f(r.get('conf_ic3'), 3)}({f(r.get('conf_t3'))}) / {f(r.get('conf_ic12'), 3)}({f(r.get('conf_t12'))})")
    for nm, key in (("E-A 시장 비중", "E_A"), ("E-B 업종 상위 1/3", "E_B")):
        e = res[key]
        L += ["", f"## {nm} — 선택: {', '.join(e.get('picked') or []) or '없음'}", ""]
        for per in ("dev", "conf"):
            if per not in e:
                continue
            L.append(f"**{'개발' if per == 'dev' else '확인'}**: " + " · ".join(f"{c} 연 {f(v['cagr'], 1, True)}% 변동 {v['vol'] * 100:.0f}% MDD {v['mdd'] * 100:.0f}%" for c, v in e[per].items() if isinstance(v, dict))
                     + (f" · 평균 비중 {e[per].get('avg_w', 0):.2f}" if "avg_w" in e[per] else "") + (f" · 비용 연 {e[per].get('turnover_cost_yr', 0) * 100:.2f}%" if "turnover_cost_yr" in e[per] else ""))
    L += ["", "## 현재 (최신 데이터)", ""]
    for k, v in res["current_A"].items():
        L.append(f"- {k}: {v['date']} 값 {v['value']:.3f}, z {v['z']:+.2f} → {'유리' if v['favorable'] else '불리'}")
    for k, v in res["current_B"].items():
        L.append(f"- {k}: {v['date']} 상위 {', '.join(v['top'])} / 하위 {', '.join(v['bottom'])}")
    if res.get("phase_now"):
        L.append(f"- 경기 국면(선행지수): {res['phase_now']['date']} 국면 {res['phase_now']['phase']} (0 수축·1 회복·2 둔화·3 확장)")
    open(os.path.join(HERE, "results_stage2.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
