"""
일 단위 배분 검증 도구 (Allocation Lab) — 2026-10-10
==================================================
shared/allocation 의 정책을 실제 상장 상품의 수정주가 일봉으로 검증하고 보고서를 만든다.
주문 기능은 없다. 결과는 '이 정책을 실전에 써도 되는가'의 근거 자료다.

검증 항목:
  1. 기준선 비교: 1배 보유 / 2배 보유 / 규칙(1배 상한) / 규칙(2배 상한) — 세전·세후(일반계좌)
  2. 긴 구간(1배만): 2배 상품 상장 전까지 포함한 전체 기간
  3. 주변값 견고성: 파라미터 격자에서 성과가 고원인지(기본값만 좋은 '봉우리'면 과적합 의심)
  4. 연도별·3년 롤링 안정성
  5. 리밸런싱 밴드: 회전율 대 성과
  6. 체결 시점: 전날 결정(lag 1) vs 15:15 결정→당일 종가(실전 방식) vs 당일 종가 결정(낙관 한계), 1m 가 있는 구간
  7. 정수 주식·운용 금액 한도 근사

사용:
  python -m core.allocation_lab --signal <신호 종목> --instruments <1배>,<2배> --out <보고서 폴더>
종목 코드는 코드에 쓰지 않는다 — 인자로만 받는다.
"""

import argparse
import itertools
import json
import os
from dataclasses import asdict, replace

import numpy as np
import pandas as pd

from shared.allocation.backtest import BacktestConfig, run_backtest, summarize
from shared.allocation.policy import AllocationParams, target_exposure
from shared.allocation.portfolio import Sleeve, account_target_weights
from shared.allocation.realize import instruments_from_ledger, realize_series
from shared.data_library import paths as dpaths

GRID = {
    "vol_target": (0.15, 0.20, 0.25, 0.30, 0.35),
    "vol_window": (10, 20, 40, 60),
    "trend_window": (60, 90, 120, 160, 200),
}


def load_daily_close(symbol: str) -> pd.Series:
    path = dpaths.normalized_path("candles_1d", f"{symbol}_1d.parquet", fallback=False)
    if not os.path.exists(path):
        raise FileNotFoundError(f"{symbol} 일봉 정규화 파일이 없습니다: {path} — normalized_builder.build_normalized_krx_daily 실행 필요")
    df = pd.read_parquet(path, columns=["timestamp", "close"])
    return df.set_index(pd.to_datetime(df["timestamp"]))["close"].astype(float).rename(symbol)


def load_decision_prices(symbol: str, minutes_before_close: int = 15) -> pd.Series:
    """1m 에서 날짜별 '장 마감 N분 전' 가격 (실전 결정 시점). 특수 장 일정은 장 일정 원장을 따른다."""
    from shared.core.market_utils import krx_session
    path = dpaths.normalized_path("candles_1m", f"{symbol}_1m.parquet", fallback=False)
    m = pd.read_parquet(path, columns=["timestamp", "close"])
    s = m.set_index(pd.to_datetime(m["timestamp"]))["close"].astype(float)
    out = {}
    for day, grp in s.groupby(s.index.normalize()):
        close_t = krx_session(day).close
        cutoff = pd.Timestamp.combine(day.date(), close_t) - pd.Timedelta(minutes=minutes_before_close)
        hit = grp[grp.index <= cutoff]
        if len(hit):
            out[day] = float(hit.iloc[-1])
    return pd.Series(out, dtype=float).sort_index()


def adjustment_ratio(symbol: str) -> pd.Series:
    """날짜별 (수정 일봉 종가 / 1m 실제 마감 종가). 1m 가격을 수정주가 기준으로 옮길 때 곱한다.
    1m 은 실제 가격, 일봉은 분배금이 반영된 수정주가라 그대로 이어 붙이면 분배금만큼 가짜 수익률이 생긴다
    (2026-10-10 첫 실행에서 15:15 결정의 회전율이 두 배로 부풀었던 원인). 실전에서는 수정 기준일이 오늘이라 비율이 1 이다."""
    from CAiModelBaker.core.normalized_builder import session_close_prices
    path = dpaths.normalized_path("candles_1m", f"{symbol}_1m.parquet", fallback=False)
    m = pd.read_parquet(path, columns=["timestamp", "close"])
    actual = session_close_prices(m.set_index(pd.to_datetime(m["timestamp"]))["close"])
    return (load_daily_close(symbol) / actual).dropna()


