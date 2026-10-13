from typing import Dict, Any

class DataReporter:
    """
    DataAnalyzer 결과를 CLI 및 로그에서 시각적으로 확인하기 위한 포맷터
    """

    @staticmethod
    def format_console_report(report: Dict[str, Any], symbol: str = "UNKNOWN") -> str:
        score = report.get("health_score", 0.0)
        status = report.get("status", "UNKNOWN")
        summary = report.get("summary", {})
        stream = report.get("stream_integrity", {})
        inf = report.get("inference_feed_health", {})
        penalties = report.get("penalties", [])

        lines = [
            "=" * 78,
            f"  [CTrade] 실시간 데이터 & AI 추론 피드 무결성 진단 리포트 (종목: {symbol})",
            "=" * 78,
            f" * 종합 건전성 점수 : {score:.1f} / 100.0   [판정: {status}]",
            f" * 총 수집 레코드   : {summary.get('total_records', 0):,} 건 ({summary.get('duration_sec', 0)} 초)",
            f" * 처리량 (TPS)     : 평균 {summary.get('avg_tps', 0)} TPS / 피크 {summary.get('peak_tps', 0)} TPS",
            "-" * 78,
            " [1] 실시간 스트림 무결성 (Stream Health)",
            f"   - 수신 레이턴시   : 평균 {stream.get('avg_latency_ms', 0):.1f} ms (최대 {stream.get('max_latency_ms', 0):.1f} ms, 지터 {stream.get('jitter_ms', 0):.1f} ms)",
            f"   - 호가 역전(Cross): {stream.get('crossed_orderbooks', 0)} 회 ('매수가 >= 매도가' 오류)",
            f"   - 0/음수 비정상값 : {stream.get('zero_or_negative_values', 0)} 회",
            f"   - 틱 수신 공백    : {stream.get('tick_gaps_count', 0)} 회 (최대 공백: {stream.get('max_tick_gap_sec', 0):.2f} 초)",
            f"   - 가격 스파이크   : {stream.get('price_spikes_count', 0)} 회 (2% 초과 급변)",
            "-" * 78,
            " [2] AI 추론 피드 안전성 (Inference Feed Readiness)",
            f"   - NaN / Inf 결측  : {inf.get('nan_inf_features_detected', 0)} 건",
            f"   - 필수 피처 누락  : {inf.get('missing_feature_counts', 0)} 건",
            f"   - AI 추론 가능여부: {'✅ SAFE TO TRADE' if inf.get('feed_ready') else '❌ BLOCKED (DATA UNSTABLE)'}",
        ]

        if penalties:
            lines.append("-" * 78)
            lines.append(" [3] 감점 및 경고 상세 내역")
            for p in penalties:
                lines.append(f"   ⚠️  {p}")

        lines.append("=" * 78)
        return "\n".join(lines)
