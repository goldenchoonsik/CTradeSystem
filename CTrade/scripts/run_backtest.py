import sys
import os
import time
import random
import tempfile

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ctrade_core

class PyOrderbook:
    def __init__(self, timestamp, level, units):
        self.timestamp = timestamp
        self.level = level
        self.units = units

def generate_mock_data():
    ticks = []
    orderbooks = []
    
    start_time = 1717732800.0  # 임의 기준 타임스탬프
    price = 50000000.0  # 가상 시작가
    
    for i in range(10000):
        ts = start_time + i * 0.1
        # 가우스 분포 난수 변화량에 트렌드 부여
        price_change = random.gauss(0, 10000)
        if 2000 < i < 6000:
            price_change += 2500  # 상승
        elif 6000 <= i < 9000:
            price_change -= 3500  # 하락
            
        price = max(1000000.0, price + price_change)
        
        side = 'B' if random.random() > 0.5 else 'A'
        volume = random.uniform(0.01, 1.5)
        ticks.append(ctrade_core.TickRecord(ts, price, volume, side))
        
        # 1초(10틱) 마다 호가 업데이트
        if i % 10 == 0:
            units = []
            for lvl in range(1, 6):
                bp = price - lvl * 1000.0
                ap = price + lvl * 1000.0
                bs = random.uniform(0.1, 5.0)
                as_ = random.uniform(0.1, 5.0)
                units.append(ctrade_core.OrderbookUnit(bp, bs, ap, as_))
            orderbooks.append(PyOrderbook(ts, 5.0, units))
            
    return ticks, orderbooks

def main():
    print("=== CTrade C++ 초고속 백테스팅 엔진 검증 ===")
    
    # 1. 모의 데이터 생성
    print("1. 모의 데이터 생성 중 (10,000 틱 / 1,000 호가)...")
    ticks, orderbooks = generate_mock_data()
    print("   - 생성 완료!")
    
    # 2. 백테스터 타겟 전략 설정 정의 (RSI/Imbalance 조건)
    target_config = {
        "symbol": "KRW-BTC",
        "exchange": "UPBIT",
        "budget_per_trade": 10000000.0, # 1000만 원
        "auto_trade": True,
        "active": True,
        "reference_market": "BINANCE",
        "orderbook_level": 5.0,
        "buy_conditions": {
            "logic": "AND",
            "rules": [
                {"target": "local_rsi", "operator": "<", "value": 45.0},
                {"target": "local_imbalance", "operator": ">", "value": -0.8}
            ]
        },
        "sell_conditions": {
            "logic": "OR",
            "rules": [
                {"target": "local_rsi", "operator": ">", "value": 55.0}
            ]
        }
    }
    
    # 3. 파이썬 리스트 주입 방식 백테스트
    print("\n2. 파이썬 리스트 주입 백테스트 구동...")
    t0 = time.time()
    
    tester = ctrade_core.Backtester(target_config, 100000000.0, 0.0005)
    tester.load_ticks(ticks)
    tester.load_orderbooks(orderbooks)
    t_load = time.time() - t0
    print(f"   - 데이터 로드 완료 (소요시간: {t_load:.4f}초)")
    
    t_run_start = time.time()
    stats = tester.run(30.0, 10.0)
    t_run = time.time() - t_run_start
    print(f"   - 백테스트 연산 완료 (소요시간: {t_run:.4f}초)")
    print(f"   - 전체 처리 소요시간: {time.time() - t0:.4f}초")
    
    print("\n--- 백테스트 결과 요약 (파이썬 데이터 주입) ---")
    print(f"최종 잔고: {stats.get('balance', 0.0):,.0f} KRW")
    print(f"총 거래 횟수: {stats.get('total_trades', 0)}")
    win_rate = stats.get('win_rate', 0.0)
    print(f"승률: {win_rate:.2f}%")
    
    # 4. CSV 다이렉트 파일 로드 방식 백테스트
    print("\n3. CSV 파일 생성 및 C++ 다이렉트 I/O 백테스트 구동...")
    
    temp_dir = tempfile.gettempdir()
    ticks_csv_path = os.path.join(temp_dir, "test_ticks.csv")
    obs_csv_path = os.path.join(temp_dir, "test_orderbooks.csv")
    
    # 틱 CSV 작성
    with open(ticks_csv_path, 'w') as f:
        f.write("timestamp,price,volume,side\n")
        for t in ticks:
            f.write(f"{t.timestamp},{t.price},{t.volume},{t.side}\n")
            
    # 호가 CSV 작성
    with open(obs_csv_path, 'w') as f:
        f.write("timestamp,level,bid_price1,bid_size1,ask_price1,ask_size1,bid_price2,bid_size2,ask_price2,ask_size2,bid_price3,bid_size3,ask_price3,ask_size3,bid_price4,bid_size4,ask_price4,ask_size4,bid_price5,bid_size5,ask_price5,ask_size5\n")
        for ob in orderbooks:
            line = f"{ob.timestamp},{ob.level}"
            for unit in ob.units:
                line += f",{unit.bid_price},{unit.bid_size},{unit.ask_price},{unit.ask_size}"
            f.write(line + "\n")
            
    print("   - 임시 CSV 파일 생성 완료.")
    
    t0_csv = time.time()
    tester_csv = ctrade_core.Backtester(target_config, 100000000.0, 0.0005)
    
    success_ticks = tester_csv.load_ticks_from_csv(ticks_csv_path)
    success_obs = tester_csv.load_orderbooks_from_csv(obs_csv_path)
    
    if not (success_ticks and success_obs):
        print("   - FAILED: CSV 파일 로드 실패!")
        return
        
    t_load_csv = time.time() - t0_csv
    print(f"   - C++ CSV 파일 직접 파싱/로드 완료 (소요시간: {t_load_csv:.4f}초)")
    
    t_run_csv_start = time.time()
    stats_csv = tester_csv.run(30.0, 10.0)
    t_run_csv = time.time() - t_run_csv_start
    print(f"   - 백테스트 연산 완료 (소요시간: {t_run_csv:.4f}초)")
    print(f"   - 전체 CSV 처리 소요시간: {time.time() - t0_csv:.4f}초")
    
    print("\n--- 백테스트 결과 요약 (CSV 직접 로드) ---")
    print(f"최종 잔고: {stats_csv.get('balance', 0.0):,.0f} KRW")
    print(f"총 거래 횟수: {stats_csv.get('total_trades', 0)}")
    win_rate_csv = stats_csv.get('win_rate', 0.0)
    print(f"승률: {win_rate_csv:.2f}%")
    
    # 5. 정합성 검증
    print("\n4. 정합성 검증 (파이썬 주입 vs CSV 로드)")
    match = True
    for key in ['balance', 'total_trades', 'win_rate']:
        v1 = stats.get(key)
        v2 = stats_csv.get(key)
        if v1 != v2:
            print(f"   - FAILED: {key} 값이 일치하지 않음! (파이썬: {v1}, CSV: {v2})")
            match = False
        else:
            print(f"   - SUCCESS: {key} 값 일치! ({v1})")
            
    if match:
        print("\n>>> 최종 검증 결과: PASS (두 방식의 백테스트 결과 완벽 일치!) <<<")
    else:
        print("\n>>> 최종 검증 결과: FAIL (데이터 불일치 발생) <<<")
        
    os.remove(ticks_csv_path)
    os.remove(obs_csv_path)

if __name__ == '__main__':
    main()
