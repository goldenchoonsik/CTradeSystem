"""판독 엔진 과거 재현 채점 — PREREGISTRATION_reading_engine.md 그대로. 매달 t 에 그 시점 자료만으로 read_at(t) 를 돌리고 이후 실제 값으로 채점.

  .venv/bin/python -I docs/research/20261022_global_topdown/reading_walkforward.py [--parts R1R4|R5|all]
출력: results_reading.md·json, DataVault/staging/global_macro/reading/history.parquet(판독 이력 — 화면용)
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../..")))
from shared.data_library.global_macro import reading as R, store  # noqa: E402

from shared.data_library.global_macro.reading_eval import (START, SPLIT, me, run_engine, score_demand, score_comp,  # noqa: E402
                                                         score_sectors, summarize, verdicts)


def main():
    D = R.Data()
    hist = run_engine(D)
    res_d, res_c = score_demand(D, hist), score_comp(D, hist)
    res_s, ic = score_sectors(D, hist)
    S = summarize(res_d, res_c, res_s, ic)
    V = verdicts(S)
    res = {"generated_at": datetime.now().isoformat(timespec="seconds"),
           "prereg_sha256": open(os.path.join(HERE, "PREREGISTRATION_reading_engine.sha256")).read().split()[0], "summary": S, "verdicts": V,
           "latest": hist[-1] if hist else None, "has_items": D.items is not None}
    json.dump(res, open(os.path.join(HERE, "results_reading.json"), "w"), ensure_ascii=False, indent=1, default=lambda o: None if isinstance(o, float) and np.isnan(o) else str(o))
    # 판독 이력(화면용): 월 × 상대국 연결 상태·수요 판독, 업종 점수
    recs = []
    for r in hist:
        for g, lk in r["links"].items():
            d = r["demand"].get(g, {})
            recs.append({"t": r["t"], "kind": "link", "key": g, "state": lk["state"], "rho": lk["rho"], "k": lk["k"], "call": d.get("call"), "delta": d.get("delta")})
        for s, v in r["sectors"].items():
            recs.append({"t": r["t"], "kind": "sector", "key": s, "state": v["reading"], "rho": v["score"], "k": None, "call": None, "delta": v["D"]})
    os.makedirs(store.path("reading"), exist_ok=True)
    pd.DataFrame(recs).to_parquet(store.path("reading", "history.parquet"))
    report(res)


def report(res):
    S, V = res["summary"], res["verdicts"]
    pc = lambda x: "–" if x is None else f"{x * 100:.0f}%"
    L = ["# 판독 엔진 과거 재현 채점", "", f"사전등록 sha256 {res['prereg_sha256'][:12]}…, 매달 2008-01~ 그 시점 자료만으로 판독. 전반 ~2016 / 후반 2017~. 계산 {res['generated_at']}", "",
         "## 판정", ""] + [f"- **{k}**: {v}" for k, v in V.items()]
    if "demand" in S:
        L += ["", "## R2·R3 수요 판독 — 6개월 뒤 수출 전년비 가속/감속 적중률", "", "기준선: 추세 지속 = 최근 3개월 방향 유지, 평균 회귀 = 5년 평균 쪽으로(사전등록 밖 참고 기준선).", "",
              "| 대상 | n | 전체 적중 | 전반 | 후반 | 추세 지속 기준(전반/후반) | 평균 회귀 기준(전반/후반) | 예측-실제 상관 |", "|---|---|---|---|---|---|---|---|"]
        for g, r in S["demand"].items():
            e, l = r.get("early", {}), r.get("late", {})
            L.append(f"| {g} | {r['n']} | {pc(r['all']['hit'])} | {pc(e.get('hit'))} | {pc(l.get('hit'))} | {pc(e.get('base_mom'))}/{pc(l.get('base_mom'))} | {pc(e.get('base_mr'))}/{pc(l.get('base_mr'))} | {r['all'].get('corr') or 0:+.2f} |")
        dp = S["demand_pooled"]
        L += ["", "상대국 풀링: " + " · ".join(f"{p} n={v['n']} 적중 {pc(v['hit'])} (추세 {pc(v['base_mom'])}, 평균 회귀 {pc(v['base_mr'])})" for p, v in dp.items()),
              "연결 상태별: " + " · ".join(f"{k} n={v['n']} 적중 {pc(v['hit'])}" for k, v in S.get("demand_by_state", {}).items())]
    if "competition" in S:
        L += ["", "## R4 경쟁 판독 — 미국 시장 점유 변화가 향후 12개월 지속되나", "", " · ".join(f"{p} n={v['n']} 지속 {pc(v['hit'])}" for p, v in S["competition"].items()),
              "", " · ".join(f"{k} n={v['n']} 지속 {pc(v['hit'])}" for k, v in S.get("competition_by", {}).items())]
    if "sectors" in S:
        L += ["", "## R5 업종 판독 — 업종 6개월 초과수익(코스피 대비)", ""] + [
            f"- {p}: IC {v['ic']:+.3f} (t {v['t']:+.1f}, {v['n_months']}개월), 우호/비우호 적중 {pc(v['hit'])} (업종이 코스피를 이긴 비율 {pc(v['base_up'])})" for p, v in S["sectors"].items() if v.get("ic") is not None]
    lt = res.get("latest") or {}
    if lt:
        L += ["", f"## 최신 판독 ({lt['t']})", ""]
        L += [f"- 연결 {g}: {v['state']} (상관 {v['rho']:.2f}, {v['k']}개월 선행)" + (f" → 수요 {lt['demand'][g]['call']}" if g in lt['demand'] else "") for g, v in lt["links"].items()]
        if lt.get("total"):
            L.append(f"- 한국 총수출: {lt['total']['call']} (예측 변화 {lt['total']['delta']:+.3f})")
        for s, v in (lt.get("sectors") or {}).items():
            L.append(f"- 업종 {v['name']}: {v['reading']} (점수 {v['score']:+.2f}, 수요 {v['D']:+.3f}, 미국 점유 {('–' if v['C'] is None else f'{v[chr(67)]:+.2f}%p')})")
    open(os.path.join(HERE, "results_reading.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
