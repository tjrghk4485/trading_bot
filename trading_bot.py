import os
import sys
import time
import datetime
import logging
import requests
import json
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("trading_log.txt", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 환경 변수 로드
APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")
CANO = os.getenv("KIS_CANO")
ACNT_PRDT_CD = os.getenv("KIS_ACNT_PRDT_CD")
URL_BASE = os.getenv("KIS_URL")

# 전역 변수
ACCESS_TOKEN = ""

def get_access_token():
    """OAuth2 토큰 발급"""
    url = f"{URL_BASE}/oauth2/tokenP"
    headers = {"content-type": "application/json"}
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "secretkey": APP_SECRET
    }
    try:
        res = requests.post(url, headers=headers, data=json.dumps(body))
        if res.status_code == 200:
            token = res.json().get("access_token")
            logger.info("Access Token 발급 성공")
            return token
        else:
            logger.error(f"토큰 발급 실패: {res.text}")
            sys.exit()
    except Exception as e:
        logger.error(f"토큰 요청 중 오류: {e}")
        sys.exit()

def get_hashkey(body):
    """Hashkey 발급 (POST 요청용)"""
    url = f"{URL_BASE}/uapi/hashkey"
    headers = {
        'content-type': 'application/json',
        'appkey': APP_KEY,
        'appsecret': APP_SECRET,
    }
    res = requests.post(url, headers=headers, data=json.dumps(body))
    if res.status_code == 200:
        return res.json()["hashkey"]
    else:
        logger.error(f"Hashkey 발급 실패: {res.text}")
        return None

def get_header(tr_id, hashkey=None):
    """API 헤더 생성"""
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {ACCESS_TOKEN}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": tr_id,
        "custtype": "P"
    }
    if hashkey:
        headers["hashkey"] = hashkey
    return headers

def get_volume_rank():
    """거래량 상위 종목 스캔 (모의투자 미지원 대응)"""
    # 모의투자의 경우 거래량 순위 API를 지원하지 않으므로 더미/관심 종목 반환
    if "vts" in URL_BASE:
        logger.info("모의투자 모드: 거래량 순위 API 대신 미리 지정된 종목 리스트를 사용합니다.")
        # 모의투자에서 테스트 가능한 주요 종목 리스트 (삼성전자, SK하이닉스, NAVER 등)
        return [
            {"mksc_shrn_iscd": "005930", "hts_avls": "4000000"}, # 삼성전자
            {"mksc_shrn_iscd": "000660", "hts_avls": "1000000"}, # SK하이닉스
            {"mksc_shrn_iscd": "035420", "hts_avls": "300000"},  # NAVER
            {"mksc_shrn_iscd": "005380", "hts_avls": "500000"},  # 현대차
            {"mksc_shrn_iscd": "068270", "hts_avls": "200000"}   # 셀트리온
        ]

    path = "/uapi/domestic-stock/v1/quotations/volume-rank"
    headers = get_header("FHPST01710000") # 거래량 순위 TR ID 수정
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0",
        "FID_BLNG_CLS_CODE": "0",
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "0000000000",
        "FID_INPUT_PRICE_1": "",
        "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
        "FID_INPUT_DATE_1": ""
    }
    res = requests.get(URL_BASE + path, headers=headers, params=params)
    if res.status_code == 200:
        return res.json().get("output", [])
    else:
        logger.error(f"거래량 순위 조회 실패: {res.text}")
    return []

def get_current_price(symbol):
    """현재가 및 상세 정보 조회"""
    path = "/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = get_header("FHKST01010100")
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol}
    res = requests.get(URL_BASE + path, headers=headers, params=params)
    if res.status_code == 200:
        output = res.json().get("output")
        if output:
            return {
                "price": int(output["stck_prpr"]),
                "vol_rate": float(output["prdy_vol_vrss_prcnt"]),
                "market_cap": int(output["hts_avls"]),
                "name": output.get("hts_kor_isnm", symbol)
            }
    return None

def buy_market_order(symbol, qty=1):
    """시장가 매수 주문"""
    tr_id = "TTTC0802U" if "vts" not in URL_BASE else "VTTC0802U"
    path = "/uapi/domestic-stock/v1/trading/order-cash"
    
    body = {
        "CANO": CANO,
        "ACNT_PRDT_CD": ACNT_PRDT_CD,
        "PDNO": symbol,
        "ORD_DVSN": "01", # 시장가
        "ORD_QTY": str(qty),
        "ORD_UNPR": "0"
    }
    
    hashkey = get_hashkey(body)
    headers = get_header(tr_id, hashkey)
    
    res = requests.post(URL_BASE + path, headers=headers, data=json.dumps(body))
    if res.status_code == 200:
        res_data = res.json()
        if res_data["rt_cd"] == "0":
            logger.info(f"[매수 주문 성공] 종목: {symbol}, 수량: {qty}")
            return True
        else:
            logger.error(f"[매수 주문 거부] {res_data['msg1']}")
    else:
        logger.error(f"[매수 API 에러] {res.text}")
    return False

