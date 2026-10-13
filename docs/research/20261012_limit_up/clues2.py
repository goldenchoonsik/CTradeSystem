"""덜 쓰이는 데이터(신용·공매도·대차·공시)로 '아직 안 오른' 상한가 맞히기 — PREREGISTRATION_altdata.md 그대로.

공표 시점(보수적):
  신용잔고: 결제일(stlm_date) < D 인 행만 → 그날 시가 전에 알려진 값
  공매도·대차: 하루 늦게 공표된다고 보고 D-2 값
  공시: 접수일이 D-1 이전(< D) 인 것만. 당일 개장 전 공시는 쓰지 않는다(불리한 쪽)

  .venv/bin/python -W ignore docs/research/20261012_limit_up/clues2.py
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "20261011_vol_spike"))
import analyze_limit_up as alu  # noqa: E402
from evaluate_events import tick_size  # noqa: E402

STAGE = alu.STAGE
ALT = os.path.join(STAGE, "raw", "kis", "alt")
DLIST = os.path.join(STAGE, "raw", "dart", "list")
TRAIN_END, VAL_FROM, VAL_END = pd.Timestamp("2025-09-30"), pd.Timestamp("2025-10-01"), pd.Timestamp("2026-05-29")
GAP_MAX = 0.03

# 공시 분류 — 결과 보기 전 고정 (보고서명 부분 일치, 위에서부터 첫 일치)
DISC_CATS = [
    ("공급계약", ["공급계약"]),
    ("최대주주변경", ["최대주주변경", "최대주주 변경"]),
    ("무상증자", ["무상증자결정"]),
    ("유상증자", ["유상증자결정"]),
    ("CB·BW·EB", ["전환사채권발행결정", "신주인수권부사채권발행결정", "교환사채권발행결정"]),
    ("자사주매입", ["자기주식취득"]),
    ("타법인취득", ["타법인주식및출자증권취득결정"]),
    ("특허", ["특허권취득"]),
    ("기술이전", ["기술이전", "기술도입"]),
    ("투자판단(임상 등)", ["투자판단관련주요경영사항"]),
    ("대량보유(5%)", ["주식등의대량보유상황보고서"]),
    ("임원·주요주주 소유", ["임원ㆍ주요주주특정증권등소유상황보고서", "임원·주요주주특정증권등소유상황보고서"]),
    ("실적", ["영업(잠정)실적", "매출액또는손익구조"]),
    ("조회공시", ["조회공시"]),
    ("시장경보", ["투자주의", "투자경고", "투자위험", "단기과열"]),
    ("합병·분할", ["합병", "분할"]),
    ("감자", ["감자결정"]),
    ("소송·횡령", ["소송", "횡령", "배임"]),
    ("상장위험", ["상장적격성", "관리종목", "매매거래정지", "불성실공시"]),
]


def categorize(name):
    for cat, keys in DISC_CATS:
        if any(k in name for k in keys):
            return cat
    return "기타"


def _num(x):
    return pd.to_numeric(x, errors="coerce")


def load_alt():
    rows = []
    for fp in glob.glob(os.path.join(ALT, "credit", "*", "*.json")):
        j = json.load(open(fp, encoding="utf-8"))
        for r in j["response"].get("output") or []:
            if r.get("deal_date"):
                rows.append(("A" + j["code"], r["deal_date"], r.get("stlm_date"), r.get("whol_loan_rmnd_stcn"), r.get("whol_loan_rmnd_rate"),
                             r.get("whol_loan_gvrt"), r.get("whol_loan_new_amt")))
    cr = pd.DataFrame(rows, columns=["code", "deal", "stlm", "cr_rmnd", "cr_rate", "cr_gvrt", "cr_new_amt"]).drop_duplicates(["code", "deal"])
    for c in ("cr_rmnd", "cr_rate", "cr_gvrt", "cr_new_amt"):
        cr[c] = _num(cr[c])
    cr["deal"], cr["stlm"] = pd.to_datetime(cr.deal, format="%Y%m%d"), pd.to_datetime(cr.stlm, format="%Y%m%d", errors="coerce")
    rows = []
    for fp in glob.glob(os.path.join(ALT, "short", "*", "*.json")):
        j = json.load(open(fp, encoding="utf-8"))
        for r in j["response"].get("output2") or []:
            if r.get("stck_bsop_date"):
                rows.append(("A" + j["code"], r["stck_bsop_date"], r.get("ssts_cntg_qty"), r.get("acml_vol")))
    sh = pd.DataFrame(rows, columns=["code", "date", "sh_qty", "vol"]).drop_duplicates(["code", "date"])
    rows = []
    for fp in glob.glob(os.path.join(ALT, "loan", "*", "*.json")):
        j = json.load(open(fp, encoding="utf-8"))
        for pg in j["pages"]:
            for r in pg.get("output1") or []:
                if r.get("bsop_date"):
                    rows.append(("A" + j["code"], r["bsop_date"], r.get("rmnd_stcn"), r.get("new_stcn")))
    ln = pd.DataFrame(rows, columns=["code", "date", "ln_rmnd", "ln_new"]).drop_duplicates(["code", "date"])
    for t in (sh, ln):
        t["date"] = pd.to_datetime(t.date, format="%Y%m%d")
        for c in t.columns[2:]:
            t[c] = _num(t[c])
    return cr, sh, ln


def load_disc():
    rows = []
    for fp in glob.glob(os.path.join(DLIST, "*.json")):
        for r in json.load(open(fp, encoding="utf-8"))["response"].get("list") or []:
            nm = (r.get("report_nm") or "").strip()
            if not r.get("stock_code") or nm.startswith("[기재정정]") or nm.startswith("[첨부"):
                continue                                                  # 정정·첨부는 새 정보가 아님
            rows.append(("A" + r["stock_code"].strip(), r["rcept_dt"], categorize(nm), r["rcept_no"]))
    d = pd.DataFrame(rows, columns=["code", "dt", "cat", "rcept_no"]).drop_duplicates("rcept_no")
    d["dt"] = pd.to_datetime(d.dt, format="%Y%m%d")
    return d


def disc_window(d, x):
    """행 (code, date, prev_date) 마다 접수일 ∈ [prev_date, date) 건수와 ∈ [date-7일, date) 건수. 당일(date) 접수는 넣지 않는다."""
    if x.empty:
        return np.zeros(len(d), "float32"), np.zeros(len(d), "float32")
    cnt = x.groupby(["dt", "code"]).size().unstack("code")
    full = cnt.reindex(pd.date_range(min(cnt.index.min(), d.date.min()) - pd.Timedelta(days=10), d.date.max())).fillna(0).cumsum()
    arr = full.to_numpy()
    ci = full.columns.get_indexer(d.code.to_numpy())                      # 종목 → 열 번호 (한 번에)

    def at(ts):
        i = full.index.get_indexer(pd.DatetimeIndex(ts))
        ok = (i >= 0) & (ci >= 0)
        v = np.zeros(len(i))
        v[ok] = arr[i[ok], ci[ok]]
        return v
    day1 = pd.Timedelta(days=1)
    end = at(d.date - day1)
    prev = d.prev_date.fillna(d.date - day1)
    return (end - at(prev - day1)).astype("float32"), (end - at(d.date - pd.Timedelta(days=8))).astype("float32")


def build():
    d, uni = alu.features()
    sh_now = pd.read_csv(os.path.join(STAGE, "shares_now.csv")).set_index("code").shares
    d = d.sort_values(["code", "date"]).reset_index(drop=True)
    g = d.groupby("code", sort=False)
    d["prev_date"] = g.date.shift(1)
    d["gap"] = (d.o * d.f) / g.ca.shift(1) - 1
    d["oc"] = d.c / d.o - 1
    d["cost"] = 0.0027 + np.maximum(0.003, 2 * tick_size(d.o.to_numpy()) / d.o.to_numpy())
    d["oc_lu"] = np.where(d.touch == 1, (1 + alu.LU) / (1 + d.gap) - 1, d.oc)      # 상한가 도달 시 상한가(+29.5%)에 매도
    d = d[(d.p_raw >= 1000) & (d.val20 >= 5) & d.r1.notna() & d.vol20.notna()].copy()
    for c in d.select_dtypes("float64").columns:
        d[c] = d[c].astype("float32")
    cr, sh, ln = load_alt()
    shares = d.code.map(sh_now)
    # F 신용: 결제일 < D 인 마지막 행
    cr = cr.dropna(subset=["stlm"]).sort_values("stlm")
    cr["avail"] = cr.stlm + pd.Timedelta(days=1)
    cr = cr.sort_values(["code", "deal"])
    gc_ = cr.groupby("code", sort=False)
    cr["cr_chg5"] = cr.cr_rmnd / gc_.cr_rmnd.shift(5) - 1
    cr["cr_chg20"] = cr.cr_rmnd / gc_.cr_rmnd.shift(20) - 1
    d = pd.merge_asof(d.sort_values("date"), cr.sort_values("avail")[["code", "avail", "cr_rmnd", "cr_rate", "cr_gvrt", "cr_new_amt", "cr_chg5", "cr_chg20"]],
                      left_on="date", right_on="avail", by="code", direction="backward")
    d.loc[(d.date - d.avail).dt.days > 10, ["cr_rmnd", "cr_rate", "cr_gvrt", "cr_new_amt", "cr_chg5", "cr_chg20"]] = np.nan
    d["cr_float"] = d.cr_rmnd / shares
    # G 공매도·대차: D-2
    sh = sh.sort_values(["code", "date"])
    gs = sh.groupby("code", sort=False)
    sh["sh_ratio"] = sh.sh_qty / sh.vol.replace(0, np.nan)
    sh["sh_ratio5"] = gs.sh_ratio.transform(lambda s: s.rolling(5, min_periods=3).mean())
    ln = ln.sort_values(["code", "date"])
    ln["ln_chg20"] = ln.ln_rmnd / ln.groupby("code", sort=False).ln_rmnd.shift(20) - 1
    g2 = sh.merge(ln, on=["code", "date"], how="outer").sort_values(["code", "date"])
    g2["avail_date"] = g2.groupby("code", sort=False).date.shift(-2)               # 이틀 뒤 시가 전부터 사용
    d = d.merge(g2[["code", "avail_date", "sh_ratio", "sh_ratio5", "ln_rmnd", "ln_chg20"]].rename(columns={"avail_date": "date"}), on=["code", "date"], how="left")
    d["ln_float"] = d.ln_rmnd / shares
    # P 공시: 접수일 ∈ [D-1 거래일, D) → 당일 신호, 접수일 ∈ [D-7일, D) → 5일 건수
    disc = load_disc()
    cats = [c for c, _ in DISC_CATS] + ["기타"]
    d = d.sort_values(["code", "date"]).reset_index(drop=True)
    for cat in cats:
        d[f"p_{cat}"], d[f"p_{cat}_5"] = disc_window(d, disc[disc.cat == cat])
    d["kosdaq"] = (d.market == "KOSDAQ").astype(int)
    return d, cats


def evaluate(d, feats):
    import lightgbm as lgb
    params = {"objective": "binary", "learning_rate": 0.03, "num_leaves": 31, "min_data_in_leaf": 300,
              "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "verbose": -1, "seed": 7, "num_threads": 4}
    tr = d[d.date <= TRAIN_END]
    va = d[(d.date >= VAL_FROM) & (d.date <= VAL_END)].copy()
    m = lgb.train(params, lgb.Dataset(tr[feats], tr.lu), num_boost_round=400)
    va["p"] = m.predict(va[feats])
    r = va.p.rank()
    npos = va.lu.sum()
    auc = (r[va.lu == 1].sum() - npos * (npos + 1) / 2) / (npos * (len(va) - npos))
    va["rk"] = va.groupby("date").p.rank(ascending=False, method="first")
    t10 = va[va.rk <= 10]
    imp = pd.Series(m.feature_importance("gain"), index=feats)
    return dict(auc=auc, p10=t10.lu.mean(), touch10=t10.touch.mean(), ev_a=(t10.oc - t10.cost).mean(), ev_b=(t10.oc_lu - t10.cost).mean(), n=len(t10)), imp / imp.sum()


def main():
    d, cats = build()
    pool = d[d.gap <= GAP_MAX]
    va = pool[(pool.date >= VAL_FROM) & (pool.date <= VAL_END)]
    L = ["# 덜 쓰이는 데이터 × '아직 안 오른' 상한가 (PREREGISTRATION_altdata.md)", "",
         f"- 풀 = 시가 갭 ≤ +{GAP_MAX:.0%} · 실제가 ≥ 1,000원 · 20일 거래대금 ≥ 5억. 검증 구간 풀 상한가 확률 {va.lu.mean()*100:.2f}% (하루 {va.groupby('date').lu.sum().mean():.1f}건)",
         f"- 연결률(검증 풀): 신용 {va.cr_rate.notna().mean():.0%} · 공매도 {va.sh_ratio.notna().mean():.0%} · 대차 {va.ln_rmnd.notna().mean():.0%} · D-1 공시 있음 {(va[[f'p_{c}' for c in cats]].sum(axis=1) > 0).mean():.0%}",
         "- 매매 = 풀 안 매일 상위 10 을 시가 매수 → (a) 당일 종가 (b) 상한가 도달 시 +29.5% 매도·아니면 종가. 비용 후", "",
         "| 모델 | AUC | 상위10 적중(상한가 마감) | 상위10 상한가 터치 | (a) 종가 청산 | (b) 상한가 매도 | 표본 |", "|---|---|---|---|---|---|---|"]
    F = ["cr_rate", "cr_gvrt", "cr_new_amt", "cr_chg5", "cr_chg20", "cr_float"]
    G = ["sh_ratio", "sh_ratio5", "ln_float", "ln_chg20"]
    P = [f"p_{c}" for c in cats] + [f"p_{c}_5" for c in cats]
    A = alu.FEATS
    imps = None
    for lab, feats in (("A 기존", A), ("A+F 신용", A + F), ("A+G 공매도·대차", A + G), ("A+P 공시", A + P), ("A+F+G+P", A + F + G + P)):
        r, imp = evaluate(pool, feats)
        imps = imp if lab == "A+F+G+P" else imps
        L.append(f"| {lab} | {r['auc']:.3f} | {r['p10']:.1%} | {r['touch10']:.1%} | {r['ev_a']*100:+.2f}% | {r['ev_b']*100:+.2f}% | {r['n']:,} |")
    L += ["", "## A+F+G+P 중요도 상위 15", "", "| 특징 | 비중 |", "|---|---|"]
    for k, v in imps.sort_values(ascending=False).head(15).items():
        L.append(f"| {k} | {v:.1%} |")
    # 공시 종류별 서술 (전 구간, 봉인 제외)
    dd = d[d.date <= VAL_END]
    base_all, base_pool = dd.lu.mean(), dd[dd.gap <= GAP_MAX].lu.mean()
    L += ["", f"## D-1 공시 종류별 (2024-09 ~ 2026-05, 전체 상한가 확률 {base_all*100:.2f}% · 풀 {base_pool*100:.2f}%)", "",
          "| 공시 | 건 | 다음날 상한가 | 시가 갭 평균 | 갭 ≤3% 비율 | 풀 안 상한가 | 풀 안 시가→종가 | 풀 안 (b) 상한가 매도 |", "|---|---|---|---|---|---|---|---|"]
    for cat in cats:
        x = dd[dd[f"p_{cat}"] > 0]
        if len(x) < 30:
            continue
        xp = x[x.gap <= GAP_MAX]
        L.append(f"| {cat} | {len(x):,} | {x.lu.mean()*100:.2f}% ({x.lu.mean()/base_all:.1f}×) | {x.gap.mean()*100:+.1f}% | {len(xp)/len(x):.0%} | "
                 f"{xp.lu.mean()*100:.2f}% ({xp.lu.mean()/base_pool:.1f}×) | {(xp.oc - xp.cost).mean()*100:+.2f}% | {(xp.oc_lu - xp.cost).mean()*100:+.2f}% |")
    out = "\n".join(L)
    print(out)
    with open(os.path.join(HERE, "results_altdata.md"), "w", encoding="utf-8") as fh:
        fh.write(out + "\n")


if __name__ == "__main__":
    main()
