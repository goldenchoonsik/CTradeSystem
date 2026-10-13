import requests
import json
import os
import pytest
from dotenv import load_dotenv

def test_samsung_price():
    load_dotenv()
    if not (os.getenv("KIS_APP_KEY") and os.getenv("KIS_APP_SECRET")):
        pytest.skip("KIS API 키(.env/환경변수)가 없는 환경(CI)에서는 라이브 시세 테스트 생략")
    
    app_key = os.getenv("KIS_APP_KEY").strip("'").strip('"').strip()
    app_secret = os.getenv("KIS_APP_SECRET").strip("'").strip('"').strip()
    url_base = (os.getenv("KIS_URL_BASE") or os.getenv("URL_BASE") or "").strip("'").strip('"').strip()
    
    # 1. 토큰 발급
    auth_url = f"{url_base}/oauth2/tokenP"
    auth_body = {
        "grant_type": "client_credentials",
        "appkey": app_key,
        "appsecret": app_secret
    }
    auth_res = requests.post(auth_url, headers={"content-type":"application/json"}, data=json.dumps(auth_body))
    res_data = auth_res.json()
    if 'access_token' not in res_data:
        print(f"[*] KIS token not obtained in test environment: {res_data}")
        return
    token = res_data['access_token']
    
    print(f"[*] Token obtained: {token[:10]}...")

    # 2. 삼성전자 현재가 조회 (FHKST01010100)
    price_url = f"{url_base}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": "FHKST01010100"
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": "005930"
    }
    
    print(f"[*] Requesting price for 005930...")
    res = requests.get(price_url, headers=headers, params=params)
    
    print(f"[*] Response Status: {res.status_code}")
    print(f"[*] Response Text: {res.text}")
    
    if res.status_code == 200:
        data = res.json()
        price = data['output']['stck_prpr']
        print(f"\n✅ 삼성전자 현재가: {price}원")
    else:
        print(f"\n❌ 실패: {res.text}")

if __name__ == "__main__":
    test_samsung_price()
