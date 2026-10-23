"""'큰 승자 잡기' 계열 전략의 실제 운용 ETF 성적 — 비교판과 같은 잣대 (2026-10-21)

전략별 대표 ETF 를 미리 정함(수익률로 고르지 않음). 실제 돈이 굴러간 표본 밖 기록이라 백테스트 착시가 없다.
  모멘텀: PDP(DWA 상대강도, 2007), XMMO(중형 모멘텀), MTUM(MSCI 모멘텀), SPMO(S&P500 모멘텀), QMOM(Alpha Architect 퀀트 모멘텀)
  CANSLIM: FFTY(IBD 50 — 실적 가속+신고가+기관 매수)
  신규상장: FPX(IPOX-100, 상장 후 4년), IPO(Renaissance IPO, 상장 후 3년)
  혁신 성장: ARKK(파괴적 혁신, 액티브)   성장 GARP: SPGP
  소형·대형 성장: IWO / IWF,  동일가중: RSP
조건(etf_scan.py 와 같음): 원화 1억, 시작일 다음 날 시가 매수 후 보유, 편도 0.1%, 배당 재투자(세후 15% 원천징수),
세후 = 미국 직투 22%(마지막 날 청산 정산). --end=2023-12-31 이면 반도체 급등 제외판.
실행: .venv/bin/python -I docs/research/20261021_winner_capture_etfs/winner_etfs.py [--end=2023-12-31]
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '20261021_strategy_board'))
sys.path.insert(0, os.path.join(HERE, '..', '20261021_slot_swing_recompute'))
import board as bd
import etf_scan as es

GA = {'기준': ['SPY', 'QQQ', 'RSP'], '모멘텀': ['PDP', 'XMMO'], '신규상장': ['FPX'], '성장': ['IWF', 'IWO']}
GB = {'기준': ['SPY', 'QQQ'], '모멘텀': ['MTUM', 'SPMO', 'QMOM'], 'CANSLIM': ['FFTY'], '신규상장': ['FPX', 'IPO'],
      '혁신': ['ARKK'], 'GARP': ['SPGP'], '성장': ['IWO']}
if __name__ == '__main__':
    tag = bd.END.strftime('%Y%m')
    for nm, g, st in [('A', GA, '2007-03-30'), ('B', GB, '2015-12-31')]:
        df = es.table(g, st)
        print(f"=== {nm}: {st} ~ {bd.END.date()} ===")
        print(df.to_string(index=False))
        df.to_csv(os.path.join(HERE, f"winner_{nm}_end{tag}.csv"), index=False, encoding='utf-8-sig')
