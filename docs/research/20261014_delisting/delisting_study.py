"""상장폐지 종목 통계·공통 특성 (2014~2026, KIND 상폐 목록 548).

  .venv/bin/python docs/research/20261014_delisting/delisting_study.py   → results_delisting.md

1) 사유 유형 분류 · 연도/시장별 건수
2) 부실 폐지(스팩 제외) vs 생존 종목 — 기준 시점(폐지 1년 전 / 생존은 연말)의 시총·주가·거래대금·수익률·변동성,
   직전 사업연도 재무(영업적자 연속·자본잠식·매출), 직전 3년 DART 공시(CB·BW·제3자 유상증자·최대주주 변경·횡령배임·불성실공시·거래정지·관리종목)
   → 비율·중앙값·배수(부실/생존)
3) 폐지 전 주가 경로 · 마지막 거래일→폐지일 공백(거래정지) · 정리매매 낙폭
"""
import os, re, sys, glob, json
import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
KR = os.path.join(ROOT, "DataVault", "staging", "kr_equity")
DER = os.path.join(KR, "derived")
OUT = os.path.dirname(os.path.abspath(__file__))

TYPES = [  # (유형, 정규식) — 위에서부터 첫 일치
    ("스팩", None),
    ("감사의견", r"감사의견|감사범위|의견거절|부적정"),
    ("자본잠식", r"자본전액잠식|자본잠식"),
    ("실질심사", r"계속성|투명성|건전성|상장적격성"),
    ("부도·파산", r"부도|파산|채권은행|회생"),
    ("공시·요건 미달", r"미제출|시가총액|주식분포|영업손실|매출액"),
    ("합병·자회사화", r"합병|완전자회사|해산"),
    ("자진(신청)", r"신청"),
]
DISTRESS = {"감사의견", "자본잠식", "실질심사", "부도·파산", "공시·요건 미달"}
EVENTS = {  # DART 공시 제목 → 사건
    "CB발행": r"전환사채권발행결정",
    "BW발행": r"신주인수권부사채권발행결정",
    "유상증자": r"유상증자결정",
    "최대주주변경": r"최대주주(등)?(의)?변경|최대주주변경을수반",
    "횡령배임": r"횡령|배임",
    "불성실공시": r"불성실공시법인지정(?!예고)",
    "거래정지": r"주권매매거래정지(?!해제|기간)",
    "관리종목": r"관리종목지정",
    "투자주의환기": r"투자주의환기",
    "소송": r"소송등의제기",
    "감자": r"감자결정",
}


def classify(row) -> str:
    if row.is_spac == "1":
        return "스팩"
    for name, pat in TYPES[1:]:
        if re.search(pat, row.delist_reason or ""):
            return name
    return "기타"


def load_events(corp2code: dict) -> tuple[pd.DataFrame, set]:
    """상장사 목록(list, stock_code) + 상폐사 보완(list_delisted, corp_code→종목코드). 반환 (사건표, 공시 자료가 있는 종목 집합)."""
    rows, covered = [], set()
    for sub in ("list", "list_delisted"):
        for fp in sorted(glob.glob(os.path.join(KR, "raw", "dart", sub, "*.json"))):
            j = json.load(open(fp, encoding="utf-8"))
            for r in (j.get("response") or {}).get("list") or []:
                code = r.get("stock_code") or corp2code.get(r["corp_code"], "")
                if len(code) != 6:
                    continue
                covered.add(code)
                nm = re.sub(r"\s+", "", r["report_nm"])
                if nm.startswith("[기재정정]") or "철회" in nm:
                    continue
                for ev, pat in EVENTS.items():
                    if re.search(pat, nm):
                        rows.append((code, ev, r["rcept_dt"], r["rcept_no"]))
    e = pd.DataFrame(rows, columns=["code", "event", "dt", "rcept_no"]).drop_duplicates(["code", "event", "rcept_no"])
    e["dt"] = pd.to_datetime(e.dt)
    return e, covered


def halt_start(p: pd.DataFrame) -> pd.Timestamp:
    """마지막 '거래량 0 연속 ≥5일' 구간의 시작일(네이버 상폐 봉은 정지 기간을 같은 가격·거래량 0 으로 채움). 없으면 마지막 거래일."""
    z = (p.volume.values == 0)
    best, i, n = None, 0, len(z)
    while i < n:
        if z[i]:
            j = i
            while j < n and z[j]:
                j += 1
            if j - i >= 5:
                best = i
            i = j
        else:
            i += 1
    return p.date.iloc[best] if best is not None else p.date.iloc[-1]


