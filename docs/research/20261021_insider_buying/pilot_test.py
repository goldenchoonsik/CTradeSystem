"""임원 장내매수 맛보기 시험 (판정 아님) — fetch_sample.py 로 받은 표본 원문 파싱 → 사건 → 비용 후 초과수익.

  .venv/bin/python -I docs/research/20261021_insider_buying/pilot_test.py

사건: 개인 보고자, 보통주 '장내매수' − '장내매도' 순증 > 0 인 보고서. 신호일 = 접수일, 매수 = 접수일 다음 거래일 종가,
보유 20/60/120 거래일. 같은 회사는 첫 사건 뒤 60거래일 안의 사건 무시. 비용 왕복 0.57%.
기준 = 매수일 같은 시총 5분위 유니버스(종가≥1,000원·20일 평균 거래대금≥5억·우선주/스팩 제외) 동일가중 같은 기간 수익.
"""
import glob
import os
import re
import zipfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
KR = os.path.join(ROOT, "DataVault", "staging", "kr_equity")
DOCS = os.path.join(KR, "raw", "dart", "insider_docs")
COST = 0.0057
HS = (20, 60, 120)


def num(s):
    s = re.sub(r"[^\d\-.]", "", (s or "").split("&cr;")[0])
    try:
        return float(s) if s not in ("", "-", ".") else np.nan
    except ValueError:
        return np.nan


def parse(fp):
    with zipfile.ZipFile(fp) as z:
        raw = z.read(z.namelist()[0])
    try:  # 연도마다 인코딩이 다름(2017 은 UTF-8, 나머지 EUC-KR)
        t = raw.decode("utf-8")
    except UnicodeDecodeError:
        t = raw.decode("cp949", errors="ignore")

    def one(code):
        m = re.search(rf'(?:AUNIT|ACODE)="{code}"[^>]*>(.*?)</T[UED]>', t, re.S)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""

    out = dict(no=os.path.basename(fp)[:-4], ifr_tp=one("IFR_TP"), stf=one("STF_RYN"), pos=one("STF_PSM"),
               main_sh=one("MAIN_SH"), crp_cd=one("CRP_CD"), hold_rt=num(one("HLD_STK_RT")))
    buy = sell = 0.0
    buy_amt = 0.0
    other = []
    for tr in re.findall(r"<TR[^>]*>(.*?)</TR>", t, re.S):
        if 'AUNIT="RPT_RSN"' not in tr:
            continue
        cell = {m.group(1): re.sub(r"\s+", " ", m.group(2)).strip()
                for m in re.finditer(r'(?:AUNIT|ACODE)="([A-Z_0-9]+)"[^>]*>(.*?)</T[UED]>', tr, re.S)}
        rsn, knd = cell.get("RPT_RSN", ""), cell.get("STR_KND", "")
        q, px = num(cell.get("MDF_STK_CNT")), num(cell.get("ACI_AMT2"))
        if "보통" not in knd or np.isnan(q):
            continue
        if "장내매수" in rsn:
            buy += abs(q)
            buy_amt += abs(q) * (px if px == px else 0)
        elif "장내매도" in rsn:
            sell += abs(q)
        else:
            other.append(rsn)
    out.update(buy=buy, sell=sell, buy_amt=buy_amt, other="|".join(sorted(set(other)))[:80])
    return out


