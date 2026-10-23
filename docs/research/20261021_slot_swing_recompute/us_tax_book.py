"""미국 주식 계좌 장부 — 한국 거주자 양도세 적용/비적용 (2026-10-21)

- 계좌 단위: 시작일 원화 1억을 그날 환율로 바꾼 달러 = 1.0
- 매수·매도: 편도 비용 cost, 종목별 이동평균 원화 취득가(매수 시점 환율)
- 양도세(tax=True): 연도별 원화 실현손익 합산 → 250만원 공제 → 22% 를 다음 해 5월 마지막 거래일에 인출
                   (현금 부족분은 보유 종목을 같은 비율로 팔아 마련, 그 매도 손익도 그해 실현으로 잡음)
                   현금 이자는 15.4% 원천징수 후. 마지막 날 전량 청산 가정으로 남은 실현·미실현분까지 정산
- tax=False: 같은 계좌, 세금만 없음(원화 평가)
- 배당은 반영하지 않음(전략·QQQ 모두 가격 기준)
"""
import numpy as np


class Book:
    def __init__(self, cal, fx, rf, px, start, cost=0.001, tax=False, rate=0.22, deduct=2.5e6, init_krw=1e8,
                 div=None, taxfree=(), wht=0.15):
        self.cal, self.fx, self.rf, self.px = cal, fx, rf, px      # px(i, j) → 마지막 유효 종가
        self.cost, self.tax, self.rate, self.deduct = cost, tax, rate, deduct
        self.div, self.taxfree, self.wht = div, set(taxfree), wht     # div: 날짜×종목 주당 배당(권리락일), taxfree: 양도세 없는 종목
        self.unit = init_krw / fx[start]                          # 계좌 1.0 = 몇 달러
        self.cash = 1.0; self.sh = {}; self.basis = {}            # basis: 원화(1억=1e8 기준 금액)
        self.realized = {}; self.tax_paid = 0.0
        self.may = set()
        for y in sorted(set(cal.year)):
            ix = np.where((cal.year == y) & (cal.month == 5))[0]
            if len(ix): self.may.add(int(ix[-1]))

    def krw(self, units, i):
        return units * self.unit * self.fx[i]

    def buy(self, j, units, p, i):
        if units <= 0: return
        q = units * (1 - self.cost) / p
        self.cash -= units
        self.sh[j] = self.sh.get(j, 0.0) + q
        self.basis[j] = self.basis.get(j, 0.0) + self.krw(units, i)

    def sell(self, j, q, p, i):
        have = self.sh.get(j, 0.0)
        q = min(q, have)
        if q <= 0: return 0.0
        proceeds = q * p * (1 - self.cost)
        b = self.basis[j] * q / have
        if j not in self.taxfree:
            self.realized[self.cal[i].year] = self.realized.get(self.cal[i].year, 0.0) + self.krw(proceeds, i) - b
        self.basis[j] -= b; self.sh[j] = have - q; self.cash += proceeds
        if self.sh[j] <= 1e-15:
            del self.sh[j]; del self.basis[j]
        return proceeds

    def value(self, i, prices=None):
        return self.cash + sum(q * (prices[j] if prices and j in prices else self.px(i, j)) for j, q in self.sh.items())

    def set_targets(self, i, tgt, prices):
        """tgt: {j: 목표 단위금액}. prices: 체결가. 매도 먼저, 그다음 매수."""
        for j in list(self.sh):
            cur = self.sh[j] * prices[j]
            want = tgt.get(j, 0.0)
            if cur > want + 1e-12:
                self.sell(j, (cur - want) / prices[j], prices[j], i)
        for j, want in tgt.items():
            cur = self.sh.get(j, 0.0) * prices[j]
            if want > cur + 1e-12:
                self.buy(j, min(want - cur, max(self.cash, 0.0)), prices[j], i)

    def start_day(self, i):
        """하루 시작: 현금 이자, (5월 말) 전년도 양도세 인출."""
        r = self.rf[i] * ((1 - 0.154) if self.tax else 1.0)
        self.cash *= 1 + r
        if self.div is not None:                                  # 배당: 전일 보유분, 세후면 15% 원천징수
            for j, q in self.sh.items():
                d = self.div[i, j]
                if d > 0: self.cash += q * d * ((1 - self.wht) if self.tax else 1.0)
        if self.tax and i in self.may:
            g = self.realized.get(self.cal[i].year - 1, 0.0)
            if g > self.deduct:
                t_units = (g - self.deduct) * self.rate / (self.unit * self.fx[i])
                self.tax_paid += (g - self.deduct) * self.rate
                need = t_units - self.cash
                if need > 0:
                    v = self.value(i - 1) - self.cash
                    frac = min(1.0, need / v) if v > 0 else 0.0
                    for j in list(self.sh):
                        self.sell(j, self.sh[j] * frac, self.px(i - 1, j), i)
                self.cash -= t_units

    def equity_krw(self, i):
        return self.krw(self.value(i), i) / 1e8      # 1.0 = 시작 1억

    def final_after_tax(self, i):
        """마지막 날 전량 청산 가정 최종 원화(억). tax=False 면 세전 평가액."""
        v = self.equity_krw(i)
        if not self.tax:
            return v, 0.0
        g = self.realized.get(self.cal[i].year, 0.0)
        for j, q in self.sh.items():
            if j in self.taxfree: continue
            g += self.krw(q * self.px(i, j) * (1 - self.cost), i) - self.basis[j]
        t = max(0.0, g - self.deduct) * self.rate
        return v - t / 1e8, (self.tax_paid + t) / 1e8