def fin_asof(f: pd.DataFrame, code: str, t: pd.Timestamp) -> dict:
    """t 이전 접수된 마지막 사업보고서(q=4) 기준 재무 + 연속 영업적자 연수."""
    x = f[(f.stock_code == code) & (f.q == 4) & (f.rcept_dt <= t)].sort_values("period_end")
    if x.empty:
        return {}
    last = x.iloc[-1]
    streak = 0
    for v in x.op_income_annual.values[::-1]:
        if np.isfinite(v) and v < 0:
            streak += 1
        else:
            break
    eq, cap = last.equity, last.capital_stock
    return dict(sales=last.sales_annual, op_loss=float(last.op_income_annual < 0), loss_streak=streak,
                net_loss=float(last.net_income_annual < 0),
                impair=(1 - eq / cap) if cap and cap > 0 else np.nan,        # 자본잠식률
                neg_equity=float(eq <= 0), debt_ratio=last.liabilities / eq if eq and eq > 0 else np.nan)


def mkt_asof(px: pd.DataFrame, ext: pd.DataFrame, code: str, t: pd.Timestamp) -> dict:
    p = px[px.code == code]
    p = p[(p.date <= t) & (p.date > t - pd.Timedelta(days=380))]
    if len(p) < 60:
        return {}
    last = p.iloc[-1]
    r = np.log(p.close).diff()
    yr = p[p.date > t - pd.Timedelta(days=365)]
    e = ext[(ext.code == code) & (ext.date <= t)].tail(1)
    value = p.value.where(p.value.notna(), p.close * p.volume)          # 네이버 상폐 봉은 거래대금 없음 → 종가×거래량
    return dict(price=last.close_unadj if np.isfinite(last.close_unadj) else last.close, value60=value.tail(60).mean(), ret12=yr.close.iloc[-1] / yr.close.iloc[0] - 1,
                vol12=r.tail(250).std() * np.sqrt(250), mcap=e.market_cap.iloc[0] if len(e) else np.nan,
                stale=(t - last.date).days)


def ev_counts(ev: pd.DataFrame, code: str, t: pd.Timestamp, covered: set) -> dict:
    if code not in covered:
        return {k: np.nan for k in EVENTS}
    x = ev[(ev.code == code) & (ev.dt <= t) & (ev.dt > t - pd.DateOffset(years=3))]
    c = x.event.value_counts()
    return {k: float(c.get(k, 0)) for k in EVENTS}


def build_samples(d, dist, u, px, ext, f, ev, covered) -> pd.DataFrame:
    """부실 기준 시점 = 마지막 거래정지 시작 1년 전(정지 중 시점은 가격 정보가 없음)."""
    samples = []
    for r in dist.itertuples():
        p = px[(px.code == r.code6) & (px.date <= r.ddate)]
        if p.empty:
            continue
        h = halt_start(p)
        t = h - pd.Timedelta(days=365)
        samples.append(dict(group="부실", code=r.code6, name=r.name, market=r.market, type=r.type, t=t, halt=h,
                            **mkt_asof(px, ext, r.code6, t), **fin_asof(f, r.code6, t), **ev_counts(ev, r.code6, t, covered)))
    # 생존 대조: 연말(2015~2024) 상장 + 이후 2년 내 부실 폐지 아님 + 스팩 아님, 해마다 무작위 150
    rng = np.random.default_rng(20261014)
    bad = set(d[d.type.isin(DISTRESS)].code6)
    live = u[(u.is_spac == "0")]
    for y in range(2015, 2025):
        t = pd.Timestamp(y, 12, 30)
        on = px[(px.date > t - pd.Timedelta(days=10)) & (px.date <= t)].code.unique()
        pool = [c for c in live.code.str[1:] if c in set(on) and c not in bad]
        for c in rng.choice(pool, size=min(150, len(pool)), replace=False):
            m = live[live.code == "A" + c].iloc[0]
            samples.append(dict(group="생존", code=c, name=m["name"], market=m.market, type="", t=t,
                                **mkt_asof(px, ext, c, t), **fin_asof(f, c, t), **ev_counts(ev, c, t, covered)))
    return pd.DataFrame(samples)


