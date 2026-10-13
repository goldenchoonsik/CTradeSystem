# AI Optimization Session: KODEX200_S10_BEST260921

- **생성 일시**: 2026-09-21 16:06:55
- **대상 자산**: 069500 (10s)
- **최종 챔피언**: Rank 1 (Trial 21)
- **챔피언 피처 (7개)**: `['target_vwap_disparity', 'target_macd_hist', 'overnight_gap_pct', 'target_ofi_cumsum', 'target_is_auction', 'target_hour_cos', 'krx_session_time']`
- **Optuna Score**: 19.752704
- **Validation Metrics**: {"score": 14.7752, "trades": 24, "win_rate": 75.0, "mdd": 0.26, "profit_factor": 9.88, "total_return": 2.69}
- **Final Test Metrics**: {"profit": 4.05, "trades": 46, "win_rate": 78.26, "mdd": 0.21, "profit_factor": 6.57, "sharpe_ratio": 4.76}
- **Assessment**: PASSED

## 📂 Directory Structure

```
├── model/ (실전 배포용 챔피언 핵심 파일)
│   ├── model_10s.txt           (LightGBM 가중치 모델)
│   ├── best_params_10s.json    (최적 하이퍼파라미터 설정)
│   └── feature_spec.json       (기술 지표 전처리 명세서)
│
├── candidates/ (Top-3 후보군 영구 보존)
│   ├── rank_1/                 (Rank 1 모델 및 명세서)
│   ├── rank_2/                 (Rank 2 모델 및 명세서)
│   └── rank_3/                 (Rank 3 모델 및 명세서)
│
└── reports/ (검증 및 기록 보조 파일)
    ├── train_run.log           (학습 실행 콘솔 로그)
    └── test_trades.csv         (챔피언 테스트 구간 상세 거래 기록)
```

---
*본 폴더는 CAiModelBaker 엔진에 의해 자동 아카이빙된 최적화 세션 패키지입니다.*
