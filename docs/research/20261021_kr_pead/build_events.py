"""한국판 PEAD 사건 표 — (회사, 회계분기)마다 영업이익 첫 공개일 E 와 그날 알려진 SUE (PREREGISTRATION.md 2절).

  .venv/bin/python -I docs/research/20261021_kr_pead/build_events.py   # → events.parquet, results_prelim_check.md

입력: staging/kr_equity/derived/fundamentals_q.parquet(정기보고서), prelim_candidates.csv + prelim_parsed.parquet(잠정 공시).
"""
import os

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DER = os.path.join(ROOT, "DataVault", "staging", "kr_equity", "derived")
OUT = os.path.dirname(os.path.abspath(__file__))
POW = np.arange(-6, 7)
MAX_LOGDIFF = np.log10(2.0)


def load_fin():
    f = pd.read_parquet(os.path.join(DER, "fundamentals_q.parquet"),
                        columns=["stock_code", "bsns_year", "q", "period_end", "rcept_dt", "fs_div", "assets", "op_income",
                                 "op_income_cum", "sales", "sales_cum"])
    f = f[f.period_end.dt.month == f.q * 3].copy()                                   # 12월 결산만
    has_sales = f.groupby("stock_code").sales.transform(lambda s: s.notna().any()) | \
        f.groupby("stock_code").sales_cum.transform(lambda s: s.notna().any())
    f = f[has_sales].copy()                                                           # 비금융
    f["fy"] = f.bsns_year.astype(int)
    f = f.sort_values(["stock_code", "fy", "q", "rcept_dt"]).drop_duplicates(["stock_code", "fy", "q"], keep="first")
    k = f.set_index(["stock_code", "fy", "q"])
    prev = k.reindex(pd.MultiIndex.from_arrays([f.stock_code, f.fy - 1, f.q]))
    f["op_q4"] = prev.op_income.values
    f["assets_q4"] = prev.assets.values
    pq = k.reindex(pd.MultiIndex.from_arrays([f.stock_code, f.fy, f.q - 1]))
    f["op_cum_prev"] = np.where(f.q == 1, 0.0, pq.op_income_cum.values)               # 직전 분기까지 누적(이미 공개)
    f["op_cum_prev_dt"] = pq.rcept_dt.values
    f["sales_q"] = f.sales
    return f


def pick_scale(doc, q_val, cum_val):
    """문서 값 doc 을 {분기 q_val, 누적 cum_val} × 10^k 중 가장 가까운 것에 맞춘다 → (종류, 배수, |log 차|)."""
    best = ("", np.nan, np.inf)
    if not np.isfinite(doc) or doc == 0:
        return best
    for kind, ref in (("q", q_val), ("cum", cum_val)):
        if not np.isfinite(ref) or ref == 0 or np.sign(ref) != np.sign(doc):
            continue
        d = np.abs(np.log10(np.abs(ref) / np.abs(doc)) - POW)
        i = int(np.argmin(d))
        if d[i] < best[2]:
            best = (kind, 10.0 ** POW[i], d[i])
    return best


def prelim_events(f):
    c = pd.read_csv(os.path.join(OUT, "prelim_candidates.csv"), dtype={"no": str, "corp": str, "stock": str, "dt": str})
    p = pd.read_parquet(os.path.join(OUT, "prelim_parsed.parquet"))
    d = c.merge(p[["no", "unit", "unit_note", "sales_raw", "op_raw", "op_yoy_raw"]], on="no", how="inner")
    d = d[d.op_raw.notna()].sort_values(["dt", "no"]).drop_duplicates(["corp", "fy", "fq"], keep="first")
    d["stock"] = d.stock.str.zfill(6)
    d["E"] = pd.to_datetime(d.dt)
    d = d.rename(columns={"stock": "stock_code", "fq": "q"})
    d["q"] = d.q.astype(int)
    d["fy"] = d.fy.astype(int)
    m = d.merge(f, on=["stock_code", "fy", "q"], how="inner")
    qend = pd.to_datetime(m.fy.astype(str) + "-" + (m.q * 3).astype(str).str.zfill(2) + "-01") + pd.offsets.MonthEnd(0)
    m = m[(m.E > qend) & (m.E <= m.rcept_dt)].copy()                                  # 분기 종료 뒤·정기보고서 이전
    u = m.unit_note.fillna(m.unit).fillna(1e6)
    rows = []
    for r, uu in zip(m.itertuples(), u):
        s_doc, o_doc = r.sales_raw * uu, r.op_raw * uu
        if np.isfinite(s_doc) and s_doc != 0 and np.isfinite(r.sales_q):
            kind, mult, ld = pick_scale(s_doc, r.sales_q, r.sales_cum)
            basis = "sales"
        else:
            kind, mult, ld = pick_scale(o_doc, r.op_income, r.op_income_cum)
            basis = "op"
        # 문서 기준(연결/별도)이 패널 재무와 다르면 차이는 문서 안 값끼리(당기 − 전년동기) — 누적형·전년동기 없음이면 잠정 버림
        same = ("연결" in r.nm) == (r.fs_div == "CFS")
        quarterly = kind == "q" or r.q == 1
        ok = kind != "" and ld < MAX_LOGDIFF and not (quarterly is False and not (r.op_cum_prev_dt < r.E))
        yoy_doc = r.op_yoy_raw * uu * mult if ok else np.nan
        if ok and not same and not (quarterly and np.isfinite(yoy_doc)):
            ok = False
        op_known, base = np.nan, np.nan
        if ok:
            v = o_doc * mult
            op_known = v if quarterly else v - r.op_cum_prev
            base = r.op_q4 if same else yoy_doc
        rows.append(dict(stock_code=r.stock_code, fy=r.fy, q=r.q, E_pre=r.E, no=r.no, kind=kind, mult=mult, logdiff=ld, basis=basis,
                         same_basis=same, op_pre=op_known, base_pre=base, yoy_doc=yoy_doc, unit_fixed=(mult != 1.0) if ok else np.nan))
    return pd.DataFrame(rows)


