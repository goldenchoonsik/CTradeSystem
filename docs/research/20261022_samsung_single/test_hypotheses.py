"""C·E 단계 — 사전등록 H1~H10 검정과 비중 규칙 (PREREGISTRATION.md 그대로).

입력: DataVault/staging/samsung_cycle/derived/monthly.parquet
출력: results_C.md, results_C.csv, results_E.md (이 폴더)
  .venv/bin/python -I docs/research/20261022_samsung_single/test_hypotheses.py
"""
import os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../..")))
from shared.data_library.paths import data_vault_path  # noqa: E402

M = pd.read_parquet(data_vault_path("staging", "samsung_cycle", "derived", "monthly.parquet"))
DEV = ("2000-01", "2016-06")
CONF = ("2017-01", "2026-02")
SUBS = [("2000-01", "2005-12"), ("2006-01", "2011-12"), ("2012-01", "2016-06")]
# (가설, 신호 열, 예측 부호, 대상 접두사, 설명)
HYP = [
    ("H1", "H1", +1, "r", "D램 수출물가 전년비의 3개월 변화"),
    ("H1b", "H1b", +1, "r", "플래시 수출물가 전년비의 3개월 변화"),
    ("H2", "H2", +1, "r", "반도체 출하−재고 전년비(3개월 평균)"),
    ("H3", "H3", +1, "r", "메모리 수출 3개월 합 전년비의 3개월 변화"),
    ("H4", "H4", +1, "r", "마이크론 매출총이익률 전분기 대비 변화"),
    ("H5", "H5", +1, "r", "SOX(원화) 대비 6개월 상대 부진(되돌림)"),
    ("H6", "H6", -1, "r", "원/달러 1년 고점 대비 거리"),
    ("H7", "H7", -1, "r", "미 10년물 6개월 변화"),
    ("H7b", "H7b", -1, "r", "국고채 10년 6개월 변화"),
    ("H8", "H8", -1, "r", "PBR 수준 (2016~, 기술 통계)"),
    ("H8b", "H8b", +1, "r", "TTM PER 수준 (2017~, 기술 통계)"),
    ("H9", "H9", -1, "r", "외국인 보유율 60개월 백분위"),
    ("H10", "H10", +1, "rp", "우선주 할인율 z → 우선주−보통주"),
]


def nw_ols(y, X, lag):
    """OLS + Newey-West(Bartlett) 표준오차. X 는 상수 포함."""
    b = np.linalg.lstsq(X, y, rcond=None)[0]
    e = y - X @ b
    n = len(y)
    XtX_inv = np.linalg.inv(X.T @ X)
    S = (X * e[:, None]).T @ (X * e[:, None])
    for L in range(1, lag + 1):
        w = 1 - L / (lag + 1)
        G = (X[L:] * e[L:, None]).T @ (X[:-L] * e[:-L, None])
        S += w * (G + G.T)
    V = XtX_inv @ S @ XtX_inv
    return b, np.sqrt(np.diag(V)), n


def window(df, a, b):
    return df.loc[a:b]


def stats(sig, tgt, mu, sd, lag, q=None):
    d = pd.DataFrame({"x": sig, "y": tgt}).dropna()
    if len(d) < 24:
        return None
    z = (d.x - mu) / sd
    X = np.column_stack([np.ones(len(d)), z.values])
    b, se, n = nw_ols(d.y.values, X, lag)
    out = dict(n=n, slope=b[1], t=b[1] / se[1], ic=d.x.rank().corr(d.y.rank()))
    if q is not None:
        lo, hi = q
        out["top"] = d.y[d.x > hi].mean()
        out["bot"] = d.y[d.x <= lo].mean()
        out["spread"] = out["top"] - out["bot"]
    return out


def with_mom(sig, tgt, mom, mu, sd, lag):
    d = pd.DataFrame({"x": sig, "y": tgt, "m": mom}).dropna()
    if len(d) < 24:
        return np.nan, np.nan
    z = (d.x - mu) / sd
    mz = (d.m - d.m.mean()) / d.m.std()
    X = np.column_stack([np.ones(len(d)), z.values, mz.values])
    b, se, _ = nw_ols(d.y.values, X, lag)
    return b[1], b[1] / se[1]


def verdict(sign, dev, conf):
    if dev is None or conf is None:
        return "기술 통계(구간 없음)"
    ds, cs = sign * dev["slope"], sign * conf["slope"]
    if ds <= 0 or cs <= 0:
        return "버림"
    if dev["t"] * sign >= 2.0 and cs >= 0.5 * ds:
        return "실전 후보"
    return "작게 실험"


