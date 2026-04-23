import sys
import os
import time
import datetime
import requests
import json
import logging
import csv
import argparse
from dotenv import load_dotenv

# .env 로드
load_dotenv()

from logging.handlers import TimedRotatingFileHandler
# region 기본 세팅
# --- [인자 처리] ---
parser = argparse.ArgumentParser(description="Fast Scalper Trading Bot")
parser.add_argument("--mode", type=str, choices=["REAL", "PAPER", "SIM"], default="SIM", help="REAL: 실거래, PAPER: 모의투자, SIM: 가상시뮬레이션")
args = parser.parse_args()

MODE = args.mode

# 로그 디렉토리 생성
LOG_DIR = "logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# 파일 로깅 설정 (날짜별 회전)
log_filename = os.path.join(LOG_DIR, f"scalper_{MODE.lower()}.log")
handler = TimedRotatingFileHandler(
    log_filename, when="midnight", interval=1, backupCount=30, encoding="utf-8"
)
handler.suffix = "%Y-%m-%d" 
handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        handler,
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

HISTORY_FILE = f"scalper_picks_{MODE.lower()}.csv"

# --- [계좌 및 URL 설정] ---
# 1. 조회용 (항상 실거래 키 사용 - 랭크 API용)
REAL_APP_KEY = os.getenv("REAL_APP_KEY")
REAL_APP_SECRET = os.getenv("REAL_APP_SECRET")
URL_REAL = "https://openapi.koreainvestment.com:9443"

# 2. 매매용 (REAL/PAPER 선택)
if MODE == "REAL":
    TRADE_APP_KEY = REAL_APP_KEY
    TRADE_APP_SECRET = REAL_APP_SECRET
    CANO = os.getenv("REAL_CANO")
    ACNT_PRDT_CD = os.getenv("REAL_ACNT_PRDT_CD", "01")
    URL_TRADE = URL_REAL
elif MODE == "PAPER":
    TRADE_APP_KEY = os.getenv("PAPER_APP_KEY")
    TRADE_APP_SECRET = os.getenv("PAPER_APP_SECRET")
    CANO = os.getenv("PAPER_CANO")
    ACNT_PRDT_CD = os.getenv("PAPER_ACNT_PRDT_CD", "01")
    URL_TRADE = "https://openapivts.koreainvestment.com:29443"
else: # SIM
    TRADE_APP_KEY = REAL_APP_KEY
    TRADE_APP_SECRET = REAL_APP_SECRET
    URL_TRADE = URL_REAL

TOKEN_REAL = ""  # 조회용 토큰
TOKEN_TRADE = "" # 매매용 토큰
PREV_DATA = {} 

# --- [전역 트래킹 변수] ---
TRACKER_DICT = {}  # 종목별 상태 분석기 저장
MY_PORTFOLIO = {}  # 가상 매수 종목 저장
TOTAL_PROFIT_LOSS = 0.0  # 당일 누적 실현 수익률 (%)
# endregion

# region 매수 알고리즘
class CandidateTracker:
    def __init__(self, code, name):
        self.code = code
        self.name = name
        self.history = []  # 최근 틱 데이터 [(price, velocity, rate), ...]
        self.max_history = 5

    def add_tick(self, price, velocity, rate):
        if not self.history:
            self.history.append((price, velocity, rate))
            return "INIT"
        
        # 틱 데이터 저장
        self.history.append((price, velocity, rate))
        if len(self.history) > self.max_history:
            self.history.pop(0)

        # 점수 계산 (최근 기록을 순회하며 점수 합산)
        total_score = 0
        for i in range(1, len(self.history)):
            p_prev, v_prev, _ = self.history[i-1]
            p_curr, v_curr, _ = self.history[i]
            
            # 가격이 오르면서 거래가 터지는 경우 (가장 높은 점수)
            if p_curr > p_prev and v_curr >= 200: total_score += 1.0
            # 가격만 오르는 경우
            elif p_curr > p_prev: total_score += 0.5
            # 가격은 그대로인데 거래가 터지는 경우 (매물 소화)
            elif p_curr == p_prev and v_curr >= 200: total_score += 0.3
            # 가격이 떨어지는 경우 (패널티 - 매도세로 판단)
            elif p_curr < p_prev: total_score -= 1.0

        # 5틱 중 포인트 합계가 2.5점 이상이면 매수 신호
        if total_score >= 2.5:
            return "BUY_SIGNAL"
        return "WATCHING"

