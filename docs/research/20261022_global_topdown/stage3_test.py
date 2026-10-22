"""3단계 — 한국 업종 순위(K1·K2·X1·X2)·코스피 방향(K3·X3~X6)·결합(E-K1·E-K2). PREREGISTRATION_stage3.md 그대로.

입력: DataVault/staging/global_macro (derived/kr_industry, derived/us_industry, series/)
출력: results_stage3.md·json, DataVault/staging/global_macro/signals/registry_stage3.json
  .venv/bin/python -I docs/research/20261022_global_topdown/stage3_test.py
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../..")))
from shared.data_library.global_macro import catalog, kr_industry, store  # noqa: E402
from shared.data_library.global_macro import panel as gp  # noqa: E402
from stage2_test import mean_nw, nw_t, perf, verdict as verdict_x  # noqa: E402

H = 6
DEV_END = pd.Timestamp("2009-06-30")
CONF = (pd.Timestamp("2010-01-31"), pd.Timestamp("2026-02-28"))
KD = store.path("derived", "kr_industry")
UD = store.path("derived", "us_industry")
CLOSE = pd.read_parquet(os.path.join(KD, "sector_close.parquet"))
PROD = pd.read_parquet(os.path.join(KD, "prod.parquet"))
US_RET = pd.read_parquet(os.path.join(UD, "ret_vw.parquet"))
US_IP = pd.read_parquet(os.path.join(UD, "fund_ip.parquet"))
SECT = list(kr_industry.SECTORS)
NAME = {c: v[0] for c, v in kr_industry.SECTORS.items()}
MONTHS = CLOSE.index
RET = CLOSE[SECT].pct_change(fill_method=None)
KOSPI = CLOSE[kr_industry.MARKET]


def pit_df(df: pd.DataFrame, lag: int) -> pd.DataFrame:
    return pd.DataFrame({c: gp.point_in_time(df[c].dropna(), lag, MONTHS)[0] for c in df.columns})


def pit_key(key: str) -> pd.Series:
    s = gp.load(key)
    return gp.point_in_time(s.dropna(), catalog.BY_KEY[key]["lag_days"], MONTHS)[0]


def fwd_close(close: pd.DataFrame, h: int) -> pd.DataFrame:
    return np.log(close.shift(-h) / close)


def accel(df: pd.DataFrame) -> pd.DataFrame:
    y = np.log(df / df.shift(12)).rolling(3).mean()
    return y - y.shift(3)


# ---------------------------------------------------------------- 신호
def signals_cs():
    lr = np.log1p(RET)
    k1 = lr.shift(1).rolling(11, min_periods=11).sum()
    kprod = pit_df(accel(PROD), kr_industry.KOSIS_LAG_DAYS)
    k2 = pd.DataFrame({c: kprod[v[3]] for c, v in kr_industry.SECTORS.items() if v[3] and v[3] in kprod}, index=MONTHS)
    # 미국 월수익은 t−1 월까지: 미국 B1 창(t−11..t−1) — 미국 월말 t 는 한국 월말 t 뒤라 쓰지 않음
    us_lr = np.log1p(US_RET).shift(1).rolling(11, min_periods=11).sum()
    us_mom = us_lr.reindex(MONTHS)
    x1 = pd.DataFrame({c: us_mom[v[1]] for c, v in kr_industry.SECTORS.items() if v[1] in us_mom}, index=MONTHS)
    uip = pit_df(accel(US_IP), catalog.BY_KEY["us_indpro"]["lag_days"])
    x2 = pd.DataFrame({c: uip[v[2]] for c, v in kr_industry.SECTORS.items() if v[2] and v[2] in uip}, index=MONTHS)
    return {"K1": (k1, +1, "한국 업종 모멘텀(12−1개월)", "K"), "K2": (k2, +1, "한국 업종 생산 가속(KOSIS)", "K"),
            "X1": (x1, +1, "미국 같은 업종 모멘텀 → 한국 업종", "X"), "X2": (x2, +1, "미국 같은 업종 생산 가속 → 한국 업종", "X")}


def signals_ts():
    k3 = np.log(KOSPI / KOSPI.shift(12))
    g7, cn, kr = pit_key("w_cli_g7"), pit_key("cn_cli"), pit_key("kr_cli")
    return {"K3": (k3, +1, "코스피 12개월 수익(추세)", "K"), "X3": (g7 - g7.shift(6), +1, "G7 선행지수 6개월 변화", "X"),
            "X4": (cn - cn.shift(6), +1, "중국 선행지수 6개월 변화", "X"), "X5": (kr - kr.shift(6), +1, "한국 선행지수 순환변동치 6개월 변화", "X"),
            "X6": (pit_key("us_cape"), -1, "미국 CAPE → 코스피", "X")}


def market_excess(h: int) -> pd.Series:
    call = gp.load("kr_call")
    call.index = call.index.to_period("M").to_timestamp(how="end").normalize()
    rm = (call.reindex(MONTHS) / 100 / 12)
    rf = np.log1p(rm)[::-1].rolling(h, min_periods=h).sum()[::-1].shift(-1)
    return fwd_close(KOSPI.to_frame("k"), h)["k"] - rf


# ---------------------------------------------------------------- 기간
def mask(idx, which, h=H):
    if which == "full":
        return idx <= CONF[1] + pd.DateOffset(months=H - h)
    if which == "dev":
        return idx <= DEV_END - pd.DateOffset(months=h - H)
    return (idx >= CONF[0]) & (idx <= CONF[1] + pd.DateOffset(months=H - h))


def verdict_k(sign, full, t, h1, h2):
    if any(v is None or np.isnan(v) for v in (full, t)):
        return "판정 불가"
    if sign * full <= 0:
        return "버림"
    if sign * t >= 2 and (h1 is not None and sign * h1 > 0) and (h2 is not None and sign * h2 > 0):
        return "실전 후보"
    return "작게 실험"


# ---------------------------------------------------------------- 횡단면
def cross_section(sig: pd.DataFrame, h: int):
    fl = fwd_close(CLOSE[SECT], h)
    ics, spr, hit = {}, {}, {}
    for t in sig.index:
        s, f = sig.loc[t], fl.loc[t, sig.columns]
        ok = s.notna() & f.notna()
        if ok.sum() < 8:
            continue
        a, b = s[ok], f[ok]
        ics[t] = a.rank().corr(b.rank())
        n = len(a) // 3
        o = a.sort_values().index
        top, bot = np.expm1(b[o[-n:]]).mean(), np.expm1(b[o[:n]]).mean()
        spr[t], hit[t] = top - bot, float(top > bot)
    return pd.Series(ics, dtype=float), pd.Series(spr, dtype=float), pd.Series(hit, dtype=float)


def test_cs(S):
    rows = []
    for k, (sig, sign, name, kind) in S.items():
        rec = {"id": k, "family": "한국 업종 순위" if kind == "K" else "미국→한국 업종 전이", "kind": kind, "name": name, "sign": sign,
               "n_sect": int(sig.notna().sum(axis=1).max())}
        for h in (3, 6, 12):
            ic, sp, hit = cross_section(sig, h)
            for per in ("full", "dev", "conf"):
                m = mask(ic.index, per, h)
                icm, t, n = mean_nw(ic[m], h)
                rec[f"{per}_ic{h}"], rec[f"{per}_t{h}"], rec[f"{per}_n{h}"] = icm, t, n
                rec[f"{per}_spread{h}"] = float(sp[m].mean()) if m.any() else np.nan
                rec[f"{per}_hit{h}"] = float(hit[m].mean()) if m.any() else np.nan
        rec["start"] = str(sig.dropna(how="all").index.min().date())
        if kind == "K":
            rec["verdict"] = verdict_k(sign, rec["full_ic6"], rec["full_t6"], rec["dev_ic6"], rec["conf_ic6"])
        else:
            rec["verdict"] = verdict_x(sign, rec["dev_ic6"], rec["dev_t6"], rec["conf_ic6"])
        rows.append(rec)
    # X1 보조: 한국 자체 모멘텀 통제(월별 횡단 회귀 → 기울기 평균)
    k1, x1 = S["K1"][0], S["X1"][0]
    fl = fwd_close(CLOSE[SECT], H)
    sl = {}
    for t in MONTHS:
        d = pd.DataFrame({"y": fl.loc[t], "a": x1.loc[t].reindex(SECT), "b": k1.loc[t]}).dropna()
        if len(d) < 8:
            continue
        z = d.apply(lambda c: (c - c.mean()) / c.std())
        X = np.column_stack([np.ones(len(z)), z.a, z.b])
        sl[t] = np.linalg.lstsq(X, d.y.values, rcond=None)[0][1]
    sl = pd.Series(sl)
    for per in ("full", "dev", "conf"):
        m, t, n = mean_nw(sl[mask(sl.index, per)], H)
        next(r for r in rows if r["id"] == "X1")[f"{per}_ctrl_slope6"] = (m, t)
    return rows


# ---------------------------------------------------------------- 시계열
def test_ts(T):
    rows = []
    ex = {h: market_excess(h) for h in (3, 6, 12)}
    for k, (sig, sign, name, kind) in T.items():
        rec = {"id": k, "family": "한국 시장 방향" if kind == "K" else "세계→코스피 전이", "kind": kind, "name": name, "sign": sign}
        base = sig[mask(sig.index, "full" if kind == "K" else "dev") & ex[H].notna()].dropna()
        mu, sd, med = base.mean(), base.std(), base.median()
        rec["std_mean"], rec["std_sd"], rec["std_median"] = float(mu), float(sd), float(med)
        for h in (3, 6, 12):
            for per in ("full", "dev", "conf"):
                d = pd.DataFrame({"x": (sig - mu) / sd, "y": ex[h], "raw": sig}).loc[mask(sig.index, per, h)].dropna()
                if len(d) < 24:
                    continue
                b, se = nw_t(d.y.values, d.x.values, h)
                rec[f"{per}_slope{h}"], rec[f"{per}_t{h}"], rec[f"{per}_n{h}"] = float(b[1]), float(b[1] / se[1]), len(d)
                fav = sign * (d.raw - med) > 0
                rec[f"{per}_pup_fav{h}"], rec[f"{per}_pup_unfav{h}"] = float((d.y[fav] > 0).mean()), float((d.y[~fav] > 0).mean())
                rec[f"{per}_n_fav{h}"], rec[f"{per}_n_unfav{h}"] = int(fav.sum()), int((~fav).sum())
                rec[f"{per}_pup_all{h}"] = float((d.y > 0).mean())
                rec[f"{per}_ret_fav{h}"], rec[f"{per}_ret_unfav{h}"] = float(np.expm1(d.y[fav]).mean()), float(np.expm1(d.y[~fav]).mean())
        rec["start"] = str(sig.dropna().index.min().date())
        if kind == "K":
            rec["verdict"] = verdict_k(sign, rec.get("full_slope6"), rec.get("full_t6"), rec.get("dev_slope6"), rec.get("conf_slope6"))
        else:
            rec["verdict"] = verdict_x(sign, rec.get("dev_slope6"), rec.get("dev_t6"), rec.get("conf_slope6"))
        rows.append(rec)
    return rows


# ---------------------------------------------------------------- 결합
def combo_cs(S, rows):
    pick = [r for r in rows if r["sign"] * (r.get("dev_ic6") or 0) > 0 and r["sign"] * (r.get("dev_t6") or 0) >= 1.5]
    out = {"picked": [r["id"] for r in pick]}
    if not pick:
        return out
    zs = []
    for r in pick:
        s = S[r["id"]][0].reindex(columns=SECT) * r["sign"]
        zs.append(s.sub(s.mean(axis=1), axis=0).div(s.std(axis=1), axis=0))
    score = sum(z.fillna(0) for z in zs) / sum(z.notna().astype(int) for z in zs).replace(0, np.nan)
    cohorts = {}
    for t in MONTHS:
        s = score.loc[t].dropna()
        s = s[CLOSE.loc[t, s.index].notna()]
        if len(s) < 9:
            continue
        cohorts[t] = list(s.sort_values().index[-(len(s) // 3):])
    port, ew, cost, prev = {}, {}, {}, None
    for i, m in enumerate(MONTHS[1:], 1):
        held = [cohorts.get(MONTHS[i - k]) for k in range(1, H + 1) if i - k >= 0]
        held = [c for c in held if c]
        if len(held) < H:
            continue
        w = pd.Series(0.0, index=SECT)
        for c in held:
            w[c] += 1.0 / (H * len(c))
        r = RET.loc[m].fillna(0)
        port[m] = float((w * r).sum())
        cost[m] = 0.002 * float((w - prev).abs().sum()) if prev is not None else 0.0
        prev = w
        ew[m] = float(RET.loc[m].mean())
    P = pd.DataFrame({"rule": pd.Series(port) - pd.Series(cost), "ew_sect": pd.Series(ew), "kospi": KOSPI.pct_change(fill_method=None)})
    for per, (a, b) in {"dev": (P.index.min(), pd.Timestamp("2009-12-31")), "conf": (pd.Timestamp("2010-01-31"), pd.Timestamp("2026-09-30"))}.items():
        Q = P.loc[a:b].dropna()
        if len(Q) > 24:
            out[per] = {c: perf(Q[c]) for c in Q.columns}
            out[per]["turnover_cost_yr"] = float(pd.Series(cost).loc[a:b].mean() * 12)
    last = max(cohorts) if cohorts else None
    out["now"] = {"date": str(last.date()), "sectors": [NAME[c] for c in cohorts[last][::-1]]} if last else None
    return out


def combo_ts(T, rows):
    pick = [r for r in rows if r["sign"] * (r.get("dev_slope6") or 0) > 0 and r["sign"] * (r.get("dev_t6") or 0) >= 1.5]
    out = {"picked": [r["id"] for r in pick]}
    if not pick:
        return out
    z = sum(((T[r["id"]][0] - r["std_mean"]) / r["std_sd"] * r["sign"]).fillna(0) for r in pick) / \
        sum(T[r["id"]][0].notna().astype(int) for r in pick).replace(0, np.nan)
    w = (1 + 0.5 * z).clip(0.25, 1.5).fillna(1.0)
    call = gp.load("kr_call")
    call.index = call.index.to_period("M").to_timestamp(how="end").normalize()
    rf = call.reindex(MONTHS) / 100 / 12
    rk = KOSPI.pct_change(fill_method=None)
    wl = w.shift(1)
    rule = wl * rk + (1 - wl) * rf - 0.001 * wl.diff().abs()
    for per, (a, b) in {"dev": (pd.Timestamp("1991-02-28"), pd.Timestamp("2009-12-31")), "conf": (pd.Timestamp("2010-01-31"), pd.Timestamp("2026-09-30"))}.items():
        mw = float(wl.loc[a:b].mean())
        Q = pd.DataFrame({"rule": rule, "hold": rk, "matched": mw * rk + (1 - mw) * rf}).loc[a:b].dropna()
        if len(Q) > 24:
            out[per] = {c: perf(Q[c]) for c in Q.columns}
            out[per]["avg_w"] = mw
    out["w_now"], out["z_now"] = float(w.dropna().iloc[-1]), float(z.dropna().iloc[-1])
    return out


def current_cs(S, rows):
    cur = {}
    for r in rows:
        s = S[r["id"]][0].dropna(how="all")
        t = s.index[-1]
        v = (s.loc[t] * r["sign"]).dropna().sort_values()
        cur[r["id"]] = {"date": str(t.date()), "top": [NAME[c] for c in v.index[-4:][::-1]], "bottom": [NAME[c] for c in v.index[:4]]}
    return cur


def current_ts(T, rows):
    cur = {}
    for r in rows:
        s = T[r["id"]][0].dropna()
        v = float(s.iloc[-1])
        cur[r["id"]] = {"date": str(s.index[-1].date()), "value": v, "z": (v - r["std_mean"]) / r["std_sd"],
                        "favorable": bool(r["sign"] * (v - r["std_median"]) > 0)}
    return cur


# ---------------------------------------------------------------- 보고·원장
def f(v, p=1, pct=False):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "–"
    return f"{v * 100:+.{p}f}" if pct else f"{v:+.{p}f}"


def combo_verdict(e, keys):
    c = e.get("conf")
    if not c:
        return "판정 불가", ""
    r = c["rule"]
    parts, ok, strong = [], True, True
    for k in keys:
        x = r["cagr"] - c[k]["cagr"]
        parts.append(f"{k} 대비 연 {x * 100:+.1f}%p, MDD {r['mdd'] * 100:.0f}% vs {c[k]['mdd'] * 100:.0f}%")
        ok &= x > 0 and r["mdd"] >= c[k]["mdd"]
        strong &= x >= 0.02
    return ("실전 후보" if ok and strong else "작게 실험" if ok else "버림"), " · ".join(parts)


def report(res):
    L = ["# 3단계 결과 — 한국 업종·코스피: 미국 규칙 이식(K) + 국가 간 전이(X)", "",
         f"사전등록 sha256 {res['prereg_sha256'][:12]}…, K = 한국 전 기간이 확인(반기 ~2009-06 / 2010~ 보고), X = 개발 ~2009-06 / 확인 2010-01~2026-02, 주 지평 6개월. 계산 {res['generated_at']}", "",
         "## 1. 업종 순위 (6개월)", "",
         "| # | 신호 | 업종 | 시작 | 전체 IC(t) | 전체 상위−하위 | 전체 적중 | ~2009 IC(t) | 2010~ IC(t) | 2010~ 상위−하위 | 2010~ 적중 | 판정 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in res["CS"]:
        L.append(f"| {r['id']} | {r['name']} | {r['n_sect']} | {r['start']} | {f(r.get('full_ic6'), 3)}({f(r.get('full_t6'))}) | {f(r.get('full_spread6'), 1, True)} | {f(r.get('full_hit6'), 0, True)} | "
                 f"{f(r.get('dev_ic6'), 3)}({f(r.get('dev_t6'))}) | {f(r.get('conf_ic6'), 3)}({f(r.get('conf_t6'))}) | {f(r.get('conf_spread6'), 1, True)} | {f(r.get('conf_hit6'), 0, True)} | {r['verdict']} |")
    x1 = next(r for r in res["CS"] if r["id"] == "X1")
    L += ["", f"X1 보조(한국 자체 모멘텀 통제 후 미국 업종 모멘텀 기울기, 6개월 %p/σ): 전체 {f(x1['full_ctrl_slope6'][0], 1, True)}(t {f(x1['full_ctrl_slope6'][1])}) · ~2009 {f(x1['dev_ctrl_slope6'][0], 1, True)}(t {f(x1['dev_ctrl_slope6'][1])}) · 2010~ {f(x1['conf_ctrl_slope6'][0], 1, True)}(t {f(x1['conf_ctrl_slope6'][1])})",
          "", "보조 IC(t) 3개월 / 12개월 (전체):", ""]
    for r in res["CS"]:
        L.append(f"- {r['id']}: {f(r.get('full_ic3'), 3)}({f(r.get('full_t3'))}) / {f(r.get('full_ic12'), 3)}({f(r.get('full_t12'))}); 2010~ {f(r.get('conf_ic3'), 3)}({f(r.get('conf_t3'))}) / {f(r.get('conf_ic12'), 3)}({f(r.get('conf_t12'))})")
    L += ["", "## 2. 코스피 방향 (6개월, 콜금리 초과)", "",
          "| # | 신호 | 시작 | 전체 기울기(%p/σ, t) | ~2009 | 2010~ | 2010~ 유리 상승확률(개월) | 2010~ 불리(개월) | 2010~ 전체 | 판정 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in res["TS"]:
        L.append(f"| {r['id']} | {r['name']} | {r['start']} | {f(r.get('full_slope6'), 1, True)}({f(r.get('full_t6'))}) | {f(r.get('dev_slope6'), 1, True)}({f(r.get('dev_t6'))}) | "
                 f"{f(r.get('conf_slope6'), 1, True)}({f(r.get('conf_t6'))}) | {f(r.get('conf_pup_fav6'), 0, True)}({r.get('conf_n_fav6')}) | {f(r.get('conf_pup_unfav6'), 0, True)}({r.get('conf_n_unfav6')}) | {f(r.get('conf_pup_all6'), 0, True)} | {r['verdict']} |")
    L += ["", "보조 12개월 기울기(t): " + " · ".join(f"{r['id']} 전체 {f(r.get('full_slope12'), 1, True)}({f(r.get('full_t12'))}) 2010~ {f(r.get('conf_slope12'), 1, True)}({f(r.get('conf_t12'))})" for r in res["TS"])]
    for nm, key, keys in (("E-K1 업종 상위 1/3", "E1", ["ew_sect", "kospi"]), ("E-K2 코스피 비중", "E2", ["hold", "matched"])):
        e = res[key]
        v, txt = combo_verdict(e, keys)
        L += ["", f"## {nm} — 선택: {', '.join(e.get('picked') or []) or '없음'} → **{v}**", "", txt]
        for per in ("dev", "conf"):
            if per in e:
                L.append(f"- {'~2009' if per == 'dev' else '2010~'}: " + " · ".join(f"{c} 연 {f(x['cagr'], 1, True)}% MDD {x['mdd'] * 100:.0f}%" for c, x in e[per].items() if isinstance(x, dict)))
    L += ["", "## 현재", ""]
    for k, v in res["cur_ts"].items():
        L.append(f"- {k}: {v['date']} 값 {v['value']:.3f}, z {v['z']:+.2f} → {'유리' if v['favorable'] else '불리'}")
    for k, v in res["cur_cs"].items():
        L.append(f"- {k}: {v['date']} 상위 {', '.join(v['top'])} / 하위 {', '.join(v['bottom'])}")
    if res["E1"].get("now"):
        L.append(f"- E-K1 현재 상위 업종: {', '.join(res['E1']['now']['sectors'])} ({res['E1']['now']['date']})")
    open(os.path.join(HERE, "results_stage3.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


def write_registry(res):
    sig = []
    for r in res["CS"]:
        per_a, per_b = ("dev", "conf")
        cur = res["cur_cs"].get(r["id"], {})
        sig.append({"id": r["id"], "family": r["family"], "name": r["name"], "verdict": r["verdict"], "horizon": "6개월",
                    "periods": ["~2009", "2010~"],
                    "dev": {"effect": r.get("dev_ic6"), "t": r.get("dev_t6"), "spread": r.get("dev_spread6"), "hit": r.get("dev_hit6")},
                    "conf": {"effect": r.get("conf_ic6"), "t": r.get("conf_t6"), "spread": r.get("conf_spread6"), "hit": r.get("conf_hit6")},
                    "current": {"date": cur.get("date"), "text": f"상위 {', '.join(cur.get('top', []))} / 하위 {', '.join(cur.get('bottom', []))}"}})
    for r in res["TS"]:
        cur = res["cur_ts"].get(r["id"], {})
        sig.append({"id": r["id"], "family": r["family"], "name": r["name"], "verdict": r["verdict"], "horizon": "6개월", "periods": ["~2009", "2010~"],
                    "dev": {"effect": r.get("dev_slope6"), "t": r.get("dev_t6"), "p_up_fav": r.get("dev_pup_fav6"), "p_up_unfav": r.get("dev_pup_unfav6"),
                            "n_fav": r.get("dev_n_fav6"), "n_unfav": r.get("dev_n_unfav6")},
                    "conf": {"effect": r.get("conf_slope6"), "t": r.get("conf_t6"), "p_up_fav": r.get("conf_pup_fav6"), "p_up_unfav": r.get("conf_pup_unfav6"),
                             "n_fav": r.get("conf_n_fav6"), "n_unfav": r.get("conf_n_unfav6")},
                    "current": {"date": cur.get("date"), "text": f"값 {cur.get('value', float('nan')):.2f}, z {cur.get('z', float('nan')):+.2f} → {'유리' if cur.get('favorable') else '불리'}"}})
    v1, t1 = combo_verdict(res["E1"], ["ew_sect", "kospi"])
    v2, t2 = combo_verdict(res["E2"], ["hold", "matched"])
    reg = {"stage": 3, "generated_at": res["generated_at"], "prereg_sha256": res["prereg_sha256"], "source": "docs/research/20261022_global_topdown/stage3_test.py",
           "periods": ["~2009", "2010~"], "signals": sig,
           "combos": [{"id": "E-K1", "name": f"한국 업종 상위 1/3({'·'.join(res['E1'].get('picked') or []) or '개발 구간 통과 신호 없음'})", "verdict": v1 if res['E1'].get('picked') else "구성 안 됨",
                       "text": t1 or "개발 반기(~2009)에서 예측 부호 & |t|≥1.5 인 업종 신호가 없어 결합을 만들지 않음",
                       "now": ("현재 " + ", ".join(res["E1"]["now"]["sectors"])) if res["E1"].get("now") else ""},
                      {"id": "E-K2", "name": f"코스피 비중 조절({'·'.join(res['E2'].get('picked') or [])})", "verdict": v2, "text": t2,
                       "now": f"현재 비중 {res['E2']['w_now']:.2f}" if "w_now" in res["E2"] else ""}]}
    fp = store.path("signals", "registry_stage3.json")
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    json.dump(reg, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=lambda o: None)
    print("신호 원장:", [(x["id"], x["verdict"]) for x in sig], [(c["id"], c["verdict"]) for c in reg["combos"]])


def main():
    S, T = signals_cs(), signals_ts()
    cs, ts = test_cs(S), test_ts(T)
    res = {"generated_at": datetime.now().isoformat(timespec="seconds"),
           "prereg_sha256": open(os.path.join(HERE, "PREREGISTRATION_stage3.sha256")).read().split()[0],
           "CS": cs, "TS": ts, "E1": combo_cs(S, cs), "E2": combo_ts(T, ts), "cur_cs": current_cs(S, cs), "cur_ts": current_ts(T, ts)}
    json.dump(res, open(os.path.join(HERE, "results_stage3.json"), "w"), ensure_ascii=False, indent=1, default=lambda o: None)
    report(res)
    write_registry(res)


if __name__ == "__main__":
    main()
