"""재무·업종 조건으로 거른 상한가 후보의 이후 5거래일 (서술·탐색, 전략 판정 아님).

재무는 '그날 공개돼 있던 것'만 쓴다: 분기(3·6·9월) = 분기 말 + 45일, 연간(12월) = + 90일 부터 사용.
KIS 분기 손익은 연초부터 누적(억원) → 최근 4분기(TTM) = 이번 누적 + 직전 연간 − 직전 연도 같은 분기 누적.

  .venv/bin/python docs/research/20261012_limit_up/fundamentals.py
"""
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import after5  # noqa: E402
import analyze_limit_up as alu  # noqa: E402

HERE = os.path.dirname(__file__)
FIN = os.path.join(alu.STAGE, "raw", "kis", "finance")


def _rows(api):
    out = []
    for fp in glob.glob(os.path.join(FIN, api, "*.json")):
        j = json.load(open(fp, encoding="utf-8"))
        for r in (j["response"].get("output") or []):
            if isinstance(r, dict) and r.get("stac_yymm"):
                out.append({"code": "A" + j["code"], **r})
    d = pd.DataFrame(out)
    for c in d.columns:
        if c not in ("code", "stac_yymm"):
            d[c] = pd.to_numeric(d[c], errors="coerce")
    return d.drop_duplicates(["code", "stac_yymm"])


def available(yymm):
    end = pd.to_datetime(yymm, format="%Y%m") + pd.offsets.MonthEnd(0)
    return end + pd.to_timedelta(np.where(end.dt.month == 12, 90, 45), unit="D")


def fundamentals():
    bal, inc_q, inc_y, rat = _rows("balance_q"), _rows("income_q"), _rows("income_y"), _rows("ratio_q")
    # KIS 는 결측(상장 전 분기 등)을 0 으로 준다 — 자산 0 인 재무상태표, 매출·영업이익·순이익이 모두 0 인 손익은 결측
    bal = bal[bal.total_aset != 0]
    zero = lambda x: (x.sale_account == 0) & (x.bsop_prti == 0) & (x.thtr_ntin == 0)
    inc_q, inc_y = inc_q[~zero(inc_q)], inc_y[~zero(inc_y)]
    rat = rat.merge(bal[["code", "stac_yymm"]], on=["code", "stac_yymm"], how="inner")     # 재무상태표 결측 분기의 비율도 결측
    q = inc_q[["code", "stac_yymm", "sale_account", "bsop_prti", "thtr_ntin"]].copy()
    q["y"], q["m"] = q.stac_yymm.str[:4].astype(int), q.stac_yymm.str[4:].astype(int)
    ann = inc_y[inc_y.stac_yymm.str[4:] == "12"][["code", "stac_yymm", "sale_account", "bsop_prti", "thtr_ntin"]].copy()
    ann["y"] = ann.stac_yymm.str[:4].astype(int)
    prev_ann = ann.assign(y=ann.y + 1).set_index(["code", "y"])[["sale_account", "bsop_prti", "thtr_ntin"]].add_prefix("pa_")
    prev_same = q.assign(y=q.y + 1).set_index(["code", "y", "m"])[["sale_account", "bsop_prti", "thtr_ntin"]].add_prefix("ps_")
    q = q.join(prev_ann, on=["code", "y"]).join(prev_same, on=["code", "y", "m"])
    for c in ("sale_account", "bsop_prti", "thtr_ntin"):
        q[f"ttm_{c}"] = np.where(q.m == 12, q[c], q[c] + q[f"pa_{c}"] - q[f"ps_{c}"])
    # 연간 영업적자 연속 연수 (그 분기 시점까지 공개된 연간 기준)
    ann = ann.sort_values(["code", "y"])
    ann["op_loss"] = (ann.bsop_prti < 0).astype(int)
    ann["loss_streak"] = ann.groupby("code").op_loss.transform(lambda s: s.groupby((s == 0).cumsum()).cumsum())
    f = q[["code", "stac_yymm", "ttm_sale_account", "ttm_bsop_prti", "ttm_thtr_ntin"]].merge(
        bal[["code", "stac_yymm", "total_aset", "total_lblt", "total_cptl", "cpfn"]], on=["code", "stac_yymm"], how="outer").merge(
        rat[["code", "stac_yymm", "lblt_rate", "roe_val", "rsrv_rate"]], on=["code", "stac_yymm"], how="outer")
    f["avail"] = available(f.stac_yymm)
    ls = ann[["code", "y", "loss_streak"]].copy()
    ls["avail_y"] = pd.to_datetime(ls.y.astype(str) + "-12-31") + pd.Timedelta(days=90)
    return f.sort_values("avail"), ls.sort_values("avail_y")