def get_access_token(app_key, app_secret, url):
    path = "/oauth2/tokenP"
    body = {"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret}
    try:
        res = requests.post(url + path, data=json.dumps(body))
        return res.json().get("access_token")
    except: return None

def get_hashkey(body, app_key, app_secret, url):
    path = "/uapi/hashkey"
    headers = {'content-type': 'application/json', 'appkey': app_key, 'appsecret': app_secret}
    res = requests.post(url + path, headers=headers, data=json.dumps(body))
    return res.json().get("hashkey")

def get_header(tr_id, token, app_key, app_secret, hashkey=None):
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": app_key, "appsecret": app_secret,
        "tr_id": tr_id, "custtype": "P"
    }
    if hashkey: headers["hashkey"] = hashkey
    return headers

def buy_market_order(symbol, qty=1):
    """시장가 매수 주문 (TRADE 계정 사용)"""
    if MODE == "SIM": 
        logger.info(f"✨ [SIM 매수] {symbol} {qty}주")
        return True
        
    tr_id = "TTTC0802U" if MODE == "REAL" else "VTTC0802U"
    path = "/uapi/domestic-stock/v1/trading/order-cash"
    body = {
        "CANO": CANO, "ACNT_PRDT_CD": ACNT_PRDT_CD, "PDNO": symbol,
        "ORD_DVSN": "01", "ORD_QTY": str(qty), "ORD_UNPR": "0"
    }
    h_key = get_hashkey(body, TRADE_APP_KEY, TRADE_APP_SECRET, URL_TRADE)
    headers = get_header(tr_id, TOKEN_TRADE, TRADE_APP_KEY, TRADE_APP_SECRET, h_key)
    res = requests.post(URL_TRADE + path, headers=headers, data=json.dumps(body))
    res_data = res.json()
    if res_data.get("rt_cd") == "0":
        logger.info(f"🚀 [실제 매수 성공] {symbol} {qty}주")
        return True
    else:
        logger.error(f"❌ [매수 실패] {res_data.get('msg1')}")
        return False

def sell_market_order(symbol, qty=1):
    """시장가 매도 주문 (TRADE 계정 사용)"""
    if MODE == "SIM": 
        logger.info(f"✨ [SIM 매도] {symbol} {qty}주")
        return True

    tr_id = "TTTC0801U" if MODE == "REAL" else "VTTC0801U"
    path = "/uapi/domestic-stock/v1/trading/order-cash"
    body = {
        "CANO": CANO, "ACNT_PRDT_CD": ACNT_PRDT_CD, "PDNO": symbol,
        "ORD_DVSN": "01", "ORD_QTY": str(qty), "ORD_UNPR": "0"
    }
    h_key = get_hashkey(body, TRADE_APP_KEY, TRADE_APP_SECRET, URL_TRADE)
    headers = get_header(tr_id, TOKEN_TRADE, TRADE_APP_KEY, TRADE_APP_SECRET, h_key)
    res = requests.post(URL_TRADE + path, headers=headers, data=json.dumps(body))
    res_data = res.json()
    if res_data.get("rt_cd") == "0":
        logger.info(f"💰 [실제 매도 성공] {symbol} {qty}주")
        return True
    else:
        logger.error(f"❌ [매도 실패] {res_data.get('msg1')}")
        return False

def save_to_history(data):
    with open(HISTORY_FILE, 'a', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        now_str = datetime.datetime.now().strftime('%H:%M:%S')
        writer.writerow([
            now_str, data['code'], data['name'], data['price'], 
            f"{data['rate']}%", data['velocity_백만'], f"{data['power']}%", data['amt_억']
        ])

# 모니터링 API
def get_surging_stocks():
    global PREV_DATA
    path = "/uapi/domestic-stock/v1/quotations/volume-rank"
    # [사용자 요청] API 파라미터 보존
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": "1001",
        "FID_DIV_CLS_CODE": "0",
        "FID_BLNG_CLS_CODE": "3",
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "000000000", 
        "FID_INPUT_PRICE_1": "1000",
        "FID_INPUT_PRICE_2": "300000",
        "FID_VOL_CNT": "50000",
        "FID_INPUT_DATE_1": "",
        "CONTINUE": ""
    }
    
    try:
        # 항상 실거래(REAL) 계정으로 조회
        headers = get_header("FHPST01710000", TOKEN_REAL, REAL_APP_KEY, REAL_APP_SECRET)
        res = requests.get(f"{URL_REAL}{path}", headers=headers, params=params)
        data = res.json()
        if data.get("rt_cd") != '0': return [], f"API에러:{data.get('rt_cd')}"

        stocks = data.get("output", [])
        targets = []
        exclude_keywords = ['KODEX', 'TIGER', 'HANARO', 'KBSTAR', 'ARIRANG', 'SOL', 'ACE', 'ETN', '삼성전자']
        
        for s in stocks:
            try:
                name = s.get('hts_kor_isnm', '').strip()
                if any(kw in name.upper() for kw in exclude_keywords): continue
                code = s.get('mksc_shrn_iscd')
                price = int(s.get('stck_prpr', 0))
                rate = float(s.get('prdy_ctrt', 0))
                curr_amt = int(s.get('acml_tr_pbmn', 0))
                power = float(s.get('vol_inrt', 0))
                
                # 분석 범위: 1.5% ~ 5.0%
                if 1.5 <= rate <= 5.0 and curr_amt > 2000000000:

                    velocity = curr_amt - PREV_DATA.get(code, curr_amt)
                    PREV_DATA[code] = curr_amt
                    if velocity > 0:
                        target = {"code": code, "name": name, "price": price, "rate": rate,
                                  "amt_억": curr_amt // 100000000, "velocity_백만": velocity // 1000000, "power": power}
                        targets.append(target)
                        if target['velocity_백만'] >= 30: save_to_history(target)
            except: continue
        return targets, "정상"
    except Exception as e: return [], f"오류:{str(e)[:100]}"
# 매수한 종목 실시간 조회 API
def get_current_price(code):
    path = "/uapi/domestic-stock/v1/quotations/inquire-price"
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code
    }
    try:
        # 현재가 조회도 실거래(REAL) 계정으로 안정적으로 수행
        headers = get_header("FHKST01010100", TOKEN_REAL, REAL_APP_KEY, REAL_APP_SECRET)
        res = requests.get(f"{URL_REAL}{path}", headers=headers, params=params)
        data = res.json()
        if data.get("rt_cd") == '0':
            return int(data.get("output", {}).get("stck_prpr", 0))
        return None
    except:
        return None

