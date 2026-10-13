# -*- coding: utf-8 -*-
"""
CTradeSystem - Regime Feature Stability Audit (체제 지표 블록 일치도 감사)
==========================================================================
[T-8] 배경
----------
체제/컨텍스트 지표(시각·세션 구간 등)는 **선형 IC 로 평가할 수 없다**. 순위 IC 는 단조성을
가정하는데 `target_hour_sin/cos` 는 주기형이고 세션 시간대 효과는 U 자형이다. 그래서
연구 루프는 이 지표들에게 IC 평가를 **면제**해 왔다 — 즉 아무 검정도 하지 않았다.

실측 (2026-09-28, 069500 10s, 93,628행 / 40거래일)
---------------------------------------------------
  면제가 위험한 이유는 "IC 가 낮다"가 아니라 **표본이 크면 무엇이든 유의해진다**는 데 있다.
  단조성을 가정하지 않는 집단차 검정으로 바꿔 보면:
      target_hour_cos                 순위 IC -0.0024  →  집단차 p = 2.1e-11
      binance_funding_settle_window   순위 IC -0.0079  →  집단차 p = 7.7e-21   (KRX ETF 인데!)
  바이낸스 펀딩 정산창 지표가 국내 ETF 에서 가장 강한 p 를 냈다. 유의성은 증거가 아니다.
  같은 데이터에서 시각 슬롯별 평균 전방수익률을 전·후반으로 나눠 대조하면 순위상관 ρ=-0.13,
  부호 일치율 52.5%(동전던지기 50%)로 **구간을 넘어 하나도 유지되지 않았다**.
  그래서 '모양에 맞는 검정'으로 게이트를 바꾸면 오히려 시계 모델에 도장을 찍어준다.

판정 원리 (조정할 문턱을 두지 않는다)
--------------------------------------
- 지표값을 **집단**으로 본다(이산형은 값 그대로, 연속형은 분위수 구간). 단조성·주기성을
  가정하지 않는다.
- 표본을 시간 순서대로 **블록**으로 쪼개고, 블록마다 집단별 평균 전방수익률을 잰다.
- 블록끼리 그 집단 순위가 **일치하는지**를 본다. 예측 구조라면 블록이 바뀌어도 순위가 남고,
  과최적화된 잡음이라면 블록마다 뒤바뀐다.
- 합격선은 손으로 정하지 않는다. **라벨을 섞어** 같은 계산을 반복해 얻은 분포의 상위 분위수를
  잡음바닥으로 쓴다(표본 수·집단 수·블록 수가 자동 반영된다).
"""

import numpy as np
import pandas as pd

# 판정에 필요한 최소 조건 — 못 채우면 '판정 불가'이고, 판정 불가는 통과가 아니다
MIN_ROWS_PER_GROUP = 30
MIN_GROUPS = 2
# 블록 수는 합격선이 아니라 **해상도**를 정한다. 블록이 적으면 라벨섞기 분포가 거칠어져
# (이진 지표 × 4블록이면 우연히 전부 일치할 확률이 12.5% > 5%) 어떤 지표도 통과할 수 없다.
# 8블록이면 이진 지표도 판정 가능하고, 40거래일 기준 블록당 5거래일이 남는다.
DEFAULT_BLOCKS = 8
DEFAULT_MAX_GROUPS = 10
DEFAULT_SHUFFLES = 200
NOISE_PERCENTILE = 95.0

VERDICT_STABLE = "STABLE"
VERDICT_UNSTABLE = "UNSTABLE"
VERDICT_INDETERMINATE = "INDETERMINATE"