def main():
    plan = pd.read_csv(os.path.join(HERE, "sample_plan.csv"), dtype={"no": str, "stock": str, "dt": str, "corp": str})
    rows = []
    for no in plan.no:
        fp = os.path.join(DOCS, no[:4], f"{no}.zip")
        if os.path.exists(fp):
            try:
                rows.append(parse(fp))
            except Exception as e:  # 깨진 문서는 건너뛰고 셈
                rows.append(dict(no=no, err=str(e)[:60]))
    p = pd.DataFrame(rows).merge(plan, on="no", how="left")
    p.to_csv(os.path.join(HERE, "sample_parsed.csv"), index=False)
    lines = ["# 임원 장내매수 맛보기 (판정 아님)", ""]
    lines.append(f"- 표본 원문 {len(p)}건 파싱, 사유 표 있음 {int((p.buy + p.sell + p.other.fillna('').str.len()).gt(0).sum())}")
    p["insider"] = p.stf.str.contains("임원", na=False)
    p["person"] = p.ifr_tp.str.startswith("개인", na=False)
    p["ceo"] = p.pos.str.contains("대표", na=False)
    p["net"] = p.buy - p.sell
    lines.append(f"- 장내매수 있는 보고서 {int((p.buy > 0).sum())} (임원 개인 {int(((p.buy > 0) & p.insider & p.person).sum())}), "
                 f"장내매도 있는 보고서 {int((p.sell > 0).sum())}")
    oth = p.other.fillna("").str.split("|").explode()
    lines.append(f"- 그 밖의 사유 상위: {oth[oth != ''].value_counts().head(8).to_dict()}")

    # 가격 패널
    pr = pd.read_parquet(os.path.join(KR, "derived", "prices_1d.parquet"), columns=["code", "date", "close", "close_unadj", "value"])
    pr["code"] = pr["code"].astype(str)
    close = pr.pivot(index="date", columns="code", values="close").sort_index()
    cu = pr.pivot(index="date", columns="code", values="close_unadj").reindex(close.index)
    val = pr.pivot(index="date", columns="code", values="value").reindex(close.index)
    ext = pd.read_parquet(os.path.join(KR, "derived", "daily_ext.parquet"), columns=["code", "date", "market_cap"])
    ext["code"] = ext["code"].astype(str)
    mcap = ext.pivot(index="date", columns="code", values="market_cap").reindex(close.index)
    uni = pd.read_csv(os.path.join(KR, "universe.csv"), dtype=str)
    bad = set(uni[(uni.is_preferred == "1") | (uni.is_spac == "1")].code)
    cols = [c for c in close.columns if c not in bad]
    close, cu, val, mcap = close[cols], cu[cols], val[cols], mcap[cols]
    cu = cu.fillna(close)  # 상폐 무수정 공백 → 수정 종가(연말 연구 함정)
    elig = (cu >= 1000) & (val.rolling(20, min_periods=15).mean() >= 5e8) & mcap.notna() & close.notna()
    cf = close.ffill()
    fwd = {h: cf.shift(-h) / cf - 1 for h in HS}
    dates = close.index

    # 사건
    ev = p[(p.net > 0) & p.person & p.insider & p.stock.notna()].copy()
    ev["code"] = "A" + ev.stock.str.zfill(6)
    ev["sig"] = pd.to_datetime(ev.dt)
    pos = np.searchsorted(dates.values, ev.sig.values, side="right")  # 접수일 다음 거래일
    ev = ev[pos < len(dates)].copy()
    ev["ei"] = pos[pos < len(dates)]
    ev["entry"] = dates[ev.ei]
    ev = ev[ev.code.isin(close.columns)].sort_values(["code", "entry"])
    keep, last = [], {}
    for i, r in ev.iterrows():
        if r.code in last and r.ei - last[r.code] < 60:
            continue
        last[r.code] = r.ei
        keep.append(i)
    ev = ev.loc[keep].copy()
    res = []
    for _, r in ev.iterrows():
        d, c = r.entry, r.code
        if not elig.at[d, c]:
            continue
        mc = mcap.loc[d][elig.loc[d]]
        q = pd.qcut(mc.rank(method="first"), 5, labels=False)
        rec = dict(no=r.no, code=c, entry=d, q=int(q[c]), ceo=r.ceo, reg=r.stf == "등기임원",
                   amt=r.buy_amt, pre20=cf.at[d, c] / cf.iloc[max(r.ei - 20, 0)][c] - 1)
        for h in HS:
            if r.ei + h >= len(dates):
                rec[f"x{h}"] = np.nan
                continue
            f = fwd[h].loc[d]
            peers = f[q.index[q == q[c]]].dropna()
            rec[f"r{h}"] = f[c]
            rec[f"b{h}"] = peers.mean()
            rec[f"x{h}"] = f[c] - COST - peers.mean()
        res.append(rec)
    R = pd.DataFrame(res)
    R.to_csv(os.path.join(HERE, "pilot_events.csv"), index=False)

    def summ(df, lab):
        out = [f"| {lab} | {len(df)} |"]
        for h in HS:
            x = df[f"x{h}"].dropna()
            mon = df.dropna(subset=[f"x{h}"]).groupby(df.entry.dt.to_period("M"))[f"x{h}"].mean()
            t = mon.mean() / mon.std(ddof=1) * np.sqrt(len(mon)) if len(mon) > 2 else np.nan
            out.append(f" {x.mean():+.2%} (중앙 {x.median():+.2%}, 승 {(x > 0).mean():.0%}, t {t:.2f}) |")
        return "".join(out)

    lines += ["", f"## 사건 {len(R)}개 (임원 개인 순매수, 60거래일 겹침 제거, 유니버스 조건 통과)", "",
              "| 묶음 | n | 20일 초과(비용 후) | 60일 | 120일 |", "|---|---|---|---|---|"]
    lines.append(summ(R, "전체"))
    lines.append(summ(R[R.ceo], "대표이사"))
    lines.append(summ(R[R.reg & ~R.ceo], "등기임원(대표 제외)"))
    lines.append(summ(R[~R.reg], "비등기임원"))
    big = R.amt >= R.amt.median()
    lines.append(summ(R[big], f"매수금액 상위 절반(≥{R.amt.median() / 1e6:.0f}백만원)"))
    lines.append(summ(R[~big], "매수금액 하위 절반"))
    lines.append(summ(R[R.pre20 < 0], "직전 20일 하락 후 매수"))
    lines.append(summ(R[R.pre20 >= 0], "직전 20일 상승 중 매수"))
    for qq in range(5):
        lines.append(summ(R[R.q == qq], f"시총 {qq + 1}분위(1=소형)"))
    lines += ["", "## 연도별 (60일 초과, 비용 후)", ""]
    y = R.dropna(subset=["x60"]).groupby(R.entry.dt.year)["x60"].agg(["count", "mean"])
    lines += [f"- {k}: n {int(v['count'])}, {v['mean']:+.2%}" for k, v in y.iterrows()]
    lines += ["", f"- 비용 전 60일 사건 수익 평균 {R.r60.mean():+.2%}, 같은 분위 기준 {R.b60.mean():+.2%}"]
    with open(os.path.join(HERE, "pilot_results.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
