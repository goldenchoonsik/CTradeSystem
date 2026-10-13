# -*- coding: utf-8 -*-
"""
자동연구 후보 신뢰도 진단 (Reliability Check)
==============================================

리더보드 수익률만으로는 "실력인지 운인지"를 알 수 없다. 2026-10-07 069500 15m 챔피언(홀드아웃 +4.9%, 13건,
승률 77%)을 손으로 검증해 보니 무작위 진입 대비 p=0.019 였지만, 홀드아웃이 후보 선택에도 쓰였고(15개 트라이얼
중 1위), 거래 t=1.65, WFA 성과의 절반 이상이 한 창에서 나왔다 — '가능성은 있으나 단정할 수 없음'.
이 모듈은 그 검증을 버튼 하나로 재현한다.

검사 항목 (모두 같은 데이터·같은 실행 엔진 기준)
  1. 무작위 진입 대비 (순환 이동 검정) — 신호 패턴을 통째로 시간축에서 무작위로 밀어 같은 엔진으로 재시뮬레이션
                                     (신호 수·몰림 구조·거래 횟수 유지, 타이밍만 무작위)
  2. 선택 편향 보정                 — 홀드아웃으로 트라이얼 k개 중 골랐다면 p_adj = 1-(1-p)^k (Šidák, 보수적)
  3. 거래 단위 t값                  — 평균 거래 수익 / 표준오차
  4. WFA 전진검증 일관성             — 창별 수익의 t값
  5. WFA 집중도                     — 최고 창 하나를 빼도 플러스인가
  6. 독립 OOS 여부                  — 홀드아웃이 후보 선택에 쓰이지 않았는가
"""
from __future__ import annotations

import glob
import json
import math
import os
from typing import Any, Callable, Dict, List, Optional

import numpy as np

PASS, WARN, FAIL = "pass", "warn", "fail"
DEFAULT_PERMUTATIONS = 2000


def _t_stat(x: List[float]) -> Optional[float]:
    a = np.asarray([v for v in x if v is not None], dtype=float)
    if len(a) < 2 or a.std(ddof=1) == 0:
        return None
    return float(a.mean() / (a.std(ddof=1) / math.sqrt(len(a))))


def _check(key, title, status, value, detail):
    return {"key": key, "title": title, "status": status, "value": value, "detail": detail}


def summarize(null_test: Optional[dict], trade_returns: List[float], wfa_profits: List[float],
              n_selection_trials: int, oos_independent: bool) -> Dict[str, Any]:
    """검사 결과 판정 (순수 함수 — 테스트·UI 공용)."""
    checks = []

    # 1·2. 무작위 대비 + 선택 편향 보정
    p = (null_test or {}).get("p_value")
    k = max(1, int(n_selection_trials)) if not oos_independent else 1
    p_adj = None if p is None else float(1.0 - (1.0 - p) ** k)
    if p is None:
        checks.append(_check("random", "무작위 진입 대비", FAIL, None, "진입 신호가 없어 비교할 수 없습니다"))
    else:
        st = PASS if p < 0.05 else (WARN if p < 0.10 else FAIL)
        checks.append(_check("random", "무작위 진입 대비", st, round(p, 4),
                             f"같은 신호 패턴을 무작위 시점으로 옮긴 {null_test['n']:,}회 중 모델 이상 성과 비율 p={p:.3f} "
                             f"(무작위 중앙 {null_test['median']:+.2f}%, 상위 5% {null_test['q95']:+.2f}%, 모델 {null_test['actual']:+.2f}%)"))
        st = PASS if p_adj < 0.05 else (WARN if p_adj < 0.20 else FAIL)
        why = (f"홀드아웃으로 트라이얼 {k}개 중 골랐으므로 우연히 하나쯤 좋을 확률을 보정: p_adj={p_adj:.3f}"
               if k > 1 else "홀드아웃이 후보 선택에 쓰이지 않아 보정 불필요")
        checks.append(_check("selection", "선택 편향 보정", st, round(p_adj, 4), why))

    # 3. 거래 t값
    n = len(trade_returns)
    t = _t_stat(trade_returns)
    if t is None:
        checks.append(_check("trade_t", "거래 단위 t값", FAIL, None, f"거래 {n}건 — 계산 불가"))
    else:
        st = PASS if (t >= 2.0 and n >= 20) else (WARN if t >= 1.0 else FAIL)
        note = " (표본 20건 미만)" if n < 20 else ""
        checks.append(_check("trade_t", "거래 단위 t값", st, round(t, 2),
                             f"거래 {n}건 평균 {np.mean(trade_returns):+.3f}%/건, t={t:.2f}{note} — 2 이상이면 통계적으로 의미"))

    # 4·5. WFA
    w = [float(x) for x in wfa_profits if x is not None]
    wt = _t_stat(w)
    if wt is None:
        checks.append(_check("wfa_t", "WFA 전진검증 일관성", WARN, None, "WFA 창이 부족합니다"))
    else:
        st = PASS if wt >= 2.0 else (WARN if wt >= 1.0 else FAIL)
        checks.append(_check("wfa_t", "WFA 전진검증 일관성", st, round(wt, 2),
                             f"창 {len(w)}개 평균 {np.mean(w):+.2f}% (플러스 {sum(1 for x in w if x > 0)}개), t={wt:.2f}"))
        best = max(w)
        rest = sum(w) - best
        share = best / sum(x for x in w if x > 0) if any(x > 0 for x in w) else 1.0
        st = PASS if (rest > 0 and share < 0.5) else (WARN if rest > 0 else FAIL)
        checks.append(_check("wfa_conc", "WFA 성과 집중도", st, round(share, 2),
                             f"최고 창 {best:+.2f}% 가 플러스 합의 {share * 100:.0f}% — 그 창을 빼면 {rest:+.2f}%"))

    # 6. 독립 OOS
    checks.append(_check("independent", "독립 홀드아웃", PASS if oos_independent else WARN,
                         bool(oos_independent),
                         "홀드아웃이 후보 선택과 분리됨" if oos_independent else
                         "홀드아웃이 후보 선택에도 쓰였습니다 — holdout_mode='split' 로 분리하면 편향 없는 성적을 얻습니다"))

    fails = [c for c in checks if c["status"] == FAIL]
    passes = [c for c in checks if c["status"] == PASS]
    core_ok = all(c["status"] == PASS for c in checks if c["key"] in ("selection", "trade_t"))
    if not fails and core_ok and len(passes) >= 4:
        verdict, label = "high", "신뢰 높음 — 실전 소액 투입 검토 가능"
    elif not fails:
        verdict, label = "medium", "가능성 있음 — 독립 검증·모의매매로 확인 필요"
    else:
        verdict, label = "low", "신뢰 낮음 — 우연과 구분되지 않습니다"
    return {"verdict": verdict, "label": label, "checks": checks}