def attach(p, f, ls):
    p = p.sort_values("date")
    p = pd.merge_asof(p, f.drop(columns="stac_yymm").rename(columns={"avail": "fin_date"}), left_on="date", right_on="fin_date", by="code", direction="backward")
    p = pd.merge_asof(p, ls[["code", "avail_y", "loss_streak"]], left_on="date", right_on="avail_y", by="code", direction="backward")
    p["fin_age"] = (p.date - p.fin_date).dt.days
    p.loc[p.fin_age > 200, ["ttm_sale_account", "ttm_bsop_prti", "ttm_thtr_ntin", "lblt_rate", "total_cptl", "cpfn", "roe_val", "rsrv_rate"]] = np.nan
    return p


def info():
    rows = []
    for fp in glob.glob(os.path.join(FIN, "info", "*.json")):
        j = json.load(open(fp, encoding="utf-8"))
        o = j["response"].get("output") or {}
        rows.append({"code": "A" + j["code"], "sector": (o.get("std_idst_clsf_cd_name") or "").strip(),
                     "industry": (o.get("std_idst_clsf_cd_name") or "").strip()})
    return pd.DataFrame(rows).set_index("code")


DART = os.path.join(alu.STAGE, "raw", "dart", "hyslr")


def _pct(v):
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return np.nan


ORG_SUFFIX = ("재단", "학원", "공사", "공단", "은행", "조합", "기금", "협회", "법인", "그룹", "건설", "전자", "화학", "산업", "상사",
              "제약", "생명", "증권", "보험", "지주", "자동차", "투자", "물산", "제철", "통신", "에너지", "바이오", "캐피탈")


def ownership():
    """DART 최대주주 현황 → (회사, 공시일) 별 지분. 공시일 = 접수번호 앞 8자리."""
    rows = []
    for fp in glob.glob(os.path.join(DART, "*", "*.json")):
        j = json.load(open(fp, encoding="utf-8"))
        lst = [r for r in (j["response"].get("list") or []) if r.get("stock_knd", "").startswith("보통")]
        if not lst:
            continue
        tot = [r for r in lst if r.get("nm", "").strip() in ("계", "합계")]
        total = _pct(tot[0]["trmend_posesn_stock_qota_rt"]) if tot else np.nansum([_pct(r["trmend_posesn_stock_qota_rt"]) for r in lst])
        top = [r for r in lst if "최대주주" in r.get("relate", "") and "특수" not in r.get("relate", "")]
        top = top[0] if top else lst[0]
        nm = top.get("nm", "").strip()                                   # 사람 이름(한글 2~4자, 법인 접미어 없음)이 아니면 법인·재단 등
        corp_like = not re.fullmatch(r"[가-힣]{2,4}", nm) or nm.endswith(ORG_SUFFIX)
        rows.append({"stock_code": j["stock_code"], "pub": pd.to_datetime(lst[0]["rcept_no"][:8], format="%Y%m%d"),
                     "stlm": lst[0].get("stlm_dt"), "own_total": total, "own_top": _pct(top["trmend_posesn_stock_qota_rt"]),
                     "top_corp": int(corp_like)})
    o = pd.DataFrame(rows).sort_values(["stock_code", "pub"])
    o["own_chg"] = o.groupby("stock_code").own_total.diff()
    return o.sort_values("pub")


def attach_own(p, o):
    p = p.copy()
    p["stock_code"] = [c[1:] if c[1:] in OWN_CODES else c[1:6] + "0" for c in p.code]
    p = pd.merge_asof(p.sort_values("date"), o[["stock_code", "pub", "own_total", "own_top", "own_chg", "top_corp"]],
                      left_on="date", right_on="pub", by="stock_code", direction="backward")
    p.loc[(p.date - p.pub).dt.days > 400, ["own_total", "own_top", "own_chg", "top_corp"]] = np.nan
    return p


