import os
import re
from datetime import datetime, timedelta
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# 設定頁面標題與佈局
st.set_page_config(
    page_title="股市大亨 - 技術籌碼診斷系統", page_icon="📈", layout="wide"
)

# 快取設定
cache_dir = os.path.join(os.getcwd(), ".cache")
os.makedirs(cache_dir, exist_ok=True)
yf.set_tz_cache_location(cache_dir)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# 1. 熱門話題族群
INDUSTRY_GROUPS = {
    "電子類-PCB載板與硬板": [
        "3037",
        "8046",
        "3189",
        "2313",
        "2368",
        "3044",
        "6269",
    ],
    "電子類-PCB上游材料/CCL": ["6213", "6274", "8358", "5483", "1802"],
    "半導體-先進封裝/CoWoS": ["2330", "3711", "6239", "3264", "6187", "3583"],
    "電腦週邊-AI伺服器代工": ["2382", "3231", "2317", "2357", "2377", "6669"],
    "光電網通-矽光子/CPO": ["3081", "4979", "3363", "3450", "2345"],
    "電機綠能-重電/算力基建": ["1519", "1503", "1513", "2308"],
    "半導體-記憶體/DRAM": ["2408", "2337", "2344", "8299"],
    "半導體-IC設計/矽智財": ["2454", "3661", "3443", "6531"],
    "航運類-貨櫃航運": ["2603", "2609", "2615"],
    "航運類-航空雙雄": ["2618", "2610"],
}

STOCK_TO_GROUP = {}
for group_name, members in INDUSTRY_GROUPS.items():
    for stock_code in members:
        clean_code = str(stock_code).strip()
        if clean_code not in STOCK_TO_GROUP:
            STOCK_TO_GROUP[clean_code] = []
        STOCK_TO_GROUP[clean_code].append(group_name)


@st.cache_data(ttl=3600)
def load_taiwan_stock_official_list():
    code_to_name, name_to_code, official_industry, market_type = (
        {},
        {},
        {},
        {},
    )

    try:
        url_twse = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
        res = requests.get(url_twse, headers=HEADERS, timeout=4)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get("Code", "")).strip()
                name = str(item.get("Name", "")).strip()
                if code and name:
                    code_to_name[code] = name
                    name_to_code[name] = code
                    market_type[code] = "上市"
    except Exception as e:
        print(f"證交所 API 清單載入異常: {e}")

    try:
        url_ind = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
        res = requests.get(url_ind, headers=HEADERS, timeout=4)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get("公司代號", "")).strip()
                ind = str(item.get("產業別", "")).strip()
                if code and ind:
                    official_industry[code] = ind
    except Exception as e:
        print(f"證交所產業分類載入異常: {e}")

    try:
        url_tpex = "https://www.tpex.org.tw/openapi/v1/mopsfront_t187ap03_O"
        res = requests.get(url_tpex, headers=HEADERS, timeout=4)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get("公司代號", "")).strip()
                name = str(item.get("公司簡稱", "")).strip()
                ind = str(item.get("產業別", "")).strip()
                if code:
                    if name:
                        code_to_name[code] = name
                        name_to_code[name] = code
                    market_type[code] = "上櫃"
                    if ind:
                        official_industry[code] = ind
    except Exception as e:
        print(f"櫃買中心 API 清單載入異常: {e}")

    return code_to_name, name_to_code, official_industry, market_type


(
    STOCK_CODE_TO_NAME,
    STOCK_NAME_TO_CODE,
    STOCK_OFFICIAL_INDUSTRY,
    STOCK_MARKET_TYPE,
) = load_taiwan_stock_official_list()


def fetch_chinese_name_online(stock_id: str) -> str:
    try:
        url = f"https://tw.stock.yahoo.com/quote/{stock_id}"
        res = requests.get(url, headers=HEADERS, timeout=3)
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(res.text, "html.parser")
        h1 = soup.find("h1")
        if h1:
            title = h1.text.strip()
            clean_name = re.sub(
                r"\([^)]*\)|Yahoo|股市|行情|個股", "", title
            ).strip()
            if clean_name and clean_name != stock_id:
                return clean_name
    except Exception:
        pass
    return "台股個股"


