# AI Optimization Session: KODEX200_M1_1004A

- **생성 일시**: 2026-10-05 16:58:31
- **대상 자산**: 069500 (1m)
- **최종 챔피언**: Rank 1 (Trial 23)
- **챔피언 피처 (8개)**: `['target_rsi_curr', 'target_bb_kc_squeeze', 'target_mfi', 'target_rsi_daily', 'ref_10100_ret_5c', 'ref_000660_ret_5c', 'target_adx', 'ref_005930_ret_5c']`
- **Optuna Score**: 1.722398142037159
- **Validation Metrics**: {"score": 0.8933320339701427, "trades": 138, "required_trades": 10.0, "win_rate": 45.65217391304348, "mdd": 7.056472005315912, "profit_factor": 0.6407704787658709, "profit_factor_raw": 0.6407704787658709, "total_return": -6.28836338297083}
- **Final Test Metrics**: {"profit": 0.85, "trades": 57, "win_rate": 47.4, "mdd": 0.64, "profit_factor": 1.27}
- **Assessment**: PASSED

## 📂 Directory Structure

```
├── model/ (실전 배포용 챔피언 핵심 파일)
│   ├── model_1m.txt           (LightGBM 가중치 모델)
│   ├── best_params_1m.json    (최적 하이퍼파라미터 설정)
│   └── feature_spec.json       (기술 지표 전처리 명세서)
│
│   └── model_source.json       (출처: 이 챔피언을 만든 실행)
│
├── runs/ (자동연구 실행 기록 — 최근 10회 보관, 실행마다 폴더 1개)
│   └── 20261005_165222/  (챔피언 출처 실행)
│       ├── leaderboard.json · result.json · config_used.json
│       ├── train_run.log       (서버가 실행 중 직접 기록한 로그)
│       ├── trials/ · candidates/rank_N/
│       └── work/               (재학습 임시 산출물)
│
└── reports/
    └── test_trades.csv         (챔피언 테스트 구간 상세 거래 기록)
```

---
*본 폴더는 CAiModelBaker 엔진에 의해 자동 아카이빙된 최적화 세션 패키지입니다.*