# ── 실행 단위 ────────────────────────────────────────────────────────────

def _sleeve(signal, instruments, params):
    usable = tuple(i for i in instruments if i.leverage <= params.max_exposure + 1e-9) or instruments[:1]
    return Sleeve("main", signal, usable, 1.0, params)


def rule_run(signal, instruments, closes, prices, params, cfg, start):
    sl = _sleeve(signal, instruments, params)
    w = account_target_weights([sl], {signal: closes[signal]})
    return run_backtest(w.loc[start:], prices[list(w.columns)].loc[start:], cfg)


def hold_run(symbol, prices, cfg, start):
    px = prices[[symbol]].loc[start:]
    return run_backtest(pd.DataFrame({symbol: 1.0}, index=px.index), px, cfg)


def _fmt(x, pct=True):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x:+.1%}" if pct else f"{x:.2f}"


def _row(name, s):
    return (f"| {name} | {_fmt(s['cagr'])} | {s['vol']:.1%} | {_fmt(s['sharpe'], False)} | {s['mdd']:.1%} | "
            f"{_fmt(s['worst_year'])} | {s['turnover_per_year']:.1f} | {s['rebalances_per_year']:.0f} | "
            f"{s['cost_pct_per_year']:.2%} | {s['tax_pct_per_year']:.2%} |")


_HEAD = ("| 전략 | 연복리 | 변동성 | 샤프 | 최대낙폭 | 최악연도 | 회전율/년 | 매매일/년 | 비용/년 | 세금/년 |\n"
         "|---|---|---|---|---|---|---|---|---|---|")


