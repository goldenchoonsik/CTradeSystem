"""보조 확인(사전등록 밖, 설명용) — results_aux.json.

1) 배수 수준을 진짜 실질금리(TIPS, 2003~)로 다시: log CAPE ~ TIPS10 + 기대물가 + 물가 변동성 + 신용.
2) 주식−채권 10년 초과수익: ECY 를 TIPS 실질금리로 계산했을 때의 현재 추정(B1b 회귀 기울기 그대로 적용).
3) 1/CAPE(M2) 의 역대 최저 구간과 현재 위치.
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vlib import HERE, ols, panel  # noqa: E402


def main():
    p = panel()
    out = {}
    s = p.loc["2003-01":"2026-10"].copy()
    s["lcape"] = np.log(s["CAPE_pit"])
    r = ols(s["lcape"], s[["tips10", "breakeven", "infl_vol", "credit"]], nw_lags=24)
    out["1_TIPS_수준회귀_2003~"] = {"n": r["n"], "r2": round(r["r2"], 3),
                                "계수": {k: round(v, 3) for k, v in r["b"].items() if k != "const"},
                                "t(NW24)": {k: round(v, 2) for k, v in r["t"].items() if k != "const"},
                                "단독R2": {x: round(ols(s["lcape"], s[[x]])["r2"], 3) for x in ["tips10", "breakeven", "infl_vol", "credit"]}}
    # 2) B1b 회귀(실현 확인된 짝 전부)
    xs = 100 * p["xs10_real_sh"]
    d = pd.concat([p["ecy"], xs], axis=1).dropna()
    b = np.polyfit(d.iloc[:, 0], d.iloc[:, 1], 1)
    now = p.index[-1]
    ecy_tips = 100 / p["CAPE_pit"][now] - p["tips10"][now]
    out["2_주식채권_10년초과"] = {"기울기": round(b[0], 3), "절편": round(b[1], 2),
                            "ECY_대리(Shiller식)": round(p["ecy"][now], 2), "예측_대리": round(b[0] * p["ecy"][now] + b[1], 2),
                            "ECY_TIPS": round(ecy_tips, 2), "예측_TIPS": round(b[0] * ecy_tips + b[1], 2),
                            "M2_주식실질−TIPS": round(100 / p["CAPE_pit"][now] - p["tips10"][now], 2)}
    # 3) 1/CAPE 역대 위치
    ey = (100 / p["CAPE_pit"]).dropna()
    low = ey.nsmallest(400)
    yrs = sorted({t.year for t in low.index})
    out["3_1/CAPE"] = {"현재": round(ey.iloc[-1], 2), "역대최저": round(ey.min(), 2), "최저월": str(ey.idxmin()),
                      "현재보다_낮았던_달": int((ey < ey.iloc[-1]).sum()),
                      "그_달들의_연도": sorted({str(t.year) for t in ey[ey < ey.iloc[-1]].index}),
                      "백분위(낮을수록 비쌈)": round(float((ey < ey.iloc[-1]).mean()), 4)}
    with open(os.path.join(HERE, "results_aux.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
