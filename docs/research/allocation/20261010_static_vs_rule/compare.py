"""
결정 실험 (2026-10-10): 지수 보유 vs 정적 배분 vs 규칙(변동성 목표 × 추세 필터)
=============================================================================
질문: 직접 만든 규칙 배분이, 증권사 앱에서 연 1회 클릭하면 되는 '정적 배분'보다 세후로 의미 있게 나은가?
나으면 최소 형태로 구축, 아니면 구축 중단.

판정 기준 (결과를 보기 전에 고정):
  같은 자산 4개(K200·미국S&P(H)·국고채10년·금(H))로
  R_EW(규칙, 슬리브 25%씩) 가 S_EW(정적 25%씩, 연 1회 리밸런싱) 대비
    (1) 세후 CAGR 차이 ≥ -1.0%p  그리고
    (2) [세후 샤프 +0.15 이상 이고 블록 부트스트랩에서 샤프 우위 확률 ≥ 90%]  또는  [MDD 가 1/3 이상 작음]
  을 만족해야 '구축할 가치 있음'.

데이터: 069500 = Creon 수정주가 일봉(DataVault). 나머지 = Yahoo chart API 수정종가(adjclose, 분배금 반영).
  069500 Yahoo vs Creon 교차검증: 일수익률 상관 0.995, 연 0.8%p 차이(Yahoo 쪽이 낮음) → Yahoo 분배금 반영이 약간 덜 될 수 있음.
  148070 2013-02-21 단일일 급등·복귀(+3.3%→-3.0%)는 잘못된 체결로 보고 전일값으로 보정.
비용: 편도 0.03%(수수료+슬리피지, 국내 ETF 보수적), ETF 라 매도세 0.
세금(일반계좌): 069500 매매차익 비과세, 나머지 국내상장 기타 ETF 매매차익 15.4%(평균단가, 손실 상계 없음).
  보유분은 마지막 날 전량 청산한 것으로 보고 이연된 세금까지 뗀다(보유 전략에 유리하게 세금을 빼먹지 않도록).
  분배금 과세(채권 ETF 연 ~2% × 15.4%)는 모델링 안 함 → 채권 비중이 큰 정적 배분이 약간 유리하게 나옴.
  ISA·연금 ≈ 세전 열로 근사.
체결: t 종가 정보 → t+1 종가 체결(lag 1). 현금 이자 0.

사용: .venv/bin/python -I docs/research/allocation/20261010_static_vs_rule/compare.py <yahoo json 폴더> <출력 폴더>
"""

import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
sys.path.insert(0, ROOT)
from shared.allocation.policy import AllocationParams, target_exposure  # noqa: E402  실전과 같은 정책 함수

K200, US, BOND, GOLD, NDQ = "069500", "143850", "148070", "132030", "133690"
ASSETS = [K200, US, BOND, GOLD]
NAMES = {K200: "K200", US: "미국S&P(H)", BOND: "국고채10년", GOLD: "금(H)", NDQ: "나스닥100"}
TAXED = {US, BOND, GOLD, NDQ}
ONE_WAY = 0.0003
GAIN_TAX = 0.154
EVAL_START = "2012-05-02"     # 가장 늦은 상장(148070, 2011-10-20) + 규칙 웜업 120일
RULE = AllocationParams(max_exposure=1.0)   # 1배 상한 (2배는 세후 근거 없음, 10-10 결론)
RULE_BAND = 0.05


# ── 데이터 ──────────────────────────────────────────────────────────────

def load_yahoo(folder, sym):
    j = json.load(open(os.path.join(folder, f"{sym}.KS.json")))["chart"]["result"][0]
    ts = (pd.to_datetime(j["timestamp"], unit="s").tz_localize("UTC")
          .tz_convert("Asia/Seoul").normalize().tz_localize(None))
    s = pd.Series(j["indicators"]["adjclose"][0]["adjclose"], index=ts, dtype=float).dropna()
    return s[~s.index.duplicated(keep="last")]


def load_prices(folder):
    c = pd.read_parquet(os.path.join(ROOT, "DataVault/normalized/candles_1d/069500_1d.parquet"),
                        columns=["timestamp", "close"])
    px = {K200: c.set_index(pd.to_datetime(c["timestamp"]))["close"].astype(float)}
    for s in (US, BOND, GOLD, NDQ):
        px[s] = load_yahoo(folder, s)
    bad = pd.Timestamp("2013-02-21")
    if bad in px[BOND].index:
        px[BOND].loc[bad] = px[BOND].shift(1).loc[bad]
    df = pd.DataFrame(px)
    df = df[df[K200].notna()]                     # KRX 거래일 = 069500 이 있는 날
    return df.ffill()


# ── 시뮬레이터 (일반계좌 세금·비용, 달력 또는 밴드 리밸런싱) ────────────────

