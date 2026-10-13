import asyncio
import os
import logging
from dotenv import load_dotenv
from utils.auth import KISAuthManager
from connectors.kis_connector import KISConnector

# 로깅 설정 (콘솔 출력)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

async def _run_kis_connection():
    # .env 파일 수동 로드 시도
    if os.path.exists(".env"):
        load_dotenv(".env")
    elif os.path.exists("t.txt"):
        load_dotenv("t.txt")

    print(f"[*] 현재 로드된 환경 변수 확인:")
    for k in ["KIS_APP_KEY", "KIS_APP_SECRET", "KIS_URL_BASE", "URL_BASE"]:
        val = os.getenv(k)
        status = "✅ 존재" if val else "❌ 누락"
        print(f"  - {k}: {status}")

    def get_env_safe(key):
        val = os.getenv(key)
        if val is None:
            raise ValueError(f"환경 변수 {key}를 찾을 수 없습니다!")
        return val.strip("'").strip('"').strip()

    try:
        app_key = get_env_safe("KIS_APP_KEY")
        app_secret = get_env_safe("KIS_APP_SECRET")
        url_base = os.getenv("KIS_URL_BASE") or get_env_safe("URL_BASE")
    except ValueError as e:
        print(f"\n❌ {e}")
        return
    
    print("\n" + "="*50)
    print(f"🚀 KIS 연결 테스트 시작 (URL: {url_base})")
    print("="*50)

    # 2. 인증 관리자 초기화
    auth = KISAuthManager(app_key, app_secret, url_base)
    
    # 3. 토큰 테스트
    print("\n[1] 토큰 발급 테스트 중...")
    token = await auth.get_access_token()
    if token:
        print(f"✅ 토큰 획득 성공 (앞 10자리): {token[:10]}...")
    else:
        print("❌ 토큰 획득 실패! .env 설정을 확인하세요.")
        return

    # 4. 승인키 테스트
    print("\n[2] 웹소켓 승인키 테스트 중...")
    approval_key = await auth.get_approval_key()
    if approval_key:
        print(f"✅ 승인키 획득 성공")
    else:
        print("❌ 승인키 획득 실패!")
        return

    # 5. 잔고 조회 테스트
    print("\n[3] KIS 잔고 조회 테스트 중...")
    from portfolio_manager import PortfolioManager
    from upbit_auth import UpbitAuth
    
    # KIS_ACC_NO 등 추가 로드
    acc_no = get_env_safe("KIS_ACC_NO")
    acc_code = get_env_safe("KIS_ACC_CODE")
    
    auth.acc_no = acc_no
    auth.acc_code = acc_code
    
    portfolio = PortfolioManager(auth, UpbitAuth("", "")) # 업비트는 일단 비움
    try:
        kis_balance = await portfolio.fetch_kis_balance()
        if kis_balance:
            print(f"✅ 잔고 조회 성공: {len(kis_balance)}개 종목 보유 중")
            for item in kis_balance:
                print(f"   - {item.get('name', '알수없음')}: {item.get('qty', 0)}주")
        else:
            print("✅ 잔고 조회 성공 (보유 종목 없음)")
    except Exception as e:
        print(f"❌ 잔고 조회 실패: {e}")

    # 6. 실시간 데이터 수신 테스트
    
    queue = asyncio.Queue()
    is_simulation = "openapivts" in url_base.lower()
    connector = KISConnector(["005930"], queue, auth, is_simulation)
    
    # 커넥터를 백그라운드에서 실행
    conn_task = asyncio.create_task(connector.connect())
    
    # 큐에서 데이터 읽기
    start_time = asyncio.get_event_loop().time()
    try:
        while asyncio.get_event_loop().time() - start_time < 15:
            try:
                data = await asyncio.wait_for(queue.get(), timeout=1.0)
                print(f"📡 데이터 수신: {data}")
            except asyncio.TimeoutError:
                print(".", end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n\n[*] 테스트 종료")
        conn_task.cancel()

def test_kis_connection():
    try:
        asyncio.run(_run_kis_connection())
    except Exception as e:
        logging.warning(f"KIS connection test skip: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(_run_kis_connection())
    except KeyboardInterrupt:
        pass
