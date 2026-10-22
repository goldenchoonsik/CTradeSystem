"""4단계(무역) — 수출 가중 상대국 선행지수(wCLI) → 코스피(T1g·T1v)·대만(T2g·T2v)·수출−내수 업종(T3). PREREGISTRATION_stage4_trade.md 그대로.

  .venv/bin/python -I docs/research/20261022_global_topdown/stage4_trade_test.py
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
from shared.data_library.global_macro import store, trade_signal as ts  # noqa: E402
from stage2_test import nw_t, verdict as verdict_x  # noqa: E402
from stage3_test import CLOSE, CONF, DEV_END, H, MONTHS, NAME, fwd_close, market_excess, mask, pit_key, verdict_k  # noqa: E402

TW = pd.read_parquet(store.path("derived", "trade", "twii.parquet"))["twii"]
EXPORT = ["U013", "U015", "U008", "U011", "U012"]       # 전기전자·운송장비·화학·금속·기계
DOMESTIC = ["U016", "U020", "U017", "U005", "U018"]     # 유통·통신·전기가스·음식료·건설


def basket_rel(h: int) -> pd.Series:
    f = fwd_close(CLOSE, h)
    return f[EXPORT].mean(axis=1, skipna=False) - f[DOMESTIC].mean(axis=1)   # 내수는 있는 업종 평균(통신·전기가스 2000~)


def tw_fwd(h: int) -> pd.Series:
    return np.log(TW.shift(-h) / TW)


def ts_test(sig: pd.Series, target: dict, sign: int, kind: str, std_mask) -> dict:
    """target = {h: Series}. kind K = 전 기간 확인(반기 보고), X = 개발/확인."""
    base = sig[mask(sig.index, std_mask) & target[H].reindex(sig.index).notna()].dropna()
    mu, sd, med = base.mean(), base.std(), base.median()
    rec = {"std_mean": float(mu), "std_sd": float(sd), "std_median": float(med), "start": str(sig.dropna().index.min().date())}
    for h in (3, 6, 12):
        y = target[h]
        for per in ("full", "dev", "conf"):
            idx = sig.index
            d = pd.DataFrame({"x": (sig - mu) / sd, "y": y.reindex(idx), "raw": sig}).loc[mask(idx, per, h)].dropna()
            if len(d) < 24:
                continue
            b, se = nw_t(d.y.values, d.x.values, h)
            rec[f"{per}_slope{h}"], rec[f"{per}_t{h}"], rec[f"{per}_n{h}"] = float(b[1]), float(b[1] / se[1]), len(d)
            fav = sign * (d.raw - med) > 0
            rec[f"{per}_pup_fav{h}"], rec[f"{per}_pup_unfav{h}"] = float((d.y[fav] > 0).mean()), float((d.y[~fav] > 0).mean())
            rec[f"{per}_n_fav{h}"], rec[f"{per}_n_unfav{h}"] = int(fav.sum()), int((~fav).sum())
            rec[f"{per}_pup_all{h}"] = float((d.y > 0).mean())
    if kind == "K":
        rec["verdict"] = verdict_k(sign, rec.get("full_slope6"), rec.get("full_t6"), rec.get("dev_slope6"), rec.get("conf_slope6"))
    else:
        rec["verdict"] = verdict_x(sign, rec.get("dev_slope6"), rec.get("dev_t6"), rec.get("conf_slope6"))
    return rec


def main():
    tw_months = TW.index
    kr_g = ts.weighted_cli(ts.weights_kr_gross(), MONTHS)["total"]
    kr_v = ts.weighted_cli(ts.weights_tiva("KOR", "FFD_DVA"), MONTHS)["total"]
    tw_g = ts.weighted_cli(ts.weights_tiva("TWN", "EXGR"), tw_months)["total"].loc["1998-01-31":]      # 사전등록: 대만 1998-01~
    tw_v = ts.weighted_cli(ts.weights_tiva("TWN", "FFD_DVA"), tw_months)["total"].loc["1998-01-31":]
    chn_kr = ts.cli_change(MONTHS)["CHN"]
    chn_tw = ts.cli_change(tw_months)["CHN"].loc["1998-01-31":]
    g7 = pit_key("w_cli_g7")
    g7_kr = g7 - g7.shift(6)
    from shared.data_library.global_macro import panel as gp, catalog
    g7s = gp.load("w_cli_g7")
    g7tw = gp.point_in_time(g7s.dropna(), catalog.BY_KEY["w_cli_g7"]["lag_days"], tw_months)[0]
    g7_tw = (g7tw - g7tw.shift(6)).loc["1998-01-31":]

    kos = {h: market_excess(h) for h in (3, 6, 12)}
    twt = {h: tw_fwd(h) for h in (3, 6, 12)}
    bas = {h: basket_rel(h) for h in (3, 6, 12)}
    dev_kr, full_tw = "dev", "full"          # 표준화 기간(신호 색인 기준으로 계산)

    tests = [
        ("T1g", "한국 총수출 가중 상대국 선행지수 → 코스피", kr_g, kos, "X", dev_kr),
        ("T1v", "한국 부가가치 가중 상대국 선행지수 → 코스피", kr_v, kos, "X", dev_kr),
        ("T2g", "대만 총수출 가중 상대국 선행지수 → 대만 가권", tw_g, twt, "K", full_tw),
        ("T2v", "대만 부가가치 가중 상대국 선행지수 → 대만 가권", tw_v, twt, "K", full_tw),
        ("T3", "한국 총수출 가중 선행지수 → 수출−내수 업종", kr_g, bas, "X", dev_kr),
    ]
    comps = [
        ("C-KR-CHN", "비교: 중국 선행지수 → 코스피", chn_kr, kos, "X", dev_kr),
        ("C-KR-G7", "비교: G7 선행지수 → 코스피", g7_kr, kos, "X", dev_kr),
        ("C-TW-CHN", "비교: 중국 선행지수 → 대만", chn_tw, twt, "K", full_tw),
        ("C-TW-G7", "비교: G7 선행지수 → 대만", g7_tw, twt, "K", full_tw),
        ("C-T3-CHN", "비교: 중국 선행지수 → 수출−내수 업종", chn_kr, bas, "X", dev_kr),
    ]
    res = {"generated_at": datetime.now().isoformat(timespec="seconds"),
           "prereg_sha256": open(os.path.join(HERE, "PREREGISTRATION_stage4_trade.sha256")).read().split()[0], "tests": [], "comparisons": []}
    for bucket, items in (("tests", tests), ("comparisons", comps)):
        for tid, name, sig, tgt, kind, std in items:
            r = ts_test(sig, tgt, +1, kind, std)
            r.update(id=tid, name=name, kind=kind)
            v = sig.dropna()
            r["now"] = {"date": str(v.index[-1].date()), "value": float(v.iloc[-1]), "z": float((v.iloc[-1] - r["std_mean"]) / r["std_sd"]),
                        "favorable": bool(v.iloc[-1] > r["std_median"])}
            res[bucket].append(r)
    json.dump(res, open(os.path.join(HERE, "results_stage4_trade.json"), "w"), ensure_ascii=False, indent=1, default=lambda o: None)
    report(res)
    write_registry(res)


def f(v, p=1, pct=False):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "–"
    return f"{v * 100:+.{p}f}" if pct else f"{v:+.{p}f}"


def report(res):
    L = ["# 4단계(무역) 결과 — 수출 가중 상대국 선행지수", "",
         "기록: 2026-10-22 trade_signal.cli_change 를 '입력 목록 6칸 전'에서 '달력 6개월 전'으로 고침(정의 그대로, 연속 월말 입력에선 값 동일). 대만은 사전등록대로 1998-01부터.", "", f"사전등록 sha256 {res['prereg_sha256'][:12]}…, 6개월 지평. 한국 T1·T3 = 개발 ~2009-06 / 확인 2010~ (3단계 결과를 본 뒤의 가설이라 독립 아님), 대만 T2 = 1998~ 전 기간 확인. 계산 {res['generated_at']}", "",
         "| # | 신호 → 대상 | 시작 | 전체(%p/σ, t) | ~2009 | 2010~ | 2010~ 유리 상승확률(개월) | 2010~ 불리(개월) | 전체 유리/불리 | 판정 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for bucket in ("tests", "comparisons"):
        if bucket == "comparisons":
            L.append("| | **비교(판정 참고)** | | | | | | | | |")
        for r in res[bucket]:
            L.append(f"| {r['id']} | {r['name']} | {r['start']} | {f(r.get('full_slope6'), 1, True)}({f(r.get('full_t6'))}) | {f(r.get('dev_slope6'), 1, True)}({f(r.get('dev_t6'))}) | "
                     f"{f(r.get('conf_slope6'), 1, True)}({f(r.get('conf_t6'))}) | {f(r.get('conf_pup_fav6'), 0, True)}({r.get('conf_n_fav6')}) | {f(r.get('conf_pup_unfav6'), 0, True)}({r.get('conf_n_unfav6')}) | "
                     f"{f(r.get('full_pup_fav6'), 0, True)}/{f(r.get('full_pup_unfav6'), 0, True)} | {r['verdict']} |")
    L += ["", "보조 12개월(전체, t): " + " · ".join(f"{r['id']} {f(r.get('full_slope12'), 1, True)}({f(r.get('full_t12'))})" for r in res["tests"] + res["comparisons"]), "", "## 현재", ""]
    for r in res["tests"] + res["comparisons"]:
        n = r["now"]
        L.append(f"- {r['id']}: {n['date']} 값 {n['value']:+.3f}, z {n['z']:+.2f} → {'유리' if n['favorable'] else '불리'}")
    open(os.path.join(HERE, "results_stage4_trade.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


def write_registry(res):
    sig = []
    for r in res["tests"]:
        fam = "무역 가중 경기 → 대만(확인)" if r["id"].startswith("T2") else "무역 가중 경기 → 한국"
        sig.append({"id": r["id"], "family": fam, "name": r["name"], "verdict": r["verdict"], "horizon": "6개월", "periods": ["~2009", "2010~"],
                    "dev": {"effect": r.get("dev_slope6"), "t": r.get("dev_t6"), "p_up_fav": r.get("dev_pup_fav6"), "p_up_unfav": r.get("dev_pup_unfav6"),
                            "n_fav": r.get("dev_n_fav6"), "n_unfav": r.get("dev_n_unfav6")},
                    "conf": {"effect": r.get("conf_slope6"), "t": r.get("conf_t6"), "p_up_fav": r.get("conf_pup_fav6"), "p_up_unfav": r.get("conf_pup_unfav6"),
                             "n_fav": r.get("conf_n_fav6"), "n_unfav": r.get("conf_n_unfav6")},
                    "current": {"date": r["now"]["date"], "text": f"값 {r['now']['value']:+.2f}, z {r['now']['z']:+.2f} → {'유리' if r['now']['favorable'] else '불리'}"}})
    reg = {"stage": 4, "generated_at": res["generated_at"], "prereg_sha256": res["prereg_sha256"], "source": "docs/research/20261022_global_topdown/stage4_trade_test.py",
           "periods": ["~2009", "2010~"], "signals": sig, "combos": []}
    fp = store.path("signals", "registry_stage4.json")
    json.dump(reg, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=lambda o: None)
    print("신호 원장:", [(x["id"], x["verdict"]) for x in sig])


if __name__ == "__main__":
    main()
