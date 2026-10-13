import json
import os
import glob
from datetime import datetime

# 최신 로그 파일 찾기
today = datetime.now().strftime("%Y%m%d")
log_path = f"vultr_logs/{today}/trade_history.json"
if not os.path.exists(log_path):
    # 가장 최근 디렉토리
    dirs = sorted(glob.glob("vultr_logs/*"))
    if dirs:
        log_path = os.path.join(dirs[-1], "trade_history.json")

with open(log_path, 'r', encoding='utf-8') as f:
    history = json.load(f)

print(f"==================================================")
print(f"📊 Vultr 시뮬레이션 매매 결과 정밀 분석 ({log_path})")
print(f"==================================================")
print(f"• 총 이벤트 건수: {len(history)}건")

buys = [h for h in history if h.get('type') == 'BUY']
sells = [h for h in history if h.get('type') == 'SELL']
refills = [h for h in history if h.get('type') == 'REFILL']

print(f"• 매수(BUY): {len(buys)}건 | 매도/청산(SELL): {len(sells)}건 | 자동충전(REFILL): {len(refills)}회")

# 현재 가상 잔고
if history:
    last_balance = history[-1].get("balance", 0.0)
    print(f"• 현재 가상 잔고: {last_balance:,.0f}원")

symbols = set(h.get('symbol') for h in history if h.get('symbol') != 'SYSTEM')

for sym in sorted(symbols):
    sym_buys = [h for h in buys if h.get('symbol') == sym]
    sym_sells = [h for h in sells if h.get('symbol') == sym]
    
    total_profit = sum(h.get('profit', 0) for h in sym_sells)
    total_fee = sum(h.get('fee', 0) for h in sym_buys) + sum(h.get('fee', 0) for h in sym_sells)
    win_trades = [h for h in sym_sells if h.get('profit', 0) > 0]
    loss_trades = [h for h in sym_sells if h.get('profit', 0) < 0]
    even_trades = [h for h in sym_sells if h.get('profit', 0) == 0]
    
    win_rate = (len(win_trades) / len(sym_sells) * 100) if sym_sells else 0.0
    avg_hold_sec = (sum(h.get('holding_time', 0) for h in sym_sells) / len(sym_sells)) if sym_sells else 0.0
    avg_profit_rate = (sum(h.get('profit_rate', 0) for h in sym_sells) / len(sym_sells)) if sym_sells else 0.0
    
    reasons = {}
    for h in sym_sells:
        r = h.get('reason', 'UNKNOWN')
        reasons[r] = reasons.get(r, 0) + 1

    print("\n" + "-"*50)
    print(f"📌 [종목: {sym}]")
    print(f"  • 청산 건수: 총 {len(sym_sells)}건 ({len(win_trades)}승 {len(loss_trades)}패 {len(even_trades)}무)")
    print(f"  • 승률(Win Rate): {win_rate:.1f}%")
    print(f"  • 누적 실현손익: {total_profit:+,.0f}원 (평균 수익률: {avg_profit_rate:+.2f}%)")
    print(f"  • 지불 수수료: {total_fee:,.0f}원")
    print(f"  • 평균 포지션 보유 시간: {avg_hold_sec:.1f}초 ({avg_hold_sec/60:.1f}분)")
    print(f"  • 청산 사유: {reasons}")
    
    # 수량 정수성 검증
    qtys = [b.get("qty", 0) for b in sym_buys]
    if sym in ["069500", "122630"] or sym.isdigit():
        is_all_int = all(q == int(q) for q in qtys)
        print(f"  • 수량 정수 검증: {'✅ 완벽한 정수(1주 단위) 매수 확인' if is_all_int else '❌ 소수점 발생'}")
        if sym_buys:
            latest_b = sym_buys[-1]
            print(f"  • 최신 매수 샘플: 단가 {latest_b.get('price'):,.0f}원 | 수량 {latest_b.get('qty')}주 | 매수액 {latest_b.get('amount'):,.0f}원")
    else:
        print(f"  • 코인 소수점 수량 검증: ✅ 정상 사토시 소수점 수량")
        if sym_buys:
            latest_b = sym_buys[-1]
            print(f"  • 최신 매수 샘플: 단가 {latest_b.get('price'):,.0f}원 | 수량 {latest_b.get('qty')} | 매수액 {latest_b.get('amount'):,.0f}원")

print("="*50)
