# AI Optimization Session: SAMSUNG_M1_BEST1

- **생성 일시**: 2026-10-07 03:47:53
- **대상 자산**: 005930 (1m)
- **최종 챔피언**: Rank 1 (Trial 3)
- **챔피언 피처 (4개)**: `['ref_10100_spread_vs_target', 'target_vwap_disparity', 'target_bb_pctb', 'ref_10100_ret_1c']`
- **Optuna Score**: 1.6748618094450745
- **Validation Metrics**: {"score": 1.6586405225300687, "trades": 73, "required_trades": 10.0, "win_rate": 49.31506849315068, "mdd": 3.9701174766672813, "profit_factor": 1.15147526925768, "profit_factor_raw": 1.15147526925768, "total_return": 2.4339878514653135}
- **Final Test Metrics**: {"profit": 3.11, "trades": 103, "win_rate": 57.3, "mdd": 1.59, "profit_factor": 1.26}
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
│   └── 20261007_034515/  (챔피언 출처 실행)
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