class RegimeStabilityFinding:
    """한 지표의 판정 결과."""

    __slots__ = ("feature", "verdict", "agreement", "noise_floor", "blocks",
                 "n_groups", "n_samples", "reason")

    def __init__(self, feature, verdict, agreement=float("nan"), noise_floor=float("nan"),
                 blocks=0, n_groups=0, n_samples=0, reason=""):
        self.feature = feature
        self.verdict = verdict
        self.agreement = agreement
        self.noise_floor = noise_floor
        self.blocks = blocks
        self.n_groups = n_groups
        self.n_samples = n_samples
        self.reason = reason

    @property
    def stable(self) -> bool:
        return self.verdict == VERDICT_STABLE

    def describe(self) -> str:
        if self.verdict == VERDICT_INDETERMINATE:
            return f"{self.feature}: 판정 불가 — {self.reason}"
        verdict_ko = "구간 넘어 유지" if self.stable else "구간마다 뒤바뀜"
        return (f"{self.feature}: {verdict_ko} (블록간 일치도 {self.agreement:+.3f} / "
                f"잡음바닥 {self.noise_floor:+.3f}, 블록 {self.blocks}개, 집단 {self.n_groups}개, "
                f"표본 {self.n_samples:,}개)")

    def to_dict(self) -> dict:
        return {"feature": self.feature, "verdict": self.verdict,
                "agreement": None if self.agreement != self.agreement else round(self.agreement, 4),
                "noise_floor": None if self.noise_floor != self.noise_floor else round(self.noise_floor, 4),
                "blocks": self.blocks, "n_groups": self.n_groups,
                "n_samples": self.n_samples, "reason": self.reason}


def _as_groups(values: pd.Series, max_groups: int) -> pd.Series:
    """지표값을 집단 라벨로 바꾼다 (이산형은 값 그대로, 연속형은 분위수 구간)."""
    if values.nunique() <= max_groups:
        return values.astype("category")
    try:
        return pd.qcut(values, max_groups, duplicates="drop")
    except (ValueError, IndexError):
        return values.rank(method="dense").astype("category")


def _block_group_means(groups: pd.Series, returns: pd.Series, block_ids: pd.Series,
                       n_blocks: int) -> pd.DataFrame:
    """블록 × 집단 평균 전방수익률 표. 표본이 얕은 칸은 비운다."""
    frame = pd.DataFrame({"_g": groups, "_r": returns, "_b": block_ids})
    agg = frame.groupby(["_b", "_g"], observed=True)["_r"].agg(["mean", "size"])
    agg = agg[agg["size"] >= MIN_ROWS_PER_GROUP]["mean"]
    return agg.unstack("_g")


def _out_of_block_agreement(table: pd.DataFrame) -> float:
    """블록 밖에서 배운 집단 효과가 그 블록에서도 맞는지 재는 단일 통계량.

    블록 쌍마다 스피어만을 재는 방식은 **집단 수가 적으면 무너진다** — 집단이 2개면 두 점의
    순위상관이 항상 ±1 이라 라벨섞기 잡음바닥이 +1.0 이 되고, 이진 지표는 원리적으로 통과할
    수 없다(실제로 `target_is_auction` 이 그렇게 막혔다). 그래서 블록×집단 전체를 한 번에 본다.

      1) 블록마다 집단 평균에서 **그 블록의 평균을 뺀다** (블록 전체가 오르내린 효과 제거 —
         남는 것은 순수한 집단 간 차이다).
      2) 각 (블록 b, 집단 g) 칸에 대해 **b 를 뺀 나머지 블록**의 값으로 기대치를 만든다.
      3) (기대치, 실제) 쌍 전체의 순위상관을 낸다.

    구조가 실재하면 블록 밖에서 배운 효과가 그 블록에서도 맞아 상관이 양수로 남고, 과최적화된
    잡음이라면 블록마다 뒤집혀 0 근처가 된다. 집단이 2개여도 블록 수만큼 점이 생긴다.
    """
    from scipy import stats

    centered = table.sub(table.mean(axis=1), axis=0)   # 블록 수준 드리프트 제거
    preds, actuals = [], []
    n_blocks = len(centered)
    for bi in range(n_blocks):
        row = centered.iloc[bi]
        others = centered.drop(centered.index[bi])
        if others.empty:
            continue
        expected = others.mean(axis=0, skipna=True)
        for g in centered.columns:
            a, e = row.get(g), expected.get(g)
            if a is None or e is None or a != a or e != e:
                continue
            actuals.append(float(a))
            preds.append(float(e))

    if len(preds) < 4 or len(np.unique(preds)) < 2 or len(np.unique(actuals)) < 2:
        return float("nan")
    rho = stats.spearmanr(preds, actuals).statistic
    return float(rho) if rho == rho else float("nan")