def resolve_stock_info(input_str: str) -> tuple[str, str]:
    query = str(input_str).strip().replace(" ", "")
    if query.isdigit():
        name = STOCK_CODE_TO_NAME.get(query)
        if name:
            return query, name
        c_name = fetch_chinese_name_online(query)
        STOCK_CODE_TO_NAME[query] = c_name
        return query, c_name

    if query in STOCK_NAME_TO_CODE:
        return STOCK_NAME_TO_CODE[query], query

    for name, code in STOCK_NAME_TO_CODE.items():
        if query in name or name in query:
            return code, name

    return query, fetch_chinese_name_online(query)


def get_realtime_quote(stock_id: str) -> dict:
    quote = {
        "current_price": 0.0,
        "change": 0.0,
        "pct_change": 0.0,
        "arrow": "➡️",
        "color": "black",
        "summary": "即時行情載入中...",
    }
    try:
        url = f"https://tw.stock.yahoo.com/quote/{stock_id}"
        res = requests.get(url, headers=HEADERS, timeout=3)
        if res.status_code == 200:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(res.text, "html.parser")
            price, open_price = 0.0, 0.0

            price_el = soup.find(
                "span",
                class_=re.compile(
                    r"Fz\(32px\)|Fz\(36px\)|Fz\(28px\)|C\(\$c-trend-.*?\)"
                ),
            )
            if not price_el:
                price_el = soup.find("span", {"data-price": True})

            if price_el:
                price_str = (
                    price_el.get("data-price")
                    or price_el.text.replace(",", "").strip()
                )
                try:
                    price = float(price_str)
                except ValueError:
                    price = 0.0

            if price > 0:
                quote.update(
                    {
                        "current_price": price,
                        "summary": f"最新價: ${price:.2f} [即時]",
                    }
                )
    except Exception:
        pass
    return quote


def get_group_status(stock_id: str) -> dict:
    clean_stock_id = str(stock_id).strip()
    market_type = STOCK_MARKET_TYPE.get(clean_stock_id, "台股")
    official_ind = STOCK_OFFICIAL_INDUSTRY.get(
        clean_stock_id, "電子/一般產業"
    )
    groups = STOCK_TO_GROUP.get(clean_stock_id, [])

    if groups:
        primary_group = groups[0]
        category_title = f"[{market_type}] {official_ind} ({primary_group})"
    else:
        category_title = f"[{market_type}] {official_ind}"

    return {
        "group_name": category_title,
        "status_text": "產業狀態正常分析中",
    }


def get_financial_and_analyst_data(ticker: yf.Ticker) -> dict:
    info = ticker.info
    fin_data = {
        "analyst_target": "無數據",
        "revenue_yoy": "無數據",
        "eps": "無數據",
        "est_eps": "無數據",
        "pe_ratio": "無數據",
        "pb_ratio": "無數據",
        "fundamental_score": 0,
        "fundamental_signals": [],
    }

    try:
        target_mean = info.get("targetMeanPrice")
        if target_mean:
            fin_data["analyst_target"] = f"${target_mean:.2f}"

        pe = info.get("trailingPE")
        pb = info.get("priceToBook")
        if pe:
            fin_data["pe_ratio"] = f"{pe:.2f} 倍"
        if pb:
            fin_data["pb_ratio"] = f"{pb:.2f} 倍"

        est_eps = info.get("forwardEps")
        if est_eps:
            fin_data["est_eps"] = f"${est_eps:.2f}"

        eps = info.get("trailingEps")
        if eps is not None:
            fin_data["eps"] = f"${eps:.2f}"

        rev_growth = info.get("revenueGrowth")
        if rev_growth is not None:
            rev_yoy_pct = rev_growth * 100
            fin_data["revenue_yoy"] = f"{rev_yoy_pct:+.2f}%"
            if rev_yoy_pct > 15:
                fin_data["fundamental_score"] += 3
                fin_data["fundamental_signals"].append(
                    f"• [基本面強勁] 營收 YoY 正成長 ({rev_yoy_pct:+.2f}%) (+3分)"
                )
            elif rev_yoy_pct < -15:
                fin_data["fundamental_score"] -= 3
                fin_data["fundamental_signals"].append(
                    f"• [基本面衰退] 營收 YoY 負成長 ({rev_yoy_pct:+.2f}%) (-3分)"
                )
    except Exception:
        pass

    return fin_data


