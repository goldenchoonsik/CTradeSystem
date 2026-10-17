"""1단계: SimFin Publish Date(SEC 제출일)는 실적 발표일이 아닐 수 있다 → 거래량 급증일로 실제 반응일 추정·검증.
창 = [제출일-45일, 제출일+2거래일], 점수 = 거래량 / 직전 60거래일 중앙값(창 시작 전). 대조군 = 창을 46일 앞으로 이동.
산출: staging/us_fundamentals_simfin/derived/events.csv
"""
import json
import numpy as np, pandas as pd
import sys; from pathlib import Path; sys.path.insert(0, str(Path(__file__).resolve().parent))
import factor_test as ft

R = ft.RAW
OUT = R.parent / "derived"; OUT.mkdir(exist_ok=True)
PRE_DAYS, POST_TD, BASE_TD = 45, 2, 60


def locate(px, p, shift_days=0):
    """제출일 p 기준 창에서 거래량 비율 최댓값 날짜·값. px: 일봉(거래량 포함)."""
    idx = px.index
    end_pos = idx.searchsorted(p + pd.Timedelta(days=shift_days), side="right") - 1 + (POST_TD if shift_days == 0 else 0)
    start = p + pd.Timedelta(days=shift_days - PRE_DAYS)
    start_pos = idx.searchsorted(start)
    if start_pos < BASE_TD or end_pos >= len(idx) or end_pos <= start_pos:
        return None
    base = px["vol"].iloc[start_pos - BASE_TD:start_pos]
    med = base[base > 0].median()
    if not med or np.isnan(med):
        return None
    w = px["vol"].iloc[start_pos:end_pos + 1] / med
    if w.isna().all():
        return None
    j = int(np.nanargmax(w.values))
    return idx[start_pos + j], float(w.iloc[j]), (start_pos + j) - idx.searchsorted(p, side="right") + 1


rows = []
for f in sorted((R / "statements").glob("*.json")):
    if f.stat().st_size <= 500:
        continue
    cid = int(f.stem)
    q = ft.load_company(cid)
    pj = R / "prices" / f"{cid}.json"
    if q is None or not pj.exists():
        continue
    dd = json.loads(pj.read_text())
    if not dd or not dd[0].get("data"):
        continue
    d = dd[0]
    px = ft.table(d); px["Date"] = pd.to_datetime(px["Date"])
    px = px.set_index("Date").rename(columns={"Trading Volume": "vol"})
    # info = 제출일+1 이므로 원래 제출일 복원
    for _, e in q.iterrows():
        p = e["info"] - pd.Timedelta(days=1)
        a = locate(px, p); b = locate(px, p, shift_days=-46)
        if a is None:
            continue
        rows.append(dict(id=cid, ticker=d["ticker"], filed=p, rd=e["rd"], est_day=a[0], vol_ratio=a[1], offset_td=a[2],
                         placebo_ratio=(b[1] if b else np.nan), mcap_hint=float(px["Last Closing Price"].iloc[-1] * (px["Common Shares Outstanding"].iloc[-1] or 0))))
ev = pd.DataFrame(rows)
ev.to_csv(OUT / "events.csv", index=False)
print(f"이벤트 {len(ev)}건, 회사 {ev['id'].nunique()}개")
print("거래량비율 중앙값 실제 %.2f vs 대조군 %.2f" % (ev["vol_ratio"].median(), ev["placebo_ratio"].median()))
print("비율>=3 비중 실제 %.0f%% vs 대조군 %.0f%%" % (100 * (ev["vol_ratio"] >= 3).mean(), 100 * (ev["placebo_ratio"] >= 3).mean()))
print("제출일 대비 반응일 거래일 차이 분포(제출일 다음 거래일=1):")
print(ev["offset_td"].clip(-30, 3).value_counts(normalize=True).sort_index().mul(100).round(1).to_string())
big = ev[ev["mcap_hint"] > 5e9]
print(f"\n대형주(시총>$50억) {len(big)}건: 반응일이 제출일 직전 1일~제출 후 1일 이내 비중 %.0f%%" % (100 * big["offset_td"].between(-1, 1).mean()))
sm = ev[ev["mcap_hint"] < 1e9]
print(f"소형주(<$10억) {len(sm)}건: 같은 비중 %.0f%%, 제출일 5거래일 이상 이전 %.0f%%" % (100 * sm["offset_td"].between(-1, 1).mean(), 100 * (sm["offset_td"] <= -5).mean()))