def simulate(target, px, taxed, calendar=None, band=0.0, start=EVAL_START):
    """target: 결정일 기준 일별 목표 비중(합 ≤ 1). lag 1 로 체결.
    calendar: 리밸런싱 날짜 집합(정적 배분). None 이면 밴드 규칙(목표 0 인데 보유 중이면 무조건 매매)."""
    px = px.loc[start:, target.columns]
    tw = target.reindex(px.index.union(target.index)).ffill().shift(1).reindex(px.index).fillna(0.0)
    syms = list(target.columns)
    cash, sh, avg = 1.0, dict.fromkeys(syms, 0.0), dict.fromkeys(syms, 0.0)
    eq_pre, eq_post, taxes, turn = [], [], 0.0, 0.0
    cash_pre = 1.0  # 세전 장부: 같은 매매, 세금만 안 뗌
    for i, (t, p) in enumerate(px.iterrows()):
        tgt = tw.loc[t]
        val = {s: sh[s] * p[s] for s in syms}
        eq = cash + sum(val.values())
        w = {s: val[s] / eq for s in syms}
        if calendar is not None:
            do = i == 0 or t in calendar
        else:
            do = i == 0 or any((tgt[s] <= 1e-12 < w[s]) or abs(tgt[s] - w[s]) >= band - 1e-12 and abs(tgt[s] - w[s]) > 1e-9
                               for s in syms)
        if do:
            for s in syms:                                     # 매도
                d = tgt[s] * eq - val[s]
                if d < 0 and sh[s] > 0:
                    q = sh[s] if tgt[s] <= 1e-12 else min(-d / p[s], sh[s])
                    v = q * p[s]
                    tax = max(q * (p[s] - avg[s]), 0.0) * (GAIN_TAX if s in taxed else 0.0)
                    cash += v - v * ONE_WAY - tax
                    cash_pre += v - v * ONE_WAY
                    taxes += tax; turn += v / eq
                    sh[s] -= q
            for s in syms:                                     # 매수
                d = tgt[s] * eq - val[s]
                if d > 0:
                    v = min(d, max(cash, 0) / (1 + ONE_WAY))
                    if v <= 0:
                        continue
                    q = v / p[s]
                    avg[s] = (avg[s] * sh[s] + v) / (sh[s] + q)
                    sh[s] += q
                    cash -= v * (1 + ONE_WAY); cash_pre -= v * (1 + ONE_WAY)
                    turn += v / eq
        hold = sum(sh[s] * p[s] for s in syms)
        eq_post.append(cash + hold)
        eq_pre.append(cash_pre + hold)
    last = px.iloc[-1]                                          # 이연된 세금: 마지막 날 전량 청산 가정
    deferred = sum(max(sh[s] * (last[s] - avg[s]), 0.0) * GAIN_TAX for s in syms if s in taxed)
    post = pd.Series(eq_post, index=px.index)
    post.iloc[-1] -= deferred
    years = len(px) / 252
    return {"pre": pd.Series(eq_pre, index=px.index), "post": post,
            "tax_per_year": (taxes + deferred) / years, "turnover": turn / years}


