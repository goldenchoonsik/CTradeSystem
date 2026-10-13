import os
import json
import time
import sys
import calendar
import argparse

default_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def parse_args():
    parser = argparse.ArgumentParser(description="CTrade Auto Performance Analyser")
    parser.add_argument("--duration-sec", type=int, default=21600, help="Analysis lookback duration in seconds (default: 6 hours)")
    return parser.parse_args()

def _num(v, default=0.0):
    """None/비수치 값을 안전하게 float 로 변환"""
    try:
        if v is None or isinstance(v, bool):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default

def load_json_file(file_path):
    if not os.path.exists(file_path):
        return []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Analyser] Failed to load JSON from {file_path}: {e}")
        return []

def read_current_version(project_root: str) -> str:
    """trading_config.json 에서 현재 전략 세션 VERSION 태그를 읽어옵니다."""
    config_path = os.path.join(project_root, "config", "trading_config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("GLOBAL_STRATEGY", {}).get("VERSION", "v1.3.0_opt001")
    except Exception:
        return "v1.3.0_opt001"

def main():
    args = parse_args()
    lookback = args.duration_sec
    now = time.time()
    cutoff_time = now - lookback

    project_root = default_project_root
    history_path = os.path.join(project_root, "spike_verification_history.json")
    report_path = os.path.join(project_root, "scripts", "analysis_report.md")
    opt_history_path = os.path.join(project_root, "config", "optimization_history.json")

    os.makedirs(os.path.dirname(report_path), exist_ok=True)

    current_version = read_current_version(project_root)
    _oh = load_json_file(opt_history_path)
    opt_history = [h for h in _oh if isinstance(h, dict)] if isinstance(_oh, list) else []

    records = load_json_file(history_path)
    # JSON 이 dict 인 경우: 값이 list 면 그것을, 아니면 dict 값들 중 dict 레코드만 사용
    if isinstance(records, dict):
        if isinstance(records.get("records"), list):
            records = records["records"]
        else:
            records = [v for v in records.values() if isinstance(v, dict)]
    if not isinstance(records, list):
        records = []
    records = [r for r in records if isinstance(r, dict)]
    filtered = []

    for r in records:
        # timestamp 파싱 (UTC 기준 '%Y-%m-%d %H:%M:%S')
        ts_str = r.get("timestamp")
        if not ts_str:
            continue
        try:
            struct_time = time.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            # UTC 문자열을 UTC 기준 epoch 로 정확 변환 (로컬 TZ/DST 무관, L-5)
            record_epoch = calendar.timegm(struct_time)
            if record_epoch >= cutoff_time:
                filtered.append(r)
        except Exception:
            continue

    # 버전별 성과 분류
    version_map: dict[str, list] = {}
    for r in filtered:
        ver = r.get("version", "unknown")
        version_map.setdefault(ver, []).append(r)

    total_events = len(filtered)
    successes = sum(1 for r in filtered if r.get("success_30s") is True)
    failures = sum(1 for r in filtered if r.get("success_30s") is False and _num(r.get("return_30s")) != 0.0)
    stagnants = sum(1 for r in filtered if _num(r.get("return_30s")) == 0.0)

    win_rate = (successes / total_events * 100) if total_events > 0 else 0.0
    avg_return = sum(_num(r.get("return_30s")) for r in filtered) / total_events if total_events > 0 else 0.0
    max_high_val = max([_num(r.get("max_high")) for r in filtered]) if filtered else 0.0
    max_dd_val = min([_num(r.get("max_drawdown")) for r in filtered]) if filtered else 0.0

    # 손실/오발령이 집중된 최악의 3대 종목 분석
    symbol_performance = {}
    for r in filtered:
        sym = r.get("symbol")
        ret = _num(r.get("return_30s"))
        if sym not in symbol_performance:
            symbol_performance[sym] = {"total_ret": 0.0, "count": 0, "failures": 0}
        symbol_performance[sym]["total_ret"] += ret
        symbol_performance[sym]["count"] += 1
        if ret < 0.0:
            symbol_performance[sym]["failures"] += 1

    worst_symbols = sorted(
        [{"symbol": k, "avg_ret": v["total_ret"]/v["count"], "failures": v["failures"]} for k, v in symbol_performance.items()],
        key=lambda x: x["avg_ret"]
    )[:3]

    # 최적화 이력 요약 (최근 5건)
    recent_history = opt_history[-5:] if len(opt_history) >= 5 else opt_history
    history_md_rows = ""
    for h in recent_history:
        history_md_rows += (
            f"| {h.get('timestamp_utc', '-')} | {h.get('from_version', '-')} | "
            f"{h.get('to_version', '-')} | {h.get('status', '-')} | {h.get('note', '-')} |\n"
        )
    if not history_md_rows:
        history_md_rows = "| - | - | - | - | 이력 없음 (첫 세션) |\n"

    # 버전별 성과 요약 테이블
    version_perf_md = ""
    for ver, ver_records in sorted(version_map.items()):
        v_total = len(ver_records)
        v_win = sum(1 for r in ver_records if r.get("success_30s"))
        v_wr = (v_win / v_total * 100) if v_total > 0 else 0.0
        v_avg = sum(_num(r.get("return_30s")) for r in ver_records) / v_total if v_total > 0 else 0.0
        version_perf_md += f"| `{ver}` | {v_total} | {v_win} | {v_wr:.1f}% | {v_avg:+.3f}% |\n"
    if not version_perf_md:
        version_perf_md = "| - | - | - | - | - |\n"

    # 마크다운 리포트 생성
    report_content = f"""# 📊 CTrade 자율 성과 분석 보고서 (자동 생성)
* **생성 시간 (UTC)**: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}
* **생성 시간 (KST)**: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(time.time() + 9 * 3600))}
* **현재 전략 세션**: `{current_version}`
* **분석 대상 윈도우**: 최근 {lookback}초 ({lookback/3600:.1f} 시간)

---

## 📈 1. 단타 스캘핑 정량 성과 지표
| 지표명 | 수치 | 비고 |
| :--- | :--- | :--- |
| **총 스파이크 감지 횟수** | {total_events} 건 | 1차 Target Radar 감지 횟수 |
| **성공 횟수 (success_30s)** | {successes} 건 | 30초 내에 0.05% 이상 상승 |
| **보합 횡보 횟수 (0.0%)** | {stagnants} 건 | 거래 정체 및 호가 무반응 |
| **손실 청산 횟수 (< 0.0%)** | {failures} 건 | 가짜 돌파 및 역세 추세 급락 |
| **사전 필터링 승률 (Win Rate)** | {win_rate:.2f} % | Target Radar 정밀도 |
| **평균 30초 수익률** | {avg_return:+.3f} % | 수수료 보전 여부 체크 기준 |
| **평가 기간 내 최고 상승률** | {max_high_val:+.3f} % | 스파이크 돌파 잠재력 |
| **평가 기간 내 최대 낙폭 (MDD)** | {max_dd_val:+.3f} % | 리스크 한계치 제어 지표 |

---

## 🔢 2. 전략 세션별 성과 비교 (Version Breakdown)
이 분석 윈도우에서 실행된 버전별 데이터를 비교합니다. 버전 정보가 누락된 레코드는 'unknown' 으로 분류됩니다.

| 버전 | 감지 건수 | 성공 | 승률 | 평균 30s 수익률 |
| :--- | :---: | :---: | :---: | :---: |
{version_perf_md}
---

## 🕒 3. 최근 최적화 롤오버 이력 (Recent 5)
| 발생 시각 (UTC) | 변경 전 | 변경 후 | 상태 | 비고 |
| :--- | :--- | :--- | :--- | :--- |
{history_md_rows}
---

## 🚫 4. 집중 개선 대상 3대 종목 (Worst 3 Assets)
이 종목들은 1차 돌파 후 가격 상승이 부재하거나 흘러내림(Tick Drift)이 심각하여 우선적으로 진입 억제 조건이나 청산 룰 보정이 필요합니다.

| 순위 | 종목 코드 | 평균 30초 수익률 | 손실 횟수 | 개선 가이드 |
| :---: | :--- | :---: | :---: | :--- |
"""

    for i, w in enumerate(worst_symbols):
        guideline = "3초 가격 모멘텀 필터(MIN_PRICE_SLOPE_3S) 상향 조정 필요"
        if w["avg_ret"] < -0.3:
            guideline = "스프레드 한도 필터(SPREAD_FILTER_MAX_PCT) 하향 및 해외선물 Intensity 조건 강화 필요"
        report_content += f"| {i+1} | {w['symbol']} | {w['avg_ret']:+.3f}% | {w['failures']} 회 | {guideline} |\n"

    if not worst_symbols:
        report_content += "| - | - | - | - | 특이 손실 자산 없음 |\n"

    report_content += f"""
---

## 🧠 5. 안티그라비티 에이전트 자율 진화 가이드
* **현재 실행 세션**: `{current_version}`
* **행동 지침**: 
  - 만약 **승률(Win Rate)이 35% 미만**이거나 **평균 30초 수익률이 마이너스**인 경우, `core/scalping_target_radar.py`의 1차 탐지 필터가 너무 느슨함을 의미합니다.
  - 이 경우 즉시 `config/trading_config.json`을 열어 `MIN_PRICE_SLOPE_3S` 임계값을 `0.01` 단위로 상향(예: `0.05%` -> `0.06%`)하거나, `DETECTOR_ENTRY_RVOL`을 상향 조율하십시오.
  - 만약 **손실 청산 횟수가 보합보다 현저히 높은 경우**, `SIMULTANEOUS_SPIKE_LIMIT`을 `2`로 좁혀 대규모 노이즈 덤핑을 조기에 차단하십시오.
  - 수정 완료 후에는 반드시 `scripts/cycle_manager.py --action rollover` 를 통해 VERSION을 인상하고 재기동하십시오.
"""

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)

    print(f"[Analyser] Automated analysis report successfully written to {report_path}")

if __name__ == "__main__":
    main()