def get_tech_data(stock_id: str, stock_name: str) -> dict:
    try:
        ticker_symbol = f"{stock_id}.TW"
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(period="6mo")

        if df.empty:
            ticker_symbol = f"{stock_id}.TWO"
            ticker = yf.Ticker(ticker_symbol)
            df = ticker.history(period="6mo")

        if df.empty or len(df) < 20:
            return {
                "error": f"無法獲取股票代碼 {stock_id} ({stock_name}) 充足的歷史數據"
            }

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        realtime_quote = get_realtime_quote(stock_id)
        fin_data = get_financial_and_analyst_data(ticker)

        df["MA10"] = df["Close"].rolling(window=10).mean()
        df["MA20"] = df["Close"].rolling(window=20).mean()
        df["MA60"] = df["Close"].rolling(window=60).mean()

        latest = df.iloc[-1]
        close_price = realtime_quote["current_price"] or latest["Close"]
        latest_date_str = df.index[-1].strftime("%Y-%m-%d")

        ma20 = latest["MA20"]
        ma60 = latest["MA60"] if not pd.isna(latest["MA60"]) else ma20
        bias_20 = ((close_price - ma20) / ma20) * 100

        signals = []
        tech_score = fin_data["fundamental_score"]
        signals.extend(fin_data["fundamental_signals"])

        if close_price >= ma20 and close_price >= ma60:
            ma_analysis_str = f"處於多頭格局，月線(${ma20:.1f}) 與季線(${ma60:.1f}) 為下檔強支撐"
            signals.append(f"• [均線支撐] {ma_analysis_str}")
        else:
            ma_analysis_str = f"處於調整或空頭格局，月線(${ma20:.1f}) 與季線(${ma60:.1f}) 為上方壓力"
            signals.append(f"• [均線狀態] {ma_analysis_str}")

        bias_str = f"20MA 乖離率: {bias_20:+.2f}%"

        trend_status = "NEUTRAL"
        if close_price > ma20 and ma20 > ma60:
            trend_status = "BULL"
            tech_score += 3
        elif close_price < ma20 and ma20 < ma60:
            trend_status = "BEAR"
            tech_score -= 3

        high_6m = df["High"].max()
        low_6m = df["Low"].min()
        diff = high_6m - low_6m
        bull_target = close_price + (diff * 0.382)

        return {
            "stock_name": stock_name,
            "close_price": close_price,
            "latest_date": latest_date_str,
            "realtime_quote": realtime_quote,
            "trend_status": trend_status,
            "score": tech_score,
            "signals": signals,
            "ma_analysis": ma_analysis_str,
            "bias_str": bias_str,
            "bull_target": bull_target,
            "financials": fin_data,
        }
    except Exception as e:
        return {"error": f"資料處理異常: {e}"}


# UI 介面設定
st.title("📈 股市大亨 - 產業與技術籌碼診斷系統")

user_input = st.text_input(
    "請輸入股票代碼或名稱 (例如: 2330 / 華通):", "2330"
)

if st.button("開始分析"):
    with st.spinner("資料讀取與計算中..."):
        stock_id, stock_name = resolve_stock_info(user_input)
        tech_data = get_tech_data(stock_id, stock_name)

        if "error" in tech_data:
            st.error(tech_data["error"])
        else:
            group_status = get_group_status(stock_id)

            # 頂部資訊卡片
            col1, col2, col3 = st.columns(3)
            col1.metric("標的名稱", f"{stock_id} {stock_name}")
            col2.metric("最新價格", f"${tech_data['close_price']:.2f}")
            col3.metric("總評分", f"{tech_data['score']} 分")

            st.markdown("---")

            # 詳細數據顯示
            st.subheader("📊 核心指標概覽")
            st.write(
                f"**產業與市場別**：{group_status['group_name']}"
            )
            st.write(f"**均線狀態**：{tech_data['ma_analysis']}")
            st.write(f"**乖離率**：{tech_data['bias_str']}")

            fin = tech_data["financials"]
            col_a, col_b, col_c = st.columns(3)
            col_a.write(f"法人目標價：{fin['analyst_target']}")
            col_b.write(f"近4季 EPS：{fin['eps']}")
            col_c.write(f"營收 YoY：{fin['revenue_yoy']}")

            st.markdown("---")

            # 分析訊號
            st.subheader("🔍 分析訊號導覽")
            for sig in tech_data["signals"]:
                st.write(sig)