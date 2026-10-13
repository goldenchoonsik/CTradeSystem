import os
import json
import math
from typing import Dict, Any, List, Optional

class DataAnalyzer:
    """
    결정론적 수학/통계 기반 실시간 데이터 무결성 및 AI 추론 피드 분석기.
    AI/ML 없이 100% 순수 통계 및 규칙 기반으로 즉각적이고 오판 없는 무결성 검증을 수행합니다.
    """

    @staticmethod
    def analyze_records(records: List[Dict[str, Any]], expected_features: Optional[List[str]] = None) -> Dict[str, Any]:
        if not records:
            return {
                "health_score": 0.0,
                "status": "CRITICAL",
                "summary": "수집된 레코드가 없습니다.",
                "details": {}
            }

        total_records = len(records)
        start_ts = records[0]["local_ts"]
        end_ts = records[-1]["local_ts"]
        duration_sec = max(0.001, end_ts - start_ts)
        avg_tps = round(total_records / duration_sec, 2)

        # 1. 이벤트 타입별 카운트
        event_counts = {}
        latencies = []
        tick_prices = []
        tick_timestamps = []
        crossed_orderbooks = 0
        zero_or_negative_values = 0
        spikes = []
        nan_inf_features = 0
        missing_feature_counts = 0

        # TPS 피크 계산용 (1초 윈도우)
        second_buckets = {}

        last_tick_price = None
        last_tick_time = None
        tick_gaps = []

        for r in records:
            e_type = r.get("event_type", "unknown")
            event_counts[e_type] = event_counts.get(e_type, 0) + 1

            local_t = r.get("local_ts", 0.0)
            sec_key = int(local_t)
            second_buckets[sec_key] = second_buckets.get(sec_key, 0) + 1

            data = r.get("data", {})

            # 레이턴시 분석 (거래소 타임스탬프가 제공되는 경우)
            ex_ts = data.get("timestamp") or data.get("t") or data.get("trade_time")
            if ex_ts:
                # ms 단위 타임스탬프 정규화
                if ex_ts > 1e11:
                    ex_ts = ex_ts / 1000.0
                latency_ms = (local_t - ex_ts) * 1000.0
                if -1000.0 < latency_ms < 60000.0:  # 극단적 이상 시계 제외
                    latencies.append(latency_ms)

            # 호가 정합성 분석
            if e_type in ("orderbook", "ORDERBOOK") or "units" in data:
                units = data.get("units", [])
                if units:
                    best_bid = float(units[0].get("bid_price", 0) if isinstance(units[0], dict) else getattr(units[0], 'bid_price', 0))
                    best_ask = float(units[0].get("ask_price", 0) if isinstance(units[0], dict) else getattr(units[0], 'ask_price', 0))
                    if best_bid <= 0 or best_ask <= 0:
                        zero_or_negative_values += 1
                    elif best_bid >= best_ask:
                        crossed_orderbooks += 1

            # 체결/시세 데이터 분석
            if e_type in ("ticker", "TICK", "trade") or "price" in data:
                price = float(data.get("price", data.get("p", 0.0)))
                vol = float(data.get("volume", data.get("v", 0.0)))
                if price <= 0:
                    zero_or_negative_values += 1
                else:
                    tick_prices.append(price)
                    tick_timestamps.append(local_t)

                    # 틱 간 시간 갭(Gap) 검사
                    if last_tick_time is not None:
                        gap = local_t - last_tick_time
                        if gap > 3.0:  # 3초 이상 틱 공백
                            tick_gaps.append(round(gap, 3))
                    last_tick_time = local_t

                    # 가격 스파이크 검사 (직전 틱 대비 2% 이상 순간 괴리)
                    if last_tick_price is not None and last_tick_price > 0:
                        ret_pct = abs(price - last_tick_price) / last_tick_price * 100.0
                        if ret_pct >= 2.0:
                            spikes.append({"prev": last_tick_price, "curr": price, "delta_pct": round(ret_pct, 2)})
                    last_tick_price = price

            # AI 피처 벡터 유효성 검사 (피처 데이터가 포함된 경우)
            if "features" in data or "feature_vector" in data:
                feat_dict = data.get("features", data.get("feature_vector", {}))
                if isinstance(feat_dict, dict):
                    for k, v in feat_dict.items():
                        if v is None or math.isnan(float(v)) or math.isinf(float(v)):
                            nan_inf_features += 1
                    if expected_features:
                        for exp_f in expected_features:
                            if exp_f not in feat_dict:
                                missing_feature_counts += 1

        # 통계 지표 산출
        peak_tps = max(second_buckets.values()) if second_buckets else avg_tps
        
        # 레이턴시 통계
        if latencies:
            avg_lat = sum(latencies) / len(latencies)
            max_lat = max(latencies)
            min_lat = min(latencies)
            variance = sum((x - avg_lat) ** 2 for x in latencies) / len(latencies)
            jitter_ms = math.sqrt(variance)
        else:
            avg_lat = max_lat = min_lat = jitter_ms = 0.0

        # 감점제 기반 종합 헬스 점수 산출
        score = 100.0
        penalties = []

        if crossed_orderbooks > 0:
            p = min(30.0, crossed_orderbooks * 10.0)
            score -= p
            penalties.append(f"호가 역전(Crossed Orderbook) {crossed_orderbooks}회 발생 (-{p}점)")

        if zero_or_negative_values > 0:
            p = min(20.0, zero_or_negative_values * 5.0)
            score -= p
            penalties.append(f"0 이하 비정상 가격/수량 {zero_or_negative_values}회 감지 (-{p}점)")

        if nan_inf_features > 0:
            p = min(40.0, nan_inf_features * 15.0)
            score -= p
            penalties.append(f"AI 피처 NaN/Infinity 결측 {nan_inf_features}회 발생 (-{p}점)")

        if missing_feature_counts > 0:
            p = min(30.0, missing_feature_counts * 10.0)
            score -= p
            penalties.append(f"필수 AI 피처 스펙 불일치 {missing_feature_counts}회 (-{p}점)")

        if avg_lat > 200.0:
            score -= 15.0
            penalties.append(f"평균 수신 레이턴시 지연({avg_lat:.1f}ms > 200ms) (-15점)")

        if len(tick_gaps) > 0:
            p = min(20.0, len(tick_gaps) * 5.0)
            score -= p
            penalties.append(f"3초 초과 틱 수신 공백(Gap) {len(tick_gaps)}회 발생 (-{p}점)")

        if len(spikes) > 0:
            p = min(15.0, len(spikes) * 5.0)
            score -= p
            penalties.append(f"2% 초과 순간 틱 스파이크 {len(spikes)}회 감지 (-{p}점)")

        score = max(0.0, round(score, 1))

        if score >= 95.0:
            status = "EXCELLENT"
        elif score >= 85.0:
            status = "GOOD"
        elif score >= 70.0:
            status = "WARNING"
        else:
            status = "CRITICAL"

        return {
            "health_score": score,
            "status": status,
            "summary": {
                "total_records": total_records,
                "duration_sec": round(duration_sec, 2),
                "avg_tps": avg_tps,
                "peak_tps": peak_tps,
                "event_distribution": event_counts,
            },
            "stream_integrity": {
                "avg_latency_ms": round(avg_lat, 2),
                "max_latency_ms": round(max_lat, 2),
                "min_latency_ms": round(min_lat, 2),
                "jitter_ms": round(jitter_ms, 2),
                "crossed_orderbooks": crossed_orderbooks,
                "zero_or_negative_values": zero_or_negative_values,
                "tick_gaps_count": len(tick_gaps),
                "max_tick_gap_sec": max(tick_gaps) if tick_gaps else 0.0,
                "price_spikes_count": len(spikes)
            },
            "inference_feed_health": {
                "nan_inf_features_detected": nan_inf_features,
                "missing_feature_counts": missing_feature_counts,
                "feed_ready": (nan_inf_features == 0 and missing_feature_counts == 0 and score >= 70.0)
            },
            "penalties": penalties
        }

    @classmethod
    def analyze_file(cls, filepath: str, expected_features: Optional[List[str]] = None) -> Dict[str, Any]:
        """저장된 덤프 파일(JSONL)을 로드하여 정밀 분석을 수행합니다."""
        if not os.path.exists(filepath):
            return {
                "health_score": 0.0,
                "status": "CRITICAL",
                "summary": f"덤프 파일을 찾을 수 없습니다: {filepath}",
                "details": {}
            }

        records = []
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        except Exception as e:
            return {
                "health_score": 0.0,
                "status": "CRITICAL",
                "summary": f"덤프 파일 파싱 에러: {e}",
                "details": {}
            }

        res = cls.analyze_records(records, expected_features)
        res["source_file"] = os.path.basename(filepath)
        return res