OWN_CODES = set()


def buckets(p):
    eq = p.total_cptl
    p["재무_자본"] = np.select([eq <= 0, eq < p.cpfn, eq.notna()], ["완전잠식", "부분잠식", "정상"], default="정보없음")
    p["재무_부채비율"] = pd.cut(p.lblt_rate, [-1e9, 50, 100, 200, 400, 1e12], labels=["<50%", "50~100%", "100~200%", "200~400%", "400%+"]).astype(str).replace("nan", "정보없음")
    p["재무_순이익TTM"] = np.select([p.ttm_thtr_ntin < 0, p.ttm_thtr_ntin >= 0], ["적자", "흑자"], default="정보없음")
    p["재무_영업이익TTM"] = np.select([p.ttm_bsop_prti < 0, p.ttm_bsop_prti >= 0], ["적자", "흑자"], default="정보없음")
    p["재무_연간영업적자연속"] = pd.cut(p.loss_streak, [-0.5, 0.5, 1.5, 2.5, 100], labels=["0년", "1년", "2년", "3년+"]).astype(str).replace("nan", "정보없음")
    p["재무_매출TTM"] = pd.cut(p.ttm_sale_account, [-1e12, 100, 500, 2000, 1e12], labels=["<100억", "100~500억", "500~2천억", "2천억+"]).astype(str).replace("nan", "정보없음")
    if "own_total" in p:
        p["지분_최대주주+특수관계인"] = pd.cut(p.own_total, [-1, 20, 30, 50, 101], labels=["<20%", "20~30%", "30~50%", "50%+"]).astype(str).replace("nan", "정보없음")
        p["지분_직전 보고서 대비"] = np.select([p.own_chg <= -1, p.own_chg >= 1, p.own_chg.notna()], ["−1%p 이상 감소", "+1%p 이상 증가", "±1%p 안"], default="정보없음")
        p["지분_최대주주 유형"] = np.select([p.top_corp == 1, p.top_corp == 0], ["법인", "개인"], default="정보없음")
    p["재무_ROE"] = pd.cut(p.roe_val, [-1e9, -10, 0, 10, 1e9], labels=["−10% 미만", "−10~0%", "0~10%", "10%+"]).astype(str).replace("nan", "정보없음")
    return p


GROUPS = ["지분_최대주주+특수관계인", "지분_직전 보고서 대비", "지분_최대주주 유형", "재무_자본", "재무_부채비율", "재무_순이익TTM", "재무_영업이익TTM", "재무_연간영업적자연속", "재무_매출TTM", "재무_ROE"]


def table(x, col, base_lu):
    rows = []
    for k, g in x.groupby(col):
        if len(g) < 200:
            continue
        day = g.groupby("date").net5.mean()
        thin = day.iloc[::5]
        t = thin.mean() / thin.std() * np.sqrt(len(thin)) if len(thin) > 2 else np.nan
        rows.append(f"| {k} | {len(g):,} | {g.lu.mean()*100:.2f}% ({g.lu.mean()/base_lu:.1f}×) | {g.d5.median()*100:+.1f}% | {(g.d5 <= -0.10).mean():.0%} | "
                    f"{g.any_lu.mean():.1%} | {day.mean()*100:+.2f}% | {t:+.1f} |")
    return rows