def sell_market_order(symbol, qty=1):
    """시장가 매도 주문"""
    tr_id = "TTTC0801U" if "vts" not in URL_BASE else "VTTC0801U"
    path = "/uapi/domestic-stock/v1/trading/order-cash"
    
    body = {
        "CANO": CANO,
        "ACNT_PRDT_CD": ACNT_PRDT_CD,
        "PDNO": symbol,
        "ORD_DVSN": "01", # 시장가
        "ORD_QTY": str(qty),
        "ORD_UNPR": "0"
    }
    
    hashkey = get_hashkey(body)
    headers = get_header(tr_id, hashkey)
    
    res = requests.post(URL_BASE + path, headers=headers, data=json.dumps(body))
    if res.status_code == 200:
        res_data = res.json()
        if res_data["rt_cd"] == "0":
            logger.info(f"[매도 주문 성공] 종목: {symbol}, 수량: {qty}")
            return True
        else:
            logger.error(f"[매도 주문 거부] {res_data['msg1']}")
    else:
        logger.error(f"[매도 API 에러] {res.text}")
    return False

def main():
    global ACCESS_TOKEN
    logger.info("=== KIS 실시간 급등주 자동매매 데몬 시작 ===")
    
    # 초기 토큰 발급
    ACCESS_TOKEN = get_access_token()
    
    target_symbol = None
    buy_price = 0
    stock_name = ""
    
    while True:
        try:
            # 1. 탐색 단계: 매수한 종목이 없을 때
            if target_symbol is None:
                logger.info("실시간 시장 스캔 중...")
                stocks = get_volume_rank()
                
                for stock in stocks[:30]: # 상위 30개 종목 검사
                    symbol = stock["mksc_shrn_iscd"]
                    m_cap = int(stock.get("hts_avls", 0)) # 시총(억)
                    
                    # 필터 1: 시가총액 1,000억 이상
                    if m_cap < 1000:
                        continue
                    
                    # 상세 정보 확인 (거래량 및 현재가)
                    price_info = get_current_price(symbol)
                    if not price_info:
                        continue
                        
                    # 필터 2: 거래량 급증 (전일 대비 200% 이상)
                    if price_info["vol_rate"] >= 200:
                        stock_name = price_info["name"]
                        logger.info(f"[조건 포착] {stock_name}({symbol}) | 시총: {m_cap}억 | 거래량증가: {price_info['vol_rate']}%")
                        
                        # 즉시 매수 실행 (1주 예시)
                        if buy_market_order(symbol, 1):
                            target_symbol = symbol
                            buy_price = price_info["price"]
                            logger.info(f"[매수완료] {stock_name} 매수가: {buy_price}")
                            break # 한 종목에 집중
                
                if target_symbol is None:
                    time.sleep(5) # 탐색 주기
            
            # 2. 모니터링 및 익절 단계: 매수한 종목이 있을 때
            else:
                price_info = get_current_price(target_symbol)
                if price_info:
                    current_price = price_info["price"]
                    profit_rate = ((current_price - buy_price) / buy_price) * 100
                    
                    logger.info(f"[수익률 모니터링] {stock_name}: {profit_rate:.2f}% (현재가: {current_price})")
                    
                    # 익절 조건: +3% 이상
                    if profit_rate >= 3.0:
                        logger.info(f"🚀 목표 수익률 달성 (+{profit_rate:.2f}%)! 매도를 진행합니다.")
                        if sell_market_order(target_symbol, 1):
                            logger.info("=== 트레이딩 종료 및 프로그램 종료 ===")
                            sys.exit()
                    
                    # 손절 조건 (예시: -2% 하락 시)
                    elif profit_rate <= -2.0:
                        logger.info(f"📉 손절 기준 도달 ({profit_rate:.2f}%). 매도를 진행합니다.")
                        sell_market_order(target_symbol, 1)
                        sys.exit()

                time.sleep(2) # 모니터링 주기

        except KeyboardInterrupt:
            logger.info("사용자에 의해 프로그램이 중단되었습니다.")
            sys.exit()
        except Exception as e:
            logger.error(f"루프 내 오류 발생: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