def main(argv=None):
    ap = argparse.ArgumentParser(description="일 단위 배분 정책 검증")
    ap.add_argument("--signal", required=True, help="노출을 정하는 종가 종목 (예: 1배 지수 ETF)")
    ap.add_argument("--instruments", required=True, help="매매 상품들, 쉼표 구분 (원장 leverage/tax_class 필수)")
    ap.add_argument("--account", default="KIS_MAIN", help="계좌 원장 ID (세금 규칙용)")
    ap.add_argument("--capital", type=float, default=10_000_000)
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-grid", action="store_true")
    a = ap.parse_args(argv)

    from shared.config.account_registry import get_account
    account = get_account(a.account)
    acct_type = account["account_type"]
    signal = a.signal.strip()
    instruments = instruments_from_ledger([s.strip() for s in a.instruments.split(",") if s.strip()])
    one_x = instruments[0]
    top = instruments[-1]
    syms = sorted({signal, *[i.symbol for i in instruments]})
    closes = {s: load_daily_close(s) for s in syms}
    prices = pd.concat(closes, axis=1).sort_index()
    sig = closes[signal]

    base = AllocationParams()
    p1 = replace(base, max_exposure=one_x.leverage)
    p2 = replace(base, max_exposure=top.leverage)
    listing = max(closes[i.symbol].first_valid_index() for i in instruments)
    start_all = prices.index[prices.index > listing][0]                     # 모든 상품이 거래되는 첫 날 다음
    start_long = sig.index[base.trend_window + 1]
    cfg_tax = BacktestConfig(initial_capital=a.capital, account_type=acct_type)
    cfg_pre = replace(cfg_tax, account_type=None)
    os.makedirs(a.out, exist_ok=True)
    R = {"account": account["account_id"], "account_type": acct_type, "signal": signal,
         "instruments": [asdict(i) for i in instruments], "params_default": asdict(base)}
    md = [f"# 일 단위 배분 검증 보고서", "",
          f"- 신호: {signal} 수정주가 일봉 / 상품: {', '.join(f'{i.symbol}({i.leverage:g}배, {i.tax_class})' for i in instruments)}",
          f"- 계좌: {account['account_id']} ({acct_type}) — 세후는 이 계좌 유형의 과세 규칙",
          f"- 기본 파라미터(문헌 표준, 최적화 안 함): {asdict(base)}",
          f"- 체결: 전날 종가 정보 → 다음 날 종가 (lag 1), 비용은 원장 수수료·슬리피지·매도세, 현금 이자 0", ""]

    # 1) 기준선 비교 (모든 상품 상장 후)
    md += [f"## 1. 기준선 비교 ({start_all.date()} ~ {prices.index[-1].date()})", "", _HEAD]
    R["baseline"] = {}
    runs = {
        f"{one_x.symbol} 보유": lambda c: hold_run(one_x.symbol, prices, c, start_all),
        f"{top.symbol} 보유": lambda c: hold_run(top.symbol, prices, c, start_all),
        "규칙 1배 상한": lambda c: rule_run(signal, instruments, closes, prices, p1, c, start_all),
        f"규칙 {top.leverage:g}배 상한": lambda c: rule_run(signal, instruments, closes, prices, p2, c, start_all),
    }
    for name, fn in runs.items():
        for tag, c in (("세전", cfg_pre), ("세후", cfg_tax)):
            s = summarize(fn(c))
            R["baseline"][f"{name} {tag}"] = s
            md.append(_row(f"{name} ({tag})", s))
    md.append("")
    # 기존 연구(선물 10년, 2026-10-09)와 같은 구간으로 재현 — 선물 2배 환산 샤프 0.93(2017~)·1.23(2019-09~)
    R["reproduction"] = {}
    for st in ("2017-01-01", "2019-09-01"):
        s = summarize(rule_run(signal, instruments, closes, prices, p2, cfg_pre, start_all), start=st)
        R["reproduction"][st] = s
    md += ["기존 연구 재현(규칙 2배 세전, 상품 실데이터): " + ", ".join(
        f"{st}~ 샤프 {s['sharpe']:.2f}·최대낙폭 {s['mdd']:.1%}" for st, s in R["reproduction"].items())
        + " — 선물 기반 기존 연구: 2017~ 0.93/29%, 2019-09~ 1.23/22%", ""]

    # 2) 긴 구간 (1배만)
    md += [f"## 2. 긴 구간, 1배 상품만 ({start_long.date()} ~)", "", _HEAD]
    R["long"] = {}
    for name, res in ((f"{one_x.symbol} 보유", hold_run(one_x.symbol, prices, cfg_tax, start_long)),
                      ("규칙 1배 상한", rule_run(signal, (one_x,), closes, prices, p1, cfg_tax, start_long))):
        s = summarize(res)
        R["long"][name] = s
        md.append(_row(name, s))
    md.append("")

    # 3) 연도별·롤링
    md += ["## 3. 연도별 수익 (세후)", ""]
    yr = {}
    for name, fn in runs.items():
        eq = fn(cfg_tax).equity
        r = eq.pct_change().dropna()
        yr[name] = (1 + r).groupby(r.index.year).prod() - 1
        roll = r.rolling(756).apply(lambda x: x.mean() / x.std() * np.sqrt(252) if x.std() > 0 else np.nan, raw=True)
        R.setdefault("rolling3y_sharpe_min", {})[name] = float(roll.min())
    ydf = pd.DataFrame(yr)
    R["yearly"] = {str(k): v for k, v in ydf.round(4).to_dict(orient="index").items()}
    md.append("| 연도 | " + " | ".join(ydf.columns) + " |")
    md.append("|---|" + "---|" * len(ydf.columns))
    for y, row in ydf.iterrows():
        md.append(f"| {y} | " + " | ".join(_fmt(v) for v in row) + " |")
    md += ["", "3년 롤링 샤프 최저: " + ", ".join(f"{k} {v:.2f}" for k, v in R["rolling3y_sharpe_min"].items()), ""]

    # 4) 리밸런싱 밴드
    md += ["## 4. 리밸런싱 밴드 (세후)", "", _HEAD]
    R["band"] = {}
    for band in (0.0, 0.05, 0.10, 0.20):
        for name, pp in (("규칙 1배", p1), (f"규칙 {top.leverage:g}배", p2)):
            s = summarize(rule_run(signal, instruments, closes, prices, pp, replace(cfg_tax, band=band), start_all))
            R["band"][f"{name} band={band:.2f}"] = s
            md.append(_row(f"{name}, 밴드 {band:.0%}", s))
    md.append("")

    # 5) 정수 주식
    s_int = summarize(rule_run(signal, instruments, closes, prices, p2, replace(cfg_tax, integer_shares=True), start_all))
    R["integer_shares"] = s_int
    md += [f"## 5. 정수 주식·운용 금액 {a.capital:,.0f}원 (규칙 {top.leverage:g}배, 세후)", "", _HEAD,
           _row("정수 주식", s_int), ""]

    # 6) 체결 시점 (1m 가 있는 구간)
    try:
        dec = load_decision_prices(signal) * adjustment_ratio(signal)
        R["timing"] = timing_study(signal, instruments, closes, prices, dec, p2, cfg_tax)
        md += [f"## 6. 체결 시점 비교 ({R['timing']['start']} ~, 1m 결정가 가능 구간, 규칙 {top.leverage:g}배 세후)", "", _HEAD]
        for k in ("lag1_prev_close", "lag0_1515", "lag0_close_optimistic"):
            md.append(_row(k, R["timing"][k]))
        md += ["", "- lag1_prev_close: 전날 종가로 결정 → 다음 날 종가 (백테스트 기본, 보수적)",
               "- lag0_1515: 당일 15:15 가격으로 결정 → 당일 종가 동시호가 (실전 계획)",
               "- lag0_close_optimistic: 당일 종가로 결정 → 당일 종가 (불가능한 상한)", ""]
        full = {}
        for lag in (1, 0):
            full[f"lag{lag}"] = summarize(rule_run(signal, instruments, closes, prices, p2, replace(cfg_tax, lag=lag), start_all))
        R["timing"]["full_period"] = full
        md += [f"전체 구간({start_all.date()}~)에서 당일 체결(lag0, 15:15≈종가 근사) vs 다음 날 체결(lag1): "
               f"샤프 {full['lag0']['sharpe']:.2f} vs {full['lag1']['sharpe']:.2f}, "
               f"최대낙폭 {full['lag0']['mdd']:.1%} vs {full['lag1']['mdd']:.1%} — 짧은 구간의 차이가 일반적인지 확인용", ""]
    except FileNotFoundError as e:
        md += ["## 6. 체결 시점 비교", "", f"생략: {e}", ""]

    # 7) 주변값 견고성
    if not a.skip_grid:
        R["grid"] = {}
        studies = (("규칙 1배", (one_x,), one_x.leverage, start_all),
                   (f"규칙 {top.leverage:g}배", instruments, top.leverage, start_all),
                   ("규칙 1배 긴 구간", (one_x,), one_x.leverage, sig.index[max(GRID["trend_window"]) + 1]))
        for name, ins, mx, st in studies:
            g = grid_study(signal, ins, closes, prices, mx, cfg_tax, st)
            R["grid"][name] = g
            md += [f"## 7. 주변값 견고성 — {name} ({st.date()}~, 세후, {len(g['rows'])}개 조합)", "",
                   f"- 기본값 샤프 {g['center_sharpe']:.2f}, 격자 중앙값 {g['median_sharpe']:.2f}, "
                   f"하위 10% {g['p10_sharpe']:.2f}, 최고 {g['max_sharpe']:.2f}",
                   f"- 기본값 −0.2 이내인 조합 비율 {g['share_within_0p2']:.0%}, "
                   f"보유 샤프({g['hold_sharpe']:.2f})를 넘는 조합 비율 {g['share_beats_hold']:.0%}",
                   f"- 최대낙폭 격자 중앙값 {g['median_mdd']:.1%}, 최악 {g['max_mdd']:.1%} vs 보유 {g['hold_mdd']:.1%} "
                   f"(보유보다 낙폭이 작은 조합 {g['share_mdd_below_hold']:.0%}), 연복리 격자 중앙값 {g['median_cagr']:+.1%}",
                   "", "vol_window=20 에서 샤프 (행 vol_target, 열 trend_window):", ""]
            hm = pd.DataFrame([r for r in g["rows"] if r["vol_window"] == 20]).pivot(
                index="vol_target", columns="trend_window", values="sharpe")
            md.append("| vol_target \\ trend | " + " | ".join(str(c) for c in hm.columns) + " |")
            md.append("|---|" + "---|" * len(hm.columns))
            for vt, row in hm.iterrows():
                md.append(f"| {vt:.2f} | " + " | ".join(f"{v:.2f}" for v in row) + " |")
            md.append("")

    with open(os.path.join(a.out, "results.json"), "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=2, default=float)
    with open(os.path.join(a.out, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))
    return R


def timing_study(signal, instruments, closes, prices, decision_px, params, cfg):
    """결정 시점별 성과. 15:15 결정은 '전날까지 종가 + 당일 15:15 가격'으로 노출을 다시 계산한다(미래 종가 미사용)."""
    sig = closes[signal]
    days = decision_px.index.intersection(sig.index)
    days = days[days > sig.index[params.trend_window + params.vol_window]]
    sl = _sleeve(signal, instruments, params)
    hist_len = max(params.trend_window, params.vol_window) + 2
    exp_1515 = {}
    for d in days:
        hist = sig.loc[:d].iloc[-hist_len:].copy()
        hist.iloc[-1] = decision_px.loc[d]                  # 당일 종가 대신 15:15 가격
        exp_1515[d] = float(target_exposure(hist, params).iloc[-1])
    w_1515 = realize_series(pd.Series(exp_1515).sort_index(), sl.instruments)
    w_close = account_target_weights([sl], {signal: sig})
    start = days[0]
    px = prices[list(w_close.columns)].loc[start:]
    out = {
        "start": str(start.date()),
        "lag1_prev_close": summarize(run_backtest(w_close.loc[start:], px, replace(cfg, lag=1))),
        "lag0_1515": summarize(run_backtest(w_1515.reindex(columns=w_close.columns, fill_value=0.0), px, replace(cfg, lag=0))),
        "lag0_close_optimistic": summarize(run_backtest(w_close.loc[start:], px, replace(cfg, lag=0))),
    }
    return out


def grid_study(signal, instruments, closes, prices, max_exposure, cfg, start):
    rows = []
    for vt, vw, tw in itertools.product(GRID["vol_target"], GRID["vol_window"], GRID["trend_window"]):
        p = AllocationParams(vol_target=vt, vol_window=vw, trend_window=tw, max_exposure=max_exposure)
        s = summarize(rule_run(signal, instruments, closes, prices, p, cfg, start))
        rows.append({"vol_target": vt, "vol_window": vw, "trend_window": tw,
                     "sharpe": s["sharpe"], "cagr": s["cagr"], "mdd": s["mdd"]})
    df = pd.DataFrame(rows)
    base = AllocationParams()
    center = df[(df.vol_target == base.vol_target) & (df.vol_window == base.vol_window)
                & (df.trend_window == base.trend_window)]["sharpe"].iloc[0]
    hold_sym = [i.symbol for i in instruments if abs(i.leverage - max_exposure) < 1e-9]
    hold_s = summarize(hold_run(hold_sym[0], prices, cfg, start)) if hold_sym else None
    hold = hold_s["sharpe"] if hold_s else float("nan")
    hold_mdd = hold_s["mdd"] if hold_s else float("nan")
    return {
        "rows": rows,
        "center_sharpe": float(center),
        "median_sharpe": float(df.sharpe.median()),
        "p10_sharpe": float(df.sharpe.quantile(0.1)),
        "max_sharpe": float(df.sharpe.max()),
        "share_within_0p2": float((df.sharpe >= center - 0.2).mean()),
        "hold_sharpe": float(hold),
        "share_beats_hold": float((df.sharpe > hold).mean()),
        "median_mdd": float(df.mdd.median()),
        "max_mdd": float(df.mdd.max()),
        "hold_mdd": float(hold_mdd),
        "share_mdd_below_hold": float((df.mdd < hold_mdd).mean()),
        "median_cagr": float(df.cagr.median()),
    }


if __name__ == "__main__":
    main()