def main():
    d, uni = alu.features()
    p = after5.paths(d)
    f, ls = fundamentals()
    p = attach(p, f, ls)
    if os.path.isdir(DART):
        o = ownership()
        OWN_CODES.update(o.stock_code.unique())
        p = attach_own(p, o)
    p = buckets(p)
    inf = info()
    p["sector"] = p.code.map(inf.sector).fillna("")
    base_lu = p.lu.mean()
    hot = (p.lu_prev20 >= 2) | (p.vratio >= 10) | (p.vol20 >= 0.08) | (p.r20 >= 0.5) | (p.r1 >= alu.LU)
    L = [f"# 재무·업종 조건 × 상한가·이후 5거래일 ({p.date.min():%Y-%m-%d} ~ {p.date.max():%Y-%m-%d})", "",
         f"- 재무 연결률: {p.lblt_rate.notna().mean():.0%} (분기 재무가 200일 넘게 오래되면 '정보없음'), 지분 연결률: {p.own_total.notna().mean() if 'own_total' in p else 0:.0%} (DART 반기·사업보고서, 공시일부터)",
         f"- '뜨거운 후보' = 직전 20일 상한가 2회+ 또는 전일 거래대금 10배+ 또는 20일 변동성 8%+ 또는 20일 +50%+ 또는 전일 상한가 — {hot.mean():.1%} 종목-일, 상한가의 {p[hot].lu.sum()/p.lu.sum():.0%} 차지",
         "- 열: 상한가 확률(그날, 배수=전체 대비) / 진입(그날 시가)→5일 종가 중앙값 / 5일 −10% 이하 비율 / 5일 안 상한가 / 비용 후 5일 평균(신호일 가중) / t(겹침 없이)",
         "- 탐색용 다중 비교라 t 2~3 은 우연일 수 있음. 쓸 만한 게 보이면 봉인 구간(2026-06~) 사전등록 시험이 따로 필요", ""]
    for scope, m in (("전 종목", p.index == p.index), ("뜨거운 후보만", hot)):
        L += [f"## {scope}", ""]
        for col in [c for c in GROUPS if c in p]:
            L += [f"### {col.replace('재무_', '').replace('지분_', '지분: ')}", "", "| 구간 | 종목-일 | 그날 상한가 | 5일 중앙 | 5일 −10%↓ | 5일 안 상한가 | 비용 후 5일 | t |", "|---|---|---|---|---|---|---|---|"]
            L += table(p[m], col, base_lu) + [""]
    # 가장 건전한 쪽 조합 — 재무 필터로 '뜨거운 후보'의 이후 5일이 바뀌나
    good = (p.ttm_thtr_ntin > 0) & (p.ttm_bsop_prti > 0) & (p.total_cptl > p.cpfn) & (p.lblt_rate < 100)
    combos = [("뜨거운 후보 전체", hot), ("뜨거운 + 흑자·영업흑자·자본정상·부채<100%", hot & good),
              ("뜨거운 + 위 + ROE 10%+", hot & good & (p.roe_val >= 10)),
              ("뜨거운 + 위 + 최대주주 지분 30%+ & 지분 감소 없음", hot & good & (p.own_total >= 30) & (p.own_chg > -1)),
              ("뜨거운 + 적자·3년+ 영업적자(반대쪽)", hot & (p.ttm_thtr_ntin < 0) & (p.loss_streak >= 3))]
    L += ["## 재무 조합 필터 — 뜨거운 후보", "", "| 조합 | 종목-일 | 그날 상한가 | 5일 중앙 | 5일 −10%↓ | 5일 안 상한가 | 비용 후 5일 | t |", "|---|---|---|---|---|---|---|---|"]
    for lab, m in combos:
        L += [r.replace("| 전체 |", f"| {lab} |", 1) for r in table(p[m].assign(_k="전체"), "_k", base_lu)]
    L += [""]
    # 업종
    sec = p.groupby("sector").agg(n=("lu", "size"), lu=("lu", "sum"))
    sec["rate"], sec["lift"] = sec.lu / sec.n, sec.lu / sec.n / base_lu
    sec = sec[sec.n >= 5000].sort_values("lift", ascending=False)
    sec = pd.concat([sec.head(12), sec.tail(6)])
    L += ["## 업종(표준산업분류, 종목-일 5천 이상) — 상한가 배수 상위 12·하위 6", "", "| 업종 | 상한가 | 종목-일 | 배수 | 뜨거운 후보 5일 중앙 |", "|---|---|---|---|---|"]
    for s, r in sec.iterrows():
        h = p[hot & (p.sector == s)]
        L.append(f"| {s or '(없음)'} | {int(r.lu)} | {int(r.n):,} | {r.lift:.1f}× | {h.d5.median()*100:+.1f}% (n{len(h):,}) |")
    out = "\n".join(L)
    print(out)
    with open(os.path.join(HERE, "results_fundamentals.md"), "w", encoding="utf-8") as fh:
        fh.write(out + "\n")


if __name__ == "__main__":
    main()