def main():
    rows = []
    for h, col, sign, pre, desc in HYP:
        dv = window(M, *DEV)
        base = dv[col].dropna()
        no_dev = len(base) < 24
        ref = M[col].dropna() if no_dev else base
        mu, sd = ref.mean(), ref.std()
        q = (ref.quantile(1 / 3), ref.quantile(2 / 3))
        rec = dict(h=h, desc=desc, sign=sign)
        for tag, (a, b) in [("dev", DEV), ("conf", CONF), ("all", ("2000-01", CONF[1]))]:
            w = window(M, a, b)
            for hz in (3, 6, 12):
                s = stats(w[col], w[f"{pre}{hz}"], mu, sd, hz, q if hz == 6 else None)
                if s is None:
                    continue
                for k, v in s.items():
                    if hz == 6 or k in ("slope", "t"):
                        rec[f"{tag}_{k}" if hz == 6 else f"{tag}_{k}{hz}"] = v
            if pre == "r":
                s = stats(w[col], w["x6"], mu, sd, 6)
                if s:
                    rec[f"{tag}_xslope"], rec[f"{tag}_xt"] = s["slope"], s["t"]
                rec[f"{tag}_mslope"], rec[f"{tag}_mt"] = with_mom(w[col], w["r6"], w["mom12_1"], mu, sd, 6)
        subs = []
        for a, b in SUBS:
            s = stats(window(M, a, b)[col], window(M, a, b)[f"{pre}6"], mu, sd, 6)
            subs.append(None if s is None else s["slope"])
        rec["dev_subs_agree"] = f"{sum(1 for v in subs if v is not None and v * sign > 0)}/{sum(1 for v in subs if v is not None)}"
        dev = stats(window(M, *DEV)[col], window(M, *DEV)[f"{pre}6"], mu, sd, 6) if not no_dev else None
        conf = stats(window(M, *CONF)[col], window(M, *CONF)[f"{pre}6"], mu, sd, 6) if not no_dev else None
        rec["verdict"] = verdict(sign, dev, conf)
        rec["mu"], rec["sd"] = mu, sd
        rows.append(rec)
    R = pd.DataFrame(rows)
    R.to_csv(os.path.join(HERE, "results_C.csv"), index=False)

    def f(v, p=2, pct=False):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return "–"
        return f"{v * 100:+.1f}" if pct else f"{v:+.{p}f}"

    L = ["# C 단계 결과 — 사전등록 H1~H10 (6개월 앞 로그수익)", "",
         f"개발 {DEV[0]}~{DEV[1]} / 확인 {CONF[0]}~{CONF[1]}. 기울기 = 신호 1표준편차당 6개월 수익(%p, 로그). t = Newey-West(6). "
         "3분위 차 = 신호 상위 1/3 − 하위 1/3 의 평균 6개월 수익(%p, 개발 구간 경계). 부호는 예측 부호 기준이 아니라 원래 부호.", "",
         "| # | 신호 | 예측 | 개발 n | 개발 기울기 | 개발 t | 개발 3분위 차 | 토막 일치 | 확인 n | 확인 기울기 | 확인 t | 확인 3분위 차 | 판정 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['h']} | {r['desc']} | {'+' if r['sign'] > 0 else '−'} | {int(r.get('dev_n', 0))} | {f(r.get('dev_slope'), pct=True)} | {f(r.get('dev_t'), 1)} | "
                 f"{f(r.get('dev_spread'), pct=True)} | {r['dev_subs_agree']} | {int(r.get('conf_n', 0))} | {f(r.get('conf_slope'), pct=True)} | {f(r.get('conf_t'), 1)} | "
                 f"{f(r.get('conf_spread'), pct=True)} | {r['verdict']} |")
    L += ["", "## 보조 — 지평 3·12개월, 코스피 대비 초과, 자체 모멘텀 통제 (기울기 %p / t)", "",
          "| # | 개발 3m | 개발 12m | 확인 3m | 확인 12m | 개발 초과6m | 확인 초과6m | 개발 모멘텀통제 | 확인 모멘텀통제 | 전체 IC |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        g = lambda k: f"{f(r.get(k + 'slope' if k.endswith('_') else k), pct=True)}"
        L.append(f"| {r['h']} | {f(r.get('dev_slope3'), pct=True)} / {f(r.get('dev_t3'), 1)} | {f(r.get('dev_slope12'), pct=True)} / {f(r.get('dev_t12'), 1)} | "
                 f"{f(r.get('conf_slope3'), pct=True)} / {f(r.get('conf_t3'), 1)} | {f(r.get('conf_slope12'), pct=True)} / {f(r.get('conf_t12'), 1)} | "
                 f"{f(r.get('dev_xslope'), pct=True)} / {f(r.get('dev_xt'), 1)} | {f(r.get('conf_xslope'), pct=True)} / {f(r.get('conf_xt'), 1)} | "
                 f"{f(r.get('dev_mslope'), pct=True)} / {f(r.get('dev_mt'), 1)} | {f(r.get('conf_mslope'), pct=True)} / {f(r.get('conf_mt'), 1)} | {f(r.get('all_ic'))} |")
    open(os.path.join(HERE, "results_C.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))
    rule_E(R)


def perf(ret):
    ret = ret.dropna()
    eq = (1 + ret).cumprod()
    yrs = len(ret) / 12
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    mdd = (eq / eq.cummax() - 1).min()
    vol = ret.std() * np.sqrt(12)
    return dict(cagr=cagr, mdd=mdd, vol=vol, sharpe=(ret.mean() * 12) / vol if vol else np.nan)


def rule_E(R):
    pick = R[(R.verdict != "기술 통계(구간 없음)") & (R.h != "H10")]
    pick = pick[(pick.sign * pick.dev_slope > 0) & (pick.sign * pick.dev_t >= 1.5)]
    L = ["# E 단계 — 비중 규칙 (개발 구간 선택 → 확인 구간 1회)", "",
         f"선택된 가설(개발 예측 부호 & t≥1.5): {', '.join(pick.h) if len(pick) else '없음'}", ""]
    zs = []
    for _, r in pick.iterrows():
        zs.append(r.sign * (M[r.h] - r.mu) / r.sd)
    zbar = pd.concat(zs, axis=1).mean(axis=1) if zs else pd.Series(0.0, index=M.index)
    w = (1 + 0.5 * zbar.clip(-np.inf, np.inf)).clip(0.25, 1.75).fillna(1.0)
    r1 = np.exp(M["r1"]) - 1                     # t→t+1 단순수익
    trend = (np.log(M.sam / M.sam.shift(12)) > 0).astype(float)
    out = []
    for tag, (a, b) in [("개발", ("2000-01", "2016-11")), ("확인", ("2017-01", "2026-07"))]:
        ww, rr, tt = w.loc[a:b], r1.loc[a:b], trend.loc[a:b]
        cost = 0.0025 * ww.diff().abs().fillna(0)
        rule = ww * rr - cost
        hold = rr
        match = ww.mean() * rr
        tr = tt * rr - 0.0025 * tt.diff().abs().fillna(0)
        for nm, s in [("비중 규칙", rule), ("상시 보유", hold), (f"노출 맞춤 보유(w={ww.mean():.2f})", match), ("12개월 추세 필터", tr)]:
            p = perf(s)
            out.append((tag, nm, p))
        out.append((tag, "규칙−노출맞춤 연 초과", dict(cagr=perf(rule)["cagr"] - perf(match)["cagr"], mdd=np.nan, vol=np.nan, sharpe=np.nan)))
    L += ["| 구간 | 전략 | 연수익 | MDD(월말) | 변동성 | 샤프(현금0) |", "|---|---|---|---|---|---|"]
    for tag, nm, p in out:
        fm = lambda v: "–" if v is None or np.isnan(v) else f"{v * 100:+.1f}%"
        L.append(f"| {tag} | {nm} | {fm(p['cagr'])} | {fm(p['mdd'])} | {fm(p['vol'])} | {'–' if np.isnan(p['sharpe']) else f'{p['sharpe']:.2f}'} |")
    conf = {nm: p for tag, nm, p in out if tag == "확인"}
    ex = [p for nm, p in conf.items() if nm.startswith("규칙−")][0]["cagr"]
    v = "작게 실험" if (ex > 0 and conf["비중 규칙"]["mdd"] > conf["상시 보유"]["mdd"]) else "버림"
    L += ["", f"사전 기준 판정(확인 구간): **{v}** (노출 맞춤 대비 연 {ex * 100:+.1f}%p, MDD 규칙 {conf['비중 규칙']['mdd'] * 100:.0f}% vs 보유 {conf['상시 보유']['mdd'] * 100:.0f}%)",
          "", f"현재(최근 월말) 합성 z = {zbar.dropna().iloc[-1]:+.2f}, 비중 w = {w.iloc[-1]:.2f}" if len(zbar.dropna()) else ""]
    open(os.path.join(HERE, "results_E.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))
    M.assign(w_rule=w, z_rule=zbar).loc["2000-01":, ["w_rule", "z_rule"]].to_csv(os.path.join(HERE, "results_E_weights.csv"))


if __name__ == "__main__":
    main()
