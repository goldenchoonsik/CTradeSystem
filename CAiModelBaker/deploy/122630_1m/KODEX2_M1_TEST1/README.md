# AI Optimization Session: KODEX2_M1_TEST1

- **생성 일시**: 2026-10-09 11:56:43
- **대상 자산**: 122630 (1m)
- **최종 챔피언**: Rank 1 (Trial 3)
- **챔피언 피처 (4개)**: `['target_rsi_daily', 'target_adx', 'target_ma_ratio', 'ref_000660_ret_5c']`
- **Optuna Score**: 1.5473579929294612
- **Validation Metrics**: {"score": 1.6020595927428563, "trades": 13, "required_trades": 10.0, "win_rate": 53.84615384615385, "mdd": 2.3453729975011957, "profit_factor": 1.093690858568236, "profit_factor_raw": 1.093690858568236, "total_return": 0.30321418687042634}
- **Final Test Metrics**: {"profit": 1.89, "trades": 67, "win_rate": 53.7, "mdd": 2.40, "profit_factor": 1.21}
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
│   └── 20261009_114611/  (챔피언 출처 실행)
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