def audit_regime_feature(df: pd.DataFrame, feature: str, return_col: str = "_forward_return",
                         n_blocks: int = DEFAULT_BLOCKS, max_groups: int = DEFAULT_MAX_GROUPS,
                         shuffles: int = DEFAULT_SHUFFLES, seed: int = 0,
                         order_col: str = None) -> RegimeStabilityFinding:
    """체제 지표의 효과가 시간 블록을 넘어 유지되는지 판정한다 (모양 가정 없음)."""
    if feature not in df.columns or return_col not in df.columns:
        return RegimeStabilityFinding(feature, VERDICT_INDETERMINATE, reason="컬럼 없음")

    work = df[[feature, return_col] + ([order_col] if order_col and order_col in df.columns else [])]
    work = work.replace([np.inf, -np.inf], np.nan).dropna(subset=[feature, return_col])
    if order_col and order_col in work.columns:
        work = work.sort_values(order_col, kind="stable")
    n = len(work)
    if n < MIN_ROWS_PER_GROUP * MIN_GROUPS * n_blocks:
        return RegimeStabilityFinding(feature, VERDICT_INDETERMINATE, n_samples=n,
                                      reason=f"표본 부족(n={n:,})")

    groups = _as_groups(work[feature].astype(float), max_groups)
    n_groups = int(groups.nunique())
    if n_groups < MIN_GROUPS:
        return RegimeStabilityFinding(feature, VERDICT_INDETERMINATE, n_samples=n,
                                      n_groups=n_groups, reason="집단이 하나뿐(상수에 가까움)")

    # 시간 순서대로 균등 분할 (블록 경계를 데이터가 정한다 — 날짜 목록을 코드에 두지 않는다)
    block_ids = pd.Series(np.minimum((np.arange(n) * n_blocks) // n, n_blocks - 1),
                          index=work.index)
    returns = work[return_col].astype(float)

    table = _block_group_means(groups, returns, block_ids, n_blocks)
    if len(table) < 2:
        return RegimeStabilityFinding(feature, VERDICT_INDETERMINATE, n_samples=n,
                                      n_groups=n_groups, blocks=len(table),
                                      reason="비교할 블록이 2개 미만(칸 표본 부족)")

    agreement = _out_of_block_agreement(table)
    if agreement != agreement:
        return RegimeStabilityFinding(feature, VERDICT_INDETERMINATE, n_samples=n,
                                      n_groups=n_groups, blocks=len(table),
                                      reason="블록 간 공통 집단이 없음")

    # 잡음바닥: 라벨을 섞어 같은 계산을 반복한다 (문턱을 손으로 정하지 않는다)
    rng = np.random.default_rng(seed)
    ret_values = returns.to_numpy(dtype=float)
    null = []
    for _ in range(max(int(shuffles), 1)):
        shuffled = pd.Series(rng.permutation(ret_values), index=work.index)
        null_table = _block_group_means(groups, shuffled, block_ids, n_blocks)
        if len(null_table) < 2:
            continue
        score = _out_of_block_agreement(null_table)
        if score == score:
            null.append(score)
    if not null:
        return RegimeStabilityFinding(feature, VERDICT_INDETERMINATE, agreement=agreement,
                                      n_samples=n, n_groups=n_groups, blocks=len(table),
                                      reason="잡음바닥을 산출할 수 없음")

    noise_floor = float(np.percentile(null, NOISE_PERCENTILE))
    verdict = VERDICT_STABLE if agreement > noise_floor else VERDICT_UNSTABLE
    return RegimeStabilityFinding(feature, verdict, agreement=agreement, noise_floor=noise_floor,
                                  blocks=len(table), n_groups=n_groups, n_samples=n)


def audit_regime_features(df: pd.DataFrame, features, return_col: str = "_forward_return",
                          **kwargs) -> list:
    """여러 체제 지표를 한 번에 판정한다."""
    return [audit_regime_feature(df, f, return_col=return_col, **kwargs) for f in (features or [])]