# --- [트레이딩 엔진 실행부] ---
# 로직 시작 부
def process_trading_logic(targets):
    for s in targets:
        code, name, price, velocity, rate = s['code'], s['name'], s['price'], s['velocity_백만'], s['rate']

        # 신규 분석 (보유 중이지 않은 종목만)
        if code not in MY_PORTFOLIO:
            if code not in TRACKER_DICT:
                TRACKER_DICT[code] = CandidateTracker(code, name)
            
            signal = TRACKER_DICT[code].add_tick(price, velocity, rate)
            if signal == "BUY_SIGNAL":
                execute_simulated_buy(code, name, price)

def execute_simulated_buy(code, name, price):
    if code in MY_PORTFOLIO: return
    
    # 실제 주문 실행 (수량 1주 고정)
    if buy_market_order(code, 1):
        logger.info(f"🚀 [매수 집행] {name}({code}) | 진입가: {price:,}원 | 수량: 1주")
        MY_PORTFOLIO[code] = {
            'name': name, 
            'entry_price': price, 
            'highest_price': price, 
            'time': datetime.datetime.now()
        }
    else:
        logger.error(f"❌ [매수 실패] {name}({code}) 주문 처리 중 오류 발생")
# 손익절 체크로직
def manage_exit_strategy(code, curr_price):
    global TOTAL_PROFIT_LOSS
    if code not in MY_PORTFOLIO: return
    
    trade = MY_PORTFOLIO[code]
    entry_price = trade['entry_price']
    entry_time = trade['time']
    profit_rate = (curr_price - entry_price) / entry_price * 100
    
    # 시간 체크 (진입 후 경과 시간)
    elapsed_time = (datetime.datetime.now() - entry_time).total_seconds() / 60
    
    if curr_price > trade['highest_price']: trade['highest_price'] = curr_price

    # 1. 목표 달성 (+1.75% 익절)
    if profit_rate >= 1.75:
        if sell_market_order(code, 1):
            TOTAL_PROFIT_LOSS += profit_rate
            logger.info(f"💰 [익절 완료] {trade['name']} | 수익률: {profit_rate:.2f}% | 당일 누적: {TOTAL_PROFIT_LOSS:.2f}% | 매도가: {curr_price:,}")
            del MY_PORTFOLIO[code]
            if code in TRACKER_DICT: del TRACKER_DICT[code]
            return True

    # 2. 타임컷 (15분 경과 시 +0.5% 이상이면 탈출)
    elif elapsed_time >= 15.0 and profit_rate >= 0.5:
        if sell_market_order(code, 1):
            TOTAL_PROFIT_LOSS += profit_rate
            logger.info(f"⏱️ [타임컷 익절] {trade['name']} (15분 경과) | 수익률: {profit_rate:.2f}% | 당일 누적: {TOTAL_PROFIT_LOSS:.2f}% | 매도가: {curr_price:,}")
            del MY_PORTFOLIO[code]
            if code in TRACKER_DICT: del TRACKER_DICT[code]
            return True

    # 3. 강제 손절 (-2.5%)
    elif profit_rate <= -2.5:
        if sell_market_order(code, 1):
            TOTAL_PROFIT_LOSS += profit_rate
            logger.info(f"📉 [손절 실행] {trade['name']} | 수익률: {profit_rate:.2f}% | 당일 누적: {TOTAL_PROFIT_LOSS:.2f}% | 매도가: {curr_price:,}")
            del MY_PORTFOLIO[code]
            if code in TRACKER_DICT: del TRACKER_DICT[code]
            return True
    
    else:
        # 집중 모니터링 로그 기록 (경과 시간 포함)
        logger.info(f"🔍 [집중모니터링] {trade['name']}: {profit_rate:+.2f}% ({curr_price:,}) | {elapsed_time:.1f}분 경과")
        return False
