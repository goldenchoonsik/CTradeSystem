"""R5-V(부가가치 가중 수요 충격) 과거 재현 — 참고 자료(판정 아님). PREREGISTRATION_reading_engine_V.md 정의 그대로:
2008-01~ 매달 그 시점 자료로 원안 판독을 재현하고, 그 시점에 공표돼 있던 TiVA 연도(t.year−3 이하)로 V 를 덧붙여 IC 를 잰다."""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))

from shared.data_library.global_macro import reading as R  # noqa: E402
from shared.data_library.global_macro import reading_eval as E  # noqa: E402
from shared.data_library.global_macro import reading_ext as X  # noqa: E402
from shared.data_library.global_macro import tiva_industry as TI  # noqa: E402


def main():
    D = R.Data()
    hist = E.run_engine(D)
    va = TI.va_exposure_by_year()
    for r in hist:
        X.attach_v(r, va)
    res, ic = E.score_sectors(D, hist)
    out = {"generated_at": datetime.now().isoformat(timespec="seconds"), "prereg_sha256": open(os.path.join(HERE, "PREREGISTRATION_reading_engine_V.sha256")).read().split()[0],
           "n_months": int(len(ic)), "va_years_used": sorted({r.get("va_year") for r in hist if r.get("va_year")}), "periods": {}}
    for per, m in (("all", ic.index == ic.index), ("early", ic.index <= E.SPLIT), ("late", ic.index > E.SPLIT)):
        rec = {}
        for col in ("composite", "D", "V"):
            icm, t, n = E.mean_nw(ic[col][m], R.H)
            rec[col] = {"ic": None if pd.isna(icm) else float(icm), "t": None if pd.isna(t) else float(t), "n": int(n)}
        ss = res[(res.t <= E.SPLIT) if per == "early" else (res.t > E.SPLIT) if per == "late" else (res.t == res.t)]
        for col, sc in (("D", "score_D"), ("V", "score_V"), ("composite", "score")):
            sp = E._spread(ss.dropna(subset=[sc]), sc)
            rec[col]["spread_top2"] = float(sp.mean()) if len(sp) else None
            hcol = {"D": "hit_D", "V": "hit_V", "composite": "hit"}[col]
            rec[col]["hit"] = float(ss[hcol].dropna().mean()) if ss[hcol].notna().any() else None
        out["periods"][per] = rec
    both = res.dropna(subset=["score_D", "score_V"])
    out["corr_D_V"] = float(both.score_D.corr(both.score_V)) if len(both) else None
    out["agree_D_V"] = float((np.sign(both.score_D) == np.sign(both.score_V)).mean()) if len(both) else None
    with open(os.path.join(HERE, "results_reading_v.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    P = out["periods"]
    f = lambda r, k: ("–" if r.get(k) is None else f"{r[k]:+.3f}" if k in ("ic", "spread_top2") else f"{r[k]:.1f}" if k == "t" else f"{r[k]*100:.0f}%" if k == "hit" else str(r[k]))
    L = ["# R5-V 과거 재현 (참고, 판정 아님) — 사전등록 V", "",
         f"사전등록 sha256 {out['prereg_sha256'][:12]}…, 판독 {out['n_months']}개월(2008-01~), 쓰인 TiVA 연도 {out['va_years_used'][0]}~{out['va_years_used'][-1]}(시차 3년). 계산 {out['generated_at']}", "",
         "| 구간 | 점수 | IC | t(NW6) | 상위2−하위2 6개월 | 우호/비우호 적중 | n |", "|---|---|---|---|---|---|---|"]
    for per, nm in (("all", "전체"), ("early", "~2016"), ("late", "2017~")):
        for col, cn in (("composite", "합성(원안)"), ("D", "수요만(트랙 D)"), ("V", "VA 가중 수요(트랙 V)")):
            r = P[per][col]; L.append(f"| {nm} | {cn} | {f(r,'ic')} | {f(r,'t')} | {f(r,'spread_top2')} | {f(r,'hit')} | {r['n']} |")
    L += ["", f"- score_D 와 score_V 상관 {out['corr_D_V']:.2f}, 부호 일치 {out['agree_D_V']*100:.0f}% — 둘이 거의 같으면 V 는 D 의 재포장이고, 다르면 미국·유럽 노출을 더 쳐 주는 쪽의 정보가 붙는다.",
          "- 이 표는 결과를 보고 고른 변형의 과거 성적이라 판정에 쓰지 않는다. 판정은 2026-10-31 이후 전진 기록(FORWARD_TRACKS.md 트랙 V)."]
    open(os.path.join(HERE, "results_reading_v.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
