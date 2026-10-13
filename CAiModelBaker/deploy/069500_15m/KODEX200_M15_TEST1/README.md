# AI Optimization Session: KODEX200_M15_TEST1

- **생성 일시**: 2026-10-07 23:45:50
- **대상 자산**: 069500 (15m)
- **최종 챔피언**: Rank 1 (Trial 14)
- **챔피언 피처 (9개)**: `['target_adx', 'target_rsi_curr', 'target_volatility_ratio', 'target_macd_hist', 'parkinson_rv_squeeze', 'mtf_ema_distance_4h', 'target_bb_kc_squeeze', 'target_mfi', 'ref_us_500_ret_5c']`
- **Optuna Score**: -14.862904246282682
- **Validation Metrics**: {"score": 1.1440455256225452, "trades": 21, "required_trades": 10.0, "win_rate": 52.38095238095239, "mdd": 7.233395499109907, "profit_factor": 0.8221676552518193, "profit_factor_raw": 0.8221676552518193, "total_return": -2.540443672814263}
- **Final Test Metrics**: {"profit": 4.98, "trades": 13, "win_rate": 76.9, "mdd": 1.55, "profit_factor": 3.07}
- **Assessment**: PASSED

## 📂 Directory Structure

```
├── model/ (실전 배포용 챔피언 핵심 파일)
│   ├── model_15m.txt           (LightGBM 가중치 모델)
│   ├── best_params_15m.json    (최적 하이퍼파라미터 설정)
│   └── feature_spec.json       (기술 지표 전처리 명세서)
│
│   └── model_source.json       (출처: 이 챔피언을 만든 실행)
│
├── runs/ (자동연구 실행 기록 — 최근 10회 보관, 실행마다 폴더 1개)
│   └── 20261007_234418/  (챔피언 출처 실행)
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