def evaluate(config: dict, run_dir: str, rank: int, log_fn: Callable[[str], None] = lambda m: None,
             n_permutations: int = DEFAULT_PERMUTATIONS) -> Dict[str, Any]:
    """자동연구 실행 폴더의 rank 후보를 진단한다."""
    from CAiModelBaker.core.backtester import process_03_pure_backtest

    with open(os.path.join(run_dir, "leaderboard.json"), encoding="utf-8") as f:
        lb = json.load(f)
    item = next((x for x in lb.get("leaderboard", []) if int(x.get("rank", 0)) == int(rank)), None)
    if item is None:
        raise ValueError(f"리더보드에 {rank}위 후보가 없습니다")
    cand_dir = os.path.join(run_dir, "candidates", f"rank_{int(rank)}")
    if not os.path.exists(os.path.join(cand_dir, f"model_{config.get('INTERVAL', '1m')}.txt")):
        raise FileNotFoundError(f"후보 모델 파일이 없습니다: {cand_dir}")

    start, end = lb.get("final_test_start"), lb.get("final_test_end")
    log_fn(f"🧪 [신뢰도 진단] {rank}위 후보 — 홀드아웃 {start}~{end}, 무작위 진입 {n_permutations:,}회 재시뮬레이션")
    res = process_03_pure_backtest(config, start, end, log_fn=lambda m: None, session_id=None,
                                   model_dir=cand_dir, null_permutations=n_permutations)
    if not isinstance(res, dict) or "trade_logs" not in res:
        raise RuntimeError("홀드아웃 재시뮬레이션에 실패했습니다")

    trade_returns = [float(t.get("net_return", 0.0)) for t in res.get("trade_logs", [])]
    wfa = [w.get("profit") for w in (item.get("wfa_window_details") or [])]
    n_trials = len(glob.glob(os.path.join(run_dir, "trials", "trial_*.json"))) or 1
    oos_indep = bool(lb.get("oos_independent", False))
    summary = summarize(res.get("null_test"), trade_returns, wfa, n_trials, oos_indep)

    exits: Dict[str, int] = {}
    for t in res.get("trade_logs", []):
        exits[t.get("exit_type", "?")] = exits.get(t.get("exit_type", "?"), 0) + 1
    return {
        "rank": int(rank), "run": os.path.basename(run_dir.rstrip(os.sep)),
        "holdout": {"start": start, "end": end},
        "reproduced": {"net_profit": res.get("net_profit"), "trades": res.get("total_trades"),
                       "leaderboard_profit": item.get("final_profit"), "leaderboard_trades": item.get("final_trades")},
        "exit_types": exits, "n_selection_trials": n_trials, "oos_independent": oos_indep,
        "null_test": res.get("null_test"), "wfa_profits": wfa, **summary,
    }