# endregion 매수 알고리즘

def main():
    global TOKEN_REAL, TOKEN_TRADE
    logger.info("="*70)
    logger.info(f"⚡ [자동 트레이딩 엔진 V4.5] 모드: {MODE}")
    logger.info("⚡ 매수 가능 시간: 09:00 ~ 10:30")
    logger.info("⚡ 강제 청산 시간: 15:20")
    logger.info("="*70 + "\n")
    
    # 1. 조회용 토큰 (항상 REAL)
    TOKEN_REAL = get_access_token(REAL_APP_KEY, REAL_APP_SECRET, URL_REAL)
    
    # 2. 매매용 토큰 (REAL/SIM이면 조회용 재사용, PAPER면 별도 발급)
    if MODE in ["REAL", "SIM"]:
        TOKEN_TRADE = TOKEN_REAL
        logger.info("✅ 실거래 모드: 동일 계정 토큰을 공유합니다.")
    else:
        TOKEN_TRADE = get_access_token(TRADE_APP_KEY, TRADE_APP_SECRET, URL_TRADE)
        logger.info("✅ 모의투자 모드: 조회(REAL)와 매매(PAPER) 토큰을 각각 발급했습니다.")
    
    if not TOKEN_REAL or not TOKEN_TRADE:
        logger.error("❌ 토큰 발급 실패. 설정을 확인하세요.")
        return

    while True:
        try:
            now = datetime.datetime.now()
            current_time = now.strftime("%H%M")
            
            # 1. 휴장 시간 (오전 9시 전)
            if current_time < "0900":
                sys.stdout.write(f"\r😴 [대기] {now.strftime('%H:%M:%S')} - 장 시작 전...     ")
                sys.stdout.flush()
                time.sleep(10); continue

            # 2. 강제 청산 및 종료 (15:20 이후)
            if current_time >= "1520":
                if MY_PORTFOLIO:
                    logger.warning("🚨 [장 마감] 미청산 종목 강제 던지기 실행!")
                    for code in list(MY_PORTFOLIO.keys()):
                        trade = MY_PORTFOLIO[code]
                        curr_price = get_current_price(code) or trade['entry_price']
                        profit_rate = (curr_price - trade['entry_price']) / trade['entry_price'] * 100
                        TOTAL_PROFIT_LOSS += profit_rate
                        logger.info(f"🔥 [강제매도] {trade['name']}({code}) | 수익률: {profit_rate:.2f}% | 당일 누적: {TOTAL_PROFIT_LOSS:.2f}%")
                        del MY_PORTFOLIO[code]
                        if code in TRACKER_DICT: del TRACKER_DICT[code]
                
                sys.stdout.write(f"\r🍺 [종료] {now.strftime('%H:%M:%S')} - 오늘 매매 끝! 고생하셨습니다.     ")
                sys.stdout.flush()
                time.sleep(60); continue

            # 3. 집중 모니터링 모드 (보유 종목이 있을 때)
            if MY_PORTFOLIO:
                for code in list(MY_PORTFOLIO.keys()):
                    curr_price = get_current_price(code)
                    if curr_price:
                        manage_exit_strategy(code, curr_price)
                    time.sleep(0.2)
            
            # 4. 스캔 모드 (보유 종목 없고, 10:30 이전일 때만 신규 매수)
            else:
                if current_time < "1030":
                    surging, msg = get_surging_stocks()
                    if surging:
                        process_trading_logic(surging)
                    else:
                        sys.stdout.write(f"\r🔍 [스캔중] {msg}... 대상 없음         ")
                        sys.stdout.flush()
                else:
                    sys.stdout.write(f"\r🌾 [관망] {now.strftime('%H:%M:%S')} - 신규 매수 금지 시간 (수확기)    ")
                    sys.stdout.flush()
            
            time.sleep(0.5) 
        except KeyboardInterrupt: break
        except Exception as e: 
            logger.error(f"메인 루프 에러: {e}")
            time.sleep(2)

if __name__ == "__main__":
    main()