def main():
    u = pd.read_csv(os.path.join(KR, "universe.csv"), dtype=str)
    u = u[u.is_preferred == "0"]
    d = u[u.status == "delisted"].copy()
    d["type"] = d.apply(classify, axis=1)
    d["year"] = d.delist_date.str[:4].astype(int)
    d["ddate"] = pd.to_datetime(d.delist_date)
    d["code6"] = d.code.str[1:]

    L = ["# 상장폐지 종목 통계·공통 특성 (KIND 상폐 목록 2014-01 ~ 2026-10)\n"]
    tab = pd.crosstab(d.year, d.type, margins=True, margins_name="합계")
    L += ["## 1. 사유 유형별 건수\n", "부실 = " + "·".join(sorted(DISTRESS)) + ". 스팩은 합병 성공(소멸합병)·기한 내 미합병 모두 포함.\n",
          "```", tab.to_string(), "```\n"]
    mk = pd.crosstab(d.type, d.market)
    L += ["시장별:\n", "```", mk.to_string(), "```\n"]

    # ── 2. 부실 vs 생존 ──
    print("loading", flush=True)
    f = pd.read_parquet(os.path.join(DER, "fundamentals_q.parquet"),
                        columns=["stock_code", "corp_code", "q", "period_end", "rcept_dt", "equity", "capital_stock", "liabilities",
                                 "sales_annual", "op_income_annual", "net_income_annual"])
    px = pd.read_parquet(os.path.join(DER, "prices_1d.parquet"), columns=["code", "date", "close", "close_unadj", "value", "volume"])
    px["code"] = px.code.astype(str).str[1:]
    ext = pd.read_parquet(os.path.join(DER, "daily_ext.parquet"), columns=["code", "date", "market_cap"])
    ext["code"] = ext.code.astype(str).str[1:]
    corp2code = f[["corp_code"]].assign(code=f.stock_code).drop_duplicates("corp_code").set_index("corp_code").code.to_dict() if "corp_code" in f else {}
    ev, covered = load_events(corp2code)
    print("events", len(ev), flush=True)

    dist = d[d.type.isin(DISTRESS) & (d.ddate >= "2016-01-01")]
    cache = os.path.join(OUT, "samples.csv")
    if os.path.exists(cache) and "--fresh" not in sys.argv:          # 표본 계산은 오래 걸림 → 재사용
        S = pd.read_csv(cache, dtype={"code": str}, parse_dates=["t", "halt"])
    else:
        S = build_samples(d, dist, u, px, ext, f, ev, covered)
        S.to_csv(cache, index=False)

    feats = [("시총(억원) 중앙", lambda g: g.mcap.median() / 1e8),
             ("주가(원) 중앙", lambda g: g.price.median()),
             ("주가 1,000원 미만", lambda g: (g.price < 1000).mean()),
             ("60일 평균 거래대금(억) 중앙", lambda g: g.value60.median() / 1e8),
             ("12개월 수익률 중앙", lambda g: g.ret12.median()),
             ("연 변동성 중앙", lambda g: g.vol12.median()),
             ("직전 연도 영업적자", lambda g: g.op_loss.mean()),
             ("영업적자 3년 이상 연속", lambda g: (g.loss_streak >= 3).mean()),
             ("순손실", lambda g: g.net_loss.mean()),
             ("자본잠식(잠식률>0)", lambda g: (g.impair > 0).mean()),
             ("자본잠식 50% 이상", lambda g: (g.impair >= 0.5).mean()),
             ("매출 100억 미만", lambda g: (g.sales < 1e10).mean()),
             ("부채비율 중앙", lambda g: g.debt_ratio.median()),
             ("재무 자료 없음", lambda g: g.sales.isna().mean())]
    for k in EVENTS:
        feats.append((f"3년 내 {k} ≥1", lambda g, k=k: (g[k] >= 1).mean()))
    feats += [("3년 내 CB+BW 발행 횟수 평균", lambda g: (g.CB발행 + g.BW발행).mean()),
              ("3년 내 최대주주 변경 ≥2", lambda g: (g.최대주주변경 >= 2).mean())]
    pct = lambda s: s.startswith(("주가 1", "직전", "영업", "순손", "자본", "매출", "재무", "3년 내")) and "평균" not in s
    G = {k: S[S.group == k] for k in ("부실", "생존")}
    K = {k: S[(S.group == k) & (S.market == "KOSDAQ")] for k in ("부실", "생존")}
    L += ["## 2. 부실 폐지(2016~) vs 생존 종목 — 기준 시점 특성\n",
          f"부실 = 폐지 1년 전 시점 {len(G['부실'])}개(코스닥 {len(K['부실'])}), 생존 = 2015~2024 연말 무작위 {len(G['생존'])}개(코스닥 {len(K['생존'])}; 이후 부실 폐지 종목 제외). "
          "재무 = 기준 시점 이전 접수된 마지막 사업보고서, 공시 = 기준 시점 이전 3년. 배수 = 부실/생존.\n",
          "| 특성 | 부실 | 생존 | 배수 | 코스닥 부실 | 코스닥 생존 |", "|---|---|---|---|---|---|"]
    for name, fn in feats:
        a, b, ka, kb = fn(G["부실"]), fn(G["생존"]), fn(K["부실"]), fn(K["생존"])
        fmt = (lambda v: f"{v:.0%}") if pct(name) else ((lambda v: f"{v:+.0%}") if "수익률" in name or "변동성" in name else (lambda v: f"{v:,.1f}"))
        lift = a / b if b and np.isfinite(b) and b != 0 else np.nan
        L.append(f"| {name} | {fmt(a)} | {fmt(b)} | {lift:.1f}× | {fmt(ka)} | {fmt(kb)} |")
    by_t = S[S.group == "부실"].groupby("type").agg(n=("code", "size"), mcap=("mcap", lambda x: x.median() / 1e8),
                                                 op_loss=("op_loss", "mean"), streak3=("loss_streak", lambda x: (x >= 3).mean()),
                                                 cb=("CB발행", lambda x: (x >= 1).mean()), mx=("최대주주변경", lambda x: (x >= 1).mean()),
                                                 emb=("횡령배임", lambda x: (x >= 1).mean()))
    L += ["\n유형별(부실):\n", "```", by_t.round(2).to_string(), "```\n"]

    # 결합 위험 신호: 여러 특성 동시 보유
    def flags(g):
        return ((g.op_loss == 1).astype(int) + (g.loss_streak >= 3).astype(int) + (g.impair > 0).astype(int)
                + ((g.CB발행 + g.BW발행) >= 2).astype(int) + (g.최대주주변경 >= 1).astype(int)
                + (g.횡령배임 >= 1).astype(int) + (g.불성실공시 >= 1).astype(int))
    S["n_flags"] = flags(S)
    L += ["위험 신호 개수(영업적자·3년 연속 적자·자본잠식·CB/BW 2회 이상·최대주주 변경·횡령배임·불성실공시) 분포:\n",
          "```", pd.crosstab(S.n_flags.clip(upper=5), S.group, normalize="columns").round(3).to_string(), "```\n"]
    base = len(dist) / (len(u[u.is_spac == "0"]) * (2026 - 2016 + 0.8))        # 연간 부실 폐지율(대략)
    L.append(f"참고: 비스팩 종목의 연간 부실 폐지율 ≈ {base:.2%} (2016~2026-10, 대략). 위 표의 생존 표본은 연도당 150개라 실제 비율과 다름.\n")

    # ── 3. 폐지 전 경로 (정지 시작 기준) ──
    rows = []
    for r in d[d.type.isin(DISTRESS) | d.type.isin({"합병·자회사화", "자진(신청)"})].itertuples():
        p = px[(px.code == r.code6) & (px.date <= r.ddate)]
        if len(p) < 30:
            continue
        h = halt_start(p)
        pre = p[p.date < h] if (p.date < h).any() else p
        c0 = pre.close.iloc[-1]
        def back(days):
            q = pre[pre.date <= pre.date.iloc[-1] - pd.Timedelta(days=days)]
            return c0 / q.close.iloc[-1] - 1 if len(q) else np.nan
        after = p[p.date >= h]
        trade_after = after[after.volume > 0]
        halted = h < p.date.iloc[-1]
        rows.append(dict(type=r.type, ret6m=back(182), ret12m=back(365), ret24m=back(730),
                         halted=float(halted), halt_days=(trade_after.date.iloc[0] - h).days if halted and len(trade_after) else np.nan,
                         liq_ret=p.close.iloc[-1] / c0 - 1 if halted else np.nan,
                         final_vs_24m=p.close.iloc[-1] / pre[pre.date <= pre.date.iloc[-1] - pd.Timedelta(days=730)].close.iloc[-1] - 1
                         if (pre.date <= pre.date.iloc[-1] - pd.Timedelta(days=730)).any() else np.nan))
    P = pd.DataFrame(rows)
    agg = P.groupby("type").agg(n=("ret12m", "size"), ret6m=("ret6m", "median"), ret12m=("ret12m", "median"),
                                ret24m=("ret24m", "median"), halted=("halted", "mean"), halt_days=("halt_days", "median"),
                                liq_ret=("liq_ret", "median"), total_24m=("final_vs_24m", "median"))
    L += ["## 3. 폐지 직전 주가 경로 (중앙값)\n",
          "ret6m/12m/24m = 거래정지 시작 직전 종가 기준 과거 수익률(정지가 없으면 마지막 거래일). halted = 거래량 0 이 5일 이상 이어진 정지 구간이 있었던 비율, "
          "halt_days = 정지 시작 → 다음 거래(대개 정리매매) 일수, liq_ret = 정지 직전 종가 → 최종 종가(정리매매 낙폭), total_24m = 정지 2년 전 → 최종.\n",
          "```", agg.round(3).to_string(), "```\n"]
    open(os.path.join(OUT, "results_delisting.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