def main():
    f = load_fin()
    pe = prelim_events(f)
    good = pe[pe.op_pre.notna()]
    ev = f.merge(good[["stock_code", "fy", "q", "E_pre", "op_pre", "base_pre", "yoy_doc", "same_basis", "no", "kind", "unit_fixed"]],
                 on=["stock_code", "fy", "q"], how="left")
    ev["src"] = np.where(ev.op_pre.notna(), "prelim", "regular")
    ev["E"] = ev.E_pre.where(ev.op_pre.notna(), ev.rcept_dt)
    ev["op_q"] = ev.op_pre.where(ev.op_pre.notna(), ev.op_income)
    ev["op_base"] = ev.base_pre.where(ev.op_pre.notna(), ev.op_q4)
    ev["sue"] = (ev.op_q - ev.op_base) / ev.assets_q4
    ev = ev[ev.sue.notna() & (ev.assets_q4 > 0) & np.isfinite(ev.sue)].copy()
    ev["code"] = "A" + ev.stock_code
    # 잠정 vs 확정 일치(해석용)
    tol = 0.02 * ev.op_income.abs() + 1e-3 * ev.sales_q.abs().fillna(0)
    ev["pre_match"] = np.where((ev.src == "prelim") & (ev.same_basis == True), (ev.op_pre - ev.op_income).abs() <= tol, np.nan)  # noqa: E712
    tol4 = 0.02 * ev.op_q4.abs() + 1e-3 * ev.sales_q.abs().fillna(0)
    ev["yoy_parse_ok"] = np.where((ev.src == "prelim") & (ev.same_basis == True) & ev.yoy_doc.notna(),  # noqa: E712
                                  (ev.yoy_doc - ev.op_q4).abs() <= tol4, np.nan)
    cols = ["code", "stock_code", "fy", "q", "E", "src", "no", "kind", "same_basis", "unit_fixed", "op_q", "op_base", "op_income", "op_q4",
            "assets_q4", "sue", "pre_match", "yoy_parse_ok", "rcept_dt"]
    ev[cols].sort_values("E").to_parquet(os.path.join(OUT, "events.parquet"), index=False)
    pr = ev[ev.src == "prelim"]
    L = ["# 잠정 공시 판별 점검 (build_events.py)", "",
         f"- 사건(회사·분기) {len(ev):,} — 잠정 {len(pr):,} / 정기보고서 {int((ev.src == 'regular').sum()):,}",
         f"- 잠정 문서 중 영업이익 있음 → 회사·분기 {len(pe):,}, 종류·단위 판별 성공 {pe.op_pre.notna().sum():,} "
         f"(실패 {pe.op_pre.isna().sum():,} → 정기보고서로 대체)",
         f"- 판별 종류: {pe[pe.op_pre.notna()].kind.value_counts().to_dict()}, 기준: {pe[pe.op_pre.notna()].basis.value_counts().to_dict()}",
         f"- 단위 배수 보정(머리글 단위와 다름): {int(pe.unit_fixed.fillna(False).astype(bool).sum())}건",
         f"- 잠정 영업이익 = 확정치(2% 안, 같은 기준만): {pr.pre_match.astype(float).mean():.1%}",
         f"- 문서 기준이 패널과 다름(별도 잠정 vs 연결 패널 등) → 문서 안 전년동기 사용: {int((pr.same_basis == False).sum())}건; "  # noqa: E712
         f"전년동기 파싱 검산(같은 기준에서 패널 전년 값과 2% 안): {pr.yoy_parse_ok.astype(float).mean():.1%}",
         f"- 연도별 사건 수(E 연도): {ev.groupby(ev.E.dt.year).size().to_dict()}",
         f"- 잠정 비중(E 연도): {ev.groupby(ev.E.dt.year).src.apply(lambda s: round((s == 'prelim').mean(), 3)).to_dict()}"]
    with open(os.path.join(OUT, "results_prelim_check.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
