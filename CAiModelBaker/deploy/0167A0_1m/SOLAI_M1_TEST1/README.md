# AI Optimization Session: SOLAI_M1_TEST1

- **생성 일시**: 2026-10-06 21:26:10
- **대상 자산**: 0167A0 (1m)
- **최종 챔피언**: Rank 1 (Trial 14)
- **챔피언 피처 (9개)**: `['ref_10100_ret_1c', 'ref_000660_ret_1c', 'target_ma_ratio', 'target_rsi_curr', 'target_adx', 'target_mfi', 'target_bb_kc_squeeze', 'ref_10100_spread_vs_target', 'target_rsi_daily']`
- **Optuna Score**: 1.6587613512757529
- **Validation Metrics**: {"score": 1.4552914702011315, "trades": 38, "required_trades": 10.0, "win_rate": 52.63157894736842, "mdd": 2.5645202526424686, "profit_factor": 0.9957300112680287, "profit_factor_raw": 0.9957300112680287, "total_return": -0.04280075510384451}
- **Final Test Metrics**: {"profit": 0.59, "trades": 141, "win_rate": 41.1, "mdd": 2.46, "profit_factor": 1.07}
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
│   └── 20261006_212100/  (챔피언 출처 실행)
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
