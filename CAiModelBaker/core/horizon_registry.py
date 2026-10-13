"""
CAiModelBaker - Target Horizon & Max Holding Registry
인터벌(시간봉)별 표준 사정거리 후보군 및 권장 고정값 정의
"""
from typing import Dict, List, Any


HORIZON_REGISTRY: Dict[str, Dict[str, Any]] = {
    # 10초봉 (초단기 틱/호가 마이크로 모델)
    "10s": {
        "recommended_predict_ahead": 18,  # 3분 (수급 모멘텀)
        "recommended_max_hold": 18,
        "candidates": [
            {"candles": 6, "minutes": 1.0, "name": "초단기 체결 펄스 (1분)"},
            {"candles": 18, "minutes": 3.0, "name": "수급 모멘텀 (3분, 추천★)"},
            {"candles": 36, "minutes": 6.0, "name": "호가 흡수 파동 (6분)"},
            {"candles": 60, "minutes": 10.0, "name": "마이크로 세션 추세 (10분)"}
        ]
    },
    # 1분봉 (초단기 인트라데이)
    "1m": {
        "recommended_predict_ahead": 6,  # 6분 (표준 모멘텀)
        "recommended_max_hold": 6,
        "candidates": [
            {"candles": 3, "minutes": 3.0, "name": "체결 스파이크 (3분)"},
            {"candles": 6, "minutes": 6.0, "name": "표준 모멘텀 (6분, 추천★)"},
            {"candles": 15, "minutes": 15.0, "name": "단기 스윙 전환 (15분)"},
            {"candles": 30, "minutes": 30.0, "name": "일중 마이크로 사이클 (30분)"}
        ]
    },
    # 3분봉
    "3m": {
        "recommended_predict_ahead": 5,  # 15분
        "recommended_max_hold": 5,
        "candidates": [
            {"candles": 3, "minutes": 9.0, "name": "단기 펄스 (9분)"},
            {"candles": 5, "minutes": 15.0, "name": "표준 수급 모멘텀 (15분, 추천★)"},
            {"candles": 10, "minutes": 30.0, "name": "단기 파동 (30분)"},
            {"candles": 20, "minutes": 60.0, "name": "시간봉 세션 추세 (1시간)"}
        ]
    },
    # 5분봉
    "5m": {
        "recommended_predict_ahead": 6,  # 30분
        "recommended_max_hold": 6,
        "candidates": [
            {"candles": 3, "minutes": 15.0, "name": "단기 펄스 (15분)"},
            {"candles": 6, "minutes": 30.0, "name": "표준 수급 모멘텀 (30분, 추천★)"},
            {"candles": 12, "minutes": 60.0, "name": "시간봉 세션 추세 (1시간)"},
            {"candles": 24, "minutes": 120.0, "name": "2시간 반일봉 추세 (2시간)"}
        ]
    },
    # 10분봉 (비트코인 데이트레이딩 표준)
    "10m": {
        "recommended_predict_ahead": 6,  # 1시간 (60분)
        "recommended_max_hold": 6,
        "candidates": [
            {"candles": 3, "minutes": 30.0, "name": "초단기 호가/체결 모멘텀 (30분)"},
            {"candles": 6, "minutes": 60.0, "name": "표준 수급 모멘텀 (1시간, 추천★)"},
            {"candles": 12, "minutes": 120.0, "name": "세션 추세 추종 (2시간)"},
            {"candles": 24, "minutes": 240.0, "name": "일중 거시 파동 (4시간)"}
        ]
    },
    # 15분봉
    "15m": {
        "recommended_predict_ahead": 4,  # 1시간
        "recommended_max_hold": 4,
        "candidates": [
            {"candles": 2, "minutes": 30.0, "name": "초단기 펄스 (30분)"},
            {"candles": 4, "minutes": 60.0, "name": "표준 모멘텀 (1시간, 추천★)"},
            {"candles": 8, "minutes": 120.0, "name": "세션 파동 (2시간)"},
            {"candles": 16, "minutes": 240.0, "name": "일중 거시 추세 (4시간)"}
        ]
    },
    # 30분봉
    "30m": {
        "recommended_predict_ahead": 4,  # 2시간
        "recommended_max_hold": 4,
        "candidates": [
            {"candles": 2, "minutes": 60.0, "name": "단기 수급 (1시간)"},
            {"candles": 4, "minutes": 120.0, "name": "세션 모멘텀 (2시간, 추천★)"},
            {"candles": 8, "minutes": 240.0, "name": "일중 거시 추세 (4시간)"},
            {"candles": 16, "minutes": 480.0, "name": "일봉 스윙 (8시간)"}
        ]
    },
    # 1시간봉 (1h / 60m)
    "1h": {
        "recommended_predict_ahead": 4,  # 4시간
        "recommended_max_hold": 4,
        "candidates": [
            {"candles": 2, "minutes": 120.0, "name": "세션 수급 (2시간)"},
            {"candles": 4, "minutes": 240.0, "name": "일중 추세 (4시간, 추천★)"},
            {"candles": 8, "minutes": 480.0, "name": "일봉 스윙 (8시간)"},
            {"candles": 24, "minutes": 1440.0, "name": "일간 거시 사이클 (24시간)"}
        ]
    },
    "60m": {
        "recommended_predict_ahead": 4,  # 4시간
        "recommended_max_hold": 4,
        "candidates": [
            {"candles": 2, "minutes": 120.0, "name": "세션 수급 (2시간)"},
            {"candles": 4, "minutes": 240.0, "name": "일중 추세 (4시간, 추천★)"},
            {"candles": 8, "minutes": 480.0, "name": "일봉 스윙 (8시간)"},
            {"candles": 24, "minutes": 1440.0, "name": "일간 거시 사이클 (24시간)"}
        ]
    },
    # 일봉 (1d - 중기 스윙/포지션 모델)
    "1d": {
        "recommended_predict_ahead": 5,  # 5거래일 (1주일 스윙)
        "recommended_max_hold": 10,      # 10거래일 (2주일 스윙)
        "candidates": [
            {"candles": 2, "minutes": 2880.0, "name": "단기 펄스 (2일)"},
            {"candles": 5, "minutes": 7200.0, "name": "주간 스윙 모멘텀 (5일/1주, 추천★)"},
            {"candles": 10, "minutes": 14400.0, "name": "2주 추세 추종 (10일/2주)"},
            {"candles": 20, "minutes": 28800.0, "name": "월간 사이클 추세 (20일/1개월)"}
        ]
    }
}


def get_horizon_context(interval_str: str) -> Dict[str, Any]:
    """
    주어진 인터벌 문자열에 대한 표준 사정거리 설정 컨텍스트를 반환합니다.
    미지원 인터벌의 경우 10m 또는 가장 가까운 설정을 폴백으로 제공합니다.
    """
    clean_intv = (interval_str or "10m").strip().lower()
    if clean_intv in HORIZON_REGISTRY:
        info = HORIZON_REGISTRY[clean_intv]
    else:
        info = HORIZON_REGISTRY["10m"]
        
    candidate_candle_list = [c["candles"] for c in info["candidates"]]
    
    return {
        "interval": clean_intv,
        "mode": "locked",
        "locked_predict_ahead": info["recommended_predict_ahead"],
        "locked_max_hold": info["recommended_max_hold"],
        "candidate_horizons": candidate_candle_list,
        "candidate_details": info["candidates"]
    }