def stats(eq):
    r = eq.pct_change().dropna()
    yrs = len(r) / 252
    return {"cagr": (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1, "vol": r.std() * np.sqrt(252),
            "sharpe": r.mean() / r.std() * np.sqrt(252), "mdd": (1 - eq / eq.cummax()).max(),
            "worst_year": ((1 + r).groupby(r.index.year).prod() - 1).min(),
            "longest_uw_years": _longest_underwater(eq) / 252}


def _longest_underwater(eq):
    peak, run, best = eq.cummax(), 0, 0
    for under in (eq < peak * 0.999):
        run = run + 1 if under else 0
        best = max(best, run)
    return best


def boot_sharpe_win(a, b, n=2000, block=60, seed=0):
    """정상 블록 부트스트랩: 샤프(a) > 샤프(b) 확률. 같은 날짜 블록을 함께 뽑는다."""
    ra, rb = a.pct_change().dropna().to_numpy(), b.pct_change().dropna().to_numpy()
    T, rng, wins, diffs = len(ra), np.random.default_rng(seed), 0, []
    for _ in range(n):
        idx = np.concatenate([np.arange(s, s + block) % T for s in rng.integers(0, T, T // block + 1)])[:T]
        x, y = ra[idx], rb[idx]
        d = x.mean() / x.std() - y.mean() / y.std()
        diffs.append(d * np.sqrt(252)); wins += d > 0
    return wins / n, np.percentile(diffs, [5, 50, 95])


def md_table(df):
    """tabulate 없이 마크다운 표"""
    cols = [df.index.name or ""] + [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for i, row in df.iterrows():
        lines.append("| " + " | ".join([str(i)] + [str(v) for v in row]) + " |")
    return "\n".join(lines)


# ── 전략 ────────────────────────────────────────────────────────────────

def static_target(px, weights):
    return pd.DataFrame({s: weights.get(s, 0.0) for s in weights}, index=px.index)


def rule_target(px, alloc):
    """alloc: {종목: 슬리브 배분}. 규칙 대상이면 배분 × 노출, 아니면 정적."""
    cols = {}
    for s, (a, ruled) in alloc.items():
        cols[s] = a * (target_exposure(px[s], RULE) if ruled else 1.0)
    return pd.DataFrame(cols, index=px.index)


def first_days_of_year(idx):
    s = pd.Series(idx, index=idx)
    return set(s.groupby(idx.year).first())


def main(yahoo_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    px = load_prices(yahoo_dir)
    cal = first_days_of_year(px.loc[EVAL_START:].index)
    q = 0.25
    strategies = {
        "H_K200   K200 보유": (static_target(px, {K200: 1.0}), dict(calendar=cal)),
        "H_US     미국S&P(H) 보유": (static_target(px, {US: 1.0}), dict(calendar=cal)),
        "S_EW     정적 4자산 25%씩 (연1회)": (static_target(px, {s: q for s in ASSETS}), dict(calendar=cal)),
        "S_6040   정적 K200 30·미국 30·채권 40 (연1회)": (static_target(px, {K200: .3, US: .3, BOND: .4}), dict(calendar=cal)),
        "R_K200   규칙 K200 단독": (rule_target(px, {K200: (1.0, True)}), dict(band=RULE_BAND)),
        "R_EW     규칙 4자산 슬리브 25%씩": (rule_target(px, {s: (q, True) for s in ASSETS}), dict(band=RULE_BAND)),
        "R_EQ     규칙 주식 2개 + 채권·금 정적": (rule_target(px, {K200: (q, True), US: (q, True), BOND: (q, False), GOLD: (q, False)}),
                                       dict(band=RULE_BAND)),
        # 민감도: 미국 슬리브를 환노출 나스닥으로 (사후적으로 가장 좋았던 자산 → 참고용)
        "S_EW_N   정적 4자산(미국=나스닥) (참고)": (static_target(px, {K200: q, NDQ: q, BOND: q, GOLD: q}), dict(calendar=cal)),
        "R_EW_N   규칙 4자산(미국=나스닥) (참고)": (rule_target(px, {K200: (q, True), NDQ: (q, True), BOND: (q, True), GOLD: (q, True)}),
                                        dict(band=RULE_BAND)),
    }
    res, rows = {}, []
    for name, (tgt, kw) in strategies.items():
        r = simulate(tgt, px, TAXED, **kw)
        res[name] = r
        pre, post = stats(r["pre"]), stats(r["post"])
        rows.append({"전략": name, "세전CAGR": pre["cagr"], "세후CAGR": post["cagr"], "변동성": post["vol"],
                     "세전샤프": pre["sharpe"], "세후샤프": post["sharpe"], "MDD": post["mdd"],
                     "최악연도": post["worst_year"], "최장 수면아래(년)": post["longest_uw_years"],
                     "세금%/년": r["tax_per_year"], "회전율/년": r["turnover"]})
    tab = pd.DataFrame(rows).set_index("전략")

    # 구간별
    periods = [("2012-05~2016-12 박스권", EVAL_START, "2016-12-31"), ("2017~2021", "2017-01-01", "2021-12-31"),
               ("2022~2026-10", "2022-01-01", None)]
    sub = {}
    for name, r in res.items():
        sub[name] = {}
        for lab, a, b in periods:
            st = stats(r["post"].loc[a:b])
            sub[name][lab] = f'{st["cagr"]:+.1%} / 샤프 {st["sharpe"]:.2f} / MDD {st["mdd"]:.0%}'
    yearly = pd.DataFrame({n.split()[0]: (lambda e: e.resample("YE").last().pct_change().fillna(e.resample("YE").last().iloc[0] / e.iloc[0] - 1))(r["post"])
                           for n, r in res.items()})

    # 판정
    R, S = res["R_EW     규칙 4자산 슬리브 25%씩"]["post"], res["S_EW     정적 4자산 25%씩 (연1회)"]["post"]
    sr, ss = stats(R), stats(S)
    p_win, ci = boot_sharpe_win(R, S)
    c1 = sr["cagr"] - ss["cagr"] >= -0.01
    c2a = (sr["sharpe"] - ss["sharpe"] >= 0.15) and p_win >= 0.90
    c2b = sr["mdd"] <= ss["mdd"] * (2 / 3)
    verdict = c1 and (c2a or c2b)
    Rq, Sq = res["R_EQ     규칙 주식 2개 + 채권·금 정적"]["post"], S
    p_win_eq, ci_eq = boot_sharpe_win(Rq, Sq, seed=1)
    corr = px.loc[EVAL_START:, ASSETS].pct_change().corr()
    # 같은 위험으로 맞춘 비교: 규칙은 평균 ~63% 만 투자(나머지 현금)라 변동성이 낮다. 정적 배분도 같은 변동성까지 현금을 섞는다.
    k = sr["vol"] / ss["vol"]
    Sk = simulate(static_target(px, {s: q * k for s in ASSETS}), px, TAXED, calendar=cal)["post"]
    sk = stats(Sk)
    p_win_k, ci_k = boot_sharpe_win(R, Sk, seed=2)

    fmt = {c: "{:+.1%}" for c in ["세전CAGR", "세후CAGR"]}
    fmt.update({"변동성": "{:.1%}", "MDD": "{:.1%}", "최악연도": "{:+.1%}", "세금%/년": "{:.2%}",
                "세전샤프": "{:.2f}", "세후샤프": "{:.2f}", "최장 수면아래(년)": "{:.1f}", "회전율/년": "{:.1f}"})
    t2 = tab.copy()
    for c, f in fmt.items():
        t2[c] = t2[c].map(f.format)
    md = [f"# 결정 실험: 지수 보유 vs 정적 배분 vs 규칙 (2026-10-10)\n",
          f"평가 구간 {px.loc[EVAL_START:].index[0].date()} ~ {px.index[-1].date()} "
          f"({len(px.loc[EVAL_START:]) / 252:.1f}년), 일반계좌 세후(마지막 날 청산 세금 포함), lag 1, 편도 0.03%.\n",
          "## 1. 전체 구간\n", md_table(t2), "\n",
          "## 2. 구간별 (세후 CAGR / 샤프 / MDD)\n", md_table(pd.DataFrame(sub).T), "\n",
          "## 3. 판정 (사전 고정 기준: R_EW vs S_EW)\n",
          f"- 세후 CAGR 차이: {sr['cagr'] - ss['cagr']:+.2%}p → 조건1(≥ -1%p) {'충족' if c1 else '불충족'}",
          f"- 세후 샤프 차이: {sr['sharpe'] - ss['sharpe']:+.2f}, 부트스트랩 우위 확률 {p_win:.0%} "
          f"(차이 5/50/95% = {ci[0]:+.2f}/{ci[1]:+.2f}/{ci[2]:+.2f}) → 조건2a {'충족' if c2a else '불충족'}",
          f"- MDD: 규칙 {sr['mdd']:.1%} vs 정적 {ss['mdd']:.1%} → 조건2b(1/3 이상 축소) {'충족' if c2b else '불충족'}",
          f"- **판정: {'구축할 가치 있음' if verdict else '구축할 가치 없음 (정적 배분으로 충분)'}**",
          f"- (참고) R_EQ vs S_EW 샤프 우위 확률 {p_win_eq:.0%} (차이 5/50/95% = {ci_eq[0]:+.2f}/{ci_eq[1]:+.2f}/{ci_eq[2]:+.2f})\n",
          "## 3-1. 같은 위험(변동성)으로 맞춘 비교\n",
          f"규칙 평균 투자 비중 {rule_target(px, {s: (q, True) for s in ASSETS}).loc[EVAL_START:].sum(axis=1).mean():.0%}. "
          f"정적 4자산을 {k:.0%} 만 투자(나머지 현금, 이자 0)하면 변동성이 같아진다.",
          f"- 정적×{k:.2f}: 세후 CAGR {sk['cagr']:+.1%}, 샤프 {sk['sharpe']:.2f}, MDD {sk['mdd']:.1%}, 최악연도 {sk['worst_year']:+.1%}",
          f"- 규칙 R_EW : 세후 CAGR {sr['cagr']:+.1%}, 샤프 {sr['sharpe']:.2f}, MDD {sr['mdd']:.1%}, 최악연도 {sr['worst_year']:+.1%}",
          f"- 규칙 샤프 우위 확률 {p_win_k:.0%} (차이 5/50/95% = {ci_k[0]:+.2f}/{ci_k[1]:+.2f}/{ci_k[2]:+.2f})",
          "- 해석: 규칙의 낙폭 축소는 대부분 '평균적으로 덜 투자한' 효과다. 같은 위험에서 남는 우위는 작고 통계적으로 구분되지 않는다.\n",
          "## 4. 자산 간 일수익률 상관\n", md_table(corr.rename(index=NAMES, columns=NAMES).round(2)), "\n",
          "## 5. 연도별 세후 수익률\n", md_table(yearly.set_axis(yearly.index.year).map(lambda v: f"{v:+.1%}")), "\n"]
    open(os.path.join(out_dir, "report.md"), "w").write("\n".join(md))
    tab.to_csv(os.path.join(out_dir, "summary.csv"))
    print("\n".join(md))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
