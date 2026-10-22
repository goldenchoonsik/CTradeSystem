"""지평 곡선(국소투영) 검정 — PREREGISTRATION_horizon.md 정의 그대로. 실행: .venv/bin/python docs/research/20261022_global_topdown/horizon_test.py
산출: results_horizon.json/.md, DataVault/staging/global_macro/signals/registry_stage6_horizon.json(신호 상태 탭)."""
import json
import os
import sys
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))

from shared.data_library.global_macro import ledger, store  # noqa: E402
from shared.data_library.global_macro import reading as R  # noqa: E402
from shared.data_library.global_macro import reading_eval as E  # noqa: E402
from shared.data_library.global_macro import reading_horizon as H  # noqa: E402


def main():
    D = R.Data()
    hist = E.run_engine(D)
    res = H.curves(D, hist)
    res.update(generated_at=datetime.now().isoformat(timespec="seconds"), prereg_sha256=open(os.path.join(HERE, "PREREGISTRATION_horizon.sha256")).read().split()[0],
               ledger_sha256=ledger.sha256(), n_readings=len(hist), first=hist[0]["t"], last=hist[-1]["t"])
    with open(os.path.join(HERE, "results_horizon.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    J = res["judgment"]
    L = ["# 판독 지평 곡선 — 국소투영 (사전등록 H)", "",
         f"사전등록 sha256 {res['prereg_sha256'][:12]}…, 원장 {res['ledger_sha256'][:8]}…, 판독 {res['first'][:7]}~{res['last'][:7]} {res['n_readings']}개월, 지평 1~12. 계산 {res['generated_at']}", "",
         "## 판정 (전체 구간, 통과 = 값>0 & NW t≥2, 2개 이상 연속)", "", "| 판독 | 통과 지평 | 유효 구간 | 판정 | h* |", "|---|---|---|---|---|"]
    for k, j in J.items():
        L.append(f"| {k} | {', '.join(map(str, j['passed'])) or '없음'} | {('~'.join(map(str, [j['run'][0], j['run'][-1]])) if j['run'] else '–')} | {j['verdict']} | {j['h_star'] or '–'} |")
    def table(rows, cols, title, key=None):
        L.extend(["", f"## {title}", "", "| h | " + " | ".join(f"{c} 전체" for c in cols) + " | " + " | ".join(f"{c} ~2016" for c in cols) + " | " + " | ".join(f"{c} 2017~" for c in cols) + " |",
                  "|---|" + "---|" * (3 * len(cols))])
        df = pd.DataFrame(rows)
        if key:
            df = df[df.score == key]
        for h in res["horizons"]:
            cells = []
            for per in ("all", "early", "late"):
                r = df[(df.h == h) & (df.per == per)]
                cells += [("–" if not len(r) or r[c].iloc[0] is None or pd.isna(r[c].iloc[0]) else f"{r[c].iloc[0]:+.3f}" if c != "n_months" else str(int(r[c].iloc[0]))) for c in cols]
            L.append(f"| {h} | " + " | ".join(cells) + " |")
    table(res["demand"], ["hit", "mr", "diff", "t"], "R2 수요 판독 — 적중 vs 평균 회귀(월별 상대국 평균)")
    table(res["total"], ["hit", "mr", "diff", "t"], "R3 총수출")
    table(res["sectors"], ["ic", "t", "spread", "ic_marginal", "t_marginal"], "R5 업종 — 합성 점수(주 판정)", key="composite")
    table(res["sectors"], ["ic", "t", "spread", "ic_marginal", "t_marginal"], "R5-D 업종 — 수요 충격만(보충, 전진 트랙 D 참고)", key="D")
    L += ["", "## 읽는 법", "", "- hit/mr = 지평 h 누적 결과에 대한 판독·기준선 적중률, diff = 차이(월 시계열 평균), t = NW(지연 h).",
          "- ic = 월별 업종 간 순위상관 평균(누적 h개월 초과수익), spread = 상위2−하위2 초과수익(로그), ic_marginal = t+h−1→t+h 한 달 구간 IC(정보가 어느 달에 반영되는지).",
          f"- {res['rule']}"]
    with open(os.path.join(HERE, "results_horizon.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    # 신호 상태 탭
    def sig(id_, name, rows, value, j, fam):
        df = pd.DataFrame(rows)
        hs = j["h_star"] or 6
        e, l = df[(df.h == hs) & (df.per == "early")].iloc[0], df[(df.h == hs) & (df.per == "late")].iloc[0]
        return {"id": id_, "family": fam, "name": name, "verdict": j["verdict"], "horizon": (f"유효 {j['run'][0]}~{j['run'][-1]}개월, 최고 {hs}" if j["run"] else "유효 지평 없음"),
                "dev": {"effect": None if pd.isna(e[value]) else float(e[value]), "t": None if pd.isna(e["t"]) else float(e["t"]), "n": int(e["n_months"])},
                "conf": {"effect": None if pd.isna(l[value]) else float(l[value]), "t": None if pd.isna(l["t"]) else float(l["t"]), "n": int(l["n_months"])},
                "current": {"date": res["last"], "text": f"통과 지평 {', '.join(map(str, j['passed'])) or '없음'}"}}
    sec = res["sectors"]
    reg = {"stage": "H", "generated_at": res["generated_at"], "prereg_sha256": res["prereg_sha256"], "source": "docs/research/20261022_global_topdown/horizon_test.py",
           "periods": ["~2016", "2017~"], "combos": [],
           "signals": [sig("H-R2", "수요 판독 지평 곡선(적중 − 평균 회귀)", res["demand"], "diff", J["R2 수요"], "판독 지평"),
                       sig("H-R3", "총수출 판독 지평 곡선", res["total"], "diff", J["R3 총수출"], "판독 지평"),
                       sig("H-R5", "업종 합성 판독 지평 곡선(IC)", [r for r in sec if r["score"] == "composite"], "ic", J["R5 합성(주)"], "판독 지평"),
                       sig("H-R5D", "업종 수요만 판독 지평 곡선(IC, 보충)", [r for r in sec if r["score"] == "D"], "ic", J["R5-D 수요만(보충)"], "판독 지평")]}
    os.makedirs(store.path("signals"), exist_ok=True)
    with open(store.path("signals", "registry_stage6_horizon.json"), "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=1)
    print("\n".join(L[:12]))


if __name__ == "__main__":
    main()
