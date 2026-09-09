import os
import re
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(
    page_title="股市大亨 - 技術籌碼診斷系統", page_icon="📈", layout="wide"
)

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
    except Exception:
        pass

    try:
        url_ind = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
        res = requests.get(url_ind, headers=HEADERS, timeout=4)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get("公司代號", "")).strip()
                ind = str(item.get("產業別", "")).strip()
                if code and ind:
                    official_industry[code] = ind
    except Exception:
        pass

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
    except Exception:
        pass

    return code_to_name, name_to_code, official_industry, market_type


(
    STOCK_CODE_TO_NAME,
    STOCK_NAME_TO_CODE,
    STOCK_OFFICIAL_INDUSTRY,
    STOCK_MARKET_TYPE,
) = load_taiwan_stock_official_list()


def resolve_stock_info(input_str: str) -> tuple[str, str]:
    query = str(input_str).strip().replace(" ", "")
    if query.isdigit():
        name = STOCK_CODE_TO_NAME.get(query, "台股個股")
        return query, name

    if query in STOCK_NAME_TO_CODE:
        return STOCK_NAME_TO_CODE[query], query

    for name, code in STOCK_NAME_TO_CODE.items():
        if query in name or name in query:
            return code, name

    return query, "台股個股"


def fetch_comprehensive_financials(stock_id: str, ticker: yf.Ticker) -> dict:
    """雙重備援機制：結合線上爬蟲與 yfinance 補齊所有財務面數據與法人目標價"""
    fin_data = {
        "analyst_target": "無資料",
        "target_range": "無資料",
        "revenue_yoy": "無資料",
        "eps": "無資料",
        "est_eps": "無資料",
        "pe_ratio": "無資料",
        "pb_ratio": "無資料",
        "fundamental_score": 0,
        "fundamental_signals": [],
    }

    # 1. 第一層：由 Yahoo 股市個股頁面爬取本益比、每股盈餘、目標價
    try:
        url = f"https://tw.stock.yahoo.com/quote/{stock_id}/profile"
        res = requests.get(url, headers=HEADERS, timeout=3)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            # 搜尋目標價與財務文字區塊
            text = soup.get_text()

            target_m = re.search(r"目標價[^\d]*(\d+(?:\.\d+)?)", text)
            if target_m:
                fin_data["analyst_target"] = f"${float(target_m.group(1)):.2f}"

            pe_m = re.search(r"本益比[^\d]*(\d+(?:\.\d+)?)", text)
            if pe_m:
                fin_data["pe_ratio"] = f"{float(pe_m.group(1)):.2f} 倍"

            eps_m = re.search(r"EPS[^\d]*([-+]?\d+(?:\.\d+)?)", text)
            if eps_m:
                fin_data["eps"] = f"${float(eps_m.group(1)):.2f}"
    except Exception:
        pass

    # 2. 第二層：補強 yfinance 財務指標
    try:
        info = ticker.info or {}

        # 目標價補算
        target_mean = info.get("targetMeanPrice")
        target_high = info.get("targetHighPrice")
        target_low = info.get("targetLowPrice")

        if target_mean and float(target_mean) > 0:
            fin_data["analyst_target"] = f"${float(target_mean):.2f}"
            if target_high and target_low:
                fin_data["target_range"] = (
                    f"${float(target_low):.1f} ~ ${float(target_high):.1f}"
                )

        if fin_data["pe_ratio"] == "無資料":
            pe = info.get("trailingPE") or info.get("forwardPE")
            if pe:
                fin_data["pe_ratio"] = f"{float(pe):.2f} 倍"

        if fin_data["pb_ratio"] == "無資料":
            pb = info.get("priceToBook")
            if pb:
                fin_data["pb_ratio"] = f"{float(pb):.2f} 倍"

        if fin_data["eps"] == "無資料":
            eps = info.get("trailingEps")
            if eps is not None:
                fin_data["eps"] = f"${float(eps):.2f}"

        est_eps = info.get("forwardEps")
        if est_eps:
            fin_data["est_eps"] = f"${float(est_eps):.2f}"

        rev_growth = info.get("revenueGrowth")
        if rev_growth is not None:
            rev_yoy_pct = float(rev_growth) * 100
            fin_data["revenue_yoy"] = f"{rev_yoy_pct:+.2f}%"
            if rev_yoy_pct > 15:
                fin_data["fundamental_score"] += 3
                fin_data["fundamental_signals"].append(
                    f"• [基本面強勁] 近期營收 YoY 強勢成長 ({rev_yoy_pct:+.2f}%) (+3分)"
                )
            elif rev_yoy_pct < -15:
                fin_data["fundamental_score"] -= 3
                fin_data["fundamental_signals"].append(
                    f"• [基本面衰退] 近期營收 YoY 呈現衰退 ({rev_yoy_pct:+.2f}%) (-3分)"
                )
    except Exception:
        pass

    return fin_data


def get_chip_data(stock_id: str) -> dict:
    chip = {
        "foreign_buy": "無資料",
        "trust_buy": "無資料",
        "dealer_buy": "無資料",
        "major_buy": "無資料",
        "chip_score": 0,
        "chip_signals": [],
    }
    try:
        url = f"https://tw.stock.yahoo.com/quote/{stock_id}/institutional-trading"
        res = requests.get(url, headers=HEADERS, timeout=4)
        if res.status_code == 200:
            text = BeautifulSoup(res.text, "html.parser").get_text()

            pats = {
                "foreign_buy": r"外資[^\d\-+]*?([-+]?\d[\d,]*)\s*張",
                "trust_buy": r"投信[^\d\-+]*?([-+]?\d[\d,]*)\s*張",
                "dealer_buy": r"自營商[^\d\-+]*?([-+]?\d[\d,]*)\s*張",
                "major_buy": r"主力[^\d\-+]*?([-+]?\d[\d,]*)\s*張",
            }
            for k, p in pats.items():
                m = re.search(p, text)
                if m:
                    v = int(m.group(1).replace(",", ""))
                    chip[k] = (
                        f"買超 {v:,} 張"
                        if v > 0
                        else (f"賣超 {abs(v):,} 張" if v < 0 else "無變化")
                    )

        if "買超" in chip["foreign_buy"]:
            chip["chip_score"] += 2
            chip["chip_signals"].append(
                f"• [籌碼利多] 外資呈現買超 ({chip['foreign_buy']}) (+2分)"
            )
        if "買超" in chip["trust_buy"]:
            chip["chip_score"] += 2
            chip["chip_signals"].append(
                f"• [籌碼利多] 投信加碼買超 ({chip['trust_buy']}) (+2分)"
            )
    except Exception:
        pass
    return chip


def get_tech_data(stock_id: str, stock_name: str) -> dict:
    try:
        ticker = yf.Ticker(f"{stock_id}.TW")
        df = ticker.history(period="6mo")
        if df.empty:
            ticker = yf.Ticker(f"{stock_id}.TWO")
            df = ticker.history(period="6mo")

        if df.empty or len(df) < 10:
            return {
                "error": f"無法獲取股票代碼 {stock_id} ({stock_name}) 充足的歷史數據"
            }

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        latest_close = float(df["Close"].iloc[-1])
        latest_date_str = df.index[-1].strftime("%Y-%m-%d")

        fin_data = fetch_comprehensive_financials(stock_id, ticker)
        chip_data = get_chip_data(stock_id)

        df["MA20"] = df["Close"].rolling(window=20).mean()
        df["MA60"] = df["Close"].rolling(window=60).mean()

        latest = df.iloc[-1]
        ma20 = (
            float(latest["MA20"])
            if not pd.isna(latest["MA20"])
            else latest_close
        )
        ma60 = float(latest["MA60"]) if not pd.isna(latest["MA60"]) else ma20
        bias_20 = ((latest_close - ma20) / ma20) * 100

        signals = []
        total_score = fin_data["fundamental_score"] + chip_data["chip_score"]
        signals.extend(fin_data["fundamental_signals"])
        signals.extend(chip_data["chip_signals"])

        if latest_close >= ma20 and latest_close >= ma60:
            ma_str = f"多頭格局，月線(${ma20:.1f}) 與季線(${ma60:.1f}) 為支撐"
        else:
            ma_str = f"調整或空頭格局，月線(${ma20:.1f}) 與季線(${ma60:.1f}) 為上方壓力"

        trend_status = "盤整態勢"
        if latest_close > ma20 and ma20 > ma60:
            trend_status = "強勢多頭"
            total_score += 3
        elif latest_close < ma20 and ma20 < ma60:
            trend_status = "弱勢空頭"
            total_score -= 3

        high_6m = float(df["High"].max())
        low_6m = float(df["Low"].min())
        diff = high_6m - low_6m

        return {
            "stock_name": stock_name,
            "close_price": latest_close,
            "latest_date": latest_date_str,
            "trend_status": trend_status,
            "score": total_score,
            "signals": signals,
            "ma_analysis": ma_str,
            "bias_str": f"{bias_20:+.2f}%",
            "high_6m": high_6m,
            "low_6m": low_6m,
            "bull_target": latest_close + (diff * 0.382),
            "bear_target": max(0.0, latest_close - (diff * 0.382)),
            "financials": fin_data,
            "chips": chip_data,
        }
    except Exception as e:
        return {"error": f"資料讀取失敗: {e}"}


# ---------------- UI 畫面配置 ----------------
st.title("📈 股市大亨 - 產業技術籌碼全方位診斷系統")

user_input = st.text_input(
    "請輸入股票代碼或名稱 (例如: 2330 / 華通):", "2330"
)

if st.button("開始全面診斷", type="primary"):
    with st.spinner("正在讀取並解析即時籌碼與財務數據..."):
        stock_id, stock_name = resolve_stock_info(user_input)
        tech_data = get_tech_data(stock_id, stock_name)

        if "error" in tech_data:
            st.error(tech_data["error"])
        else:
            market_type = STOCK_MARKET_TYPE.get(stock_id, "台股")
            official_ind = STOCK_OFFICIAL_INDUSTRY.get(stock_id, "一般產業")
            groups = STOCK_TO_GROUP.get(stock_id, [])
            group_display = (
                f"[{market_type}] {official_ind} ({groups[0]})"
                if groups
                else f"[{market_type}] {official_ind}"
            )

            fin = tech_data["financials"]
            chip = tech_data["chips"]

            # 1. 主數據列
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("標的名稱", f"{stock_id} {stock_name}")
            col2.metric("最新收盤/即時價", f"${tech_data['close_price']:.2f}")
            col3.metric("技術籌碼趨勢", tech_data["trend_status"])
            col4.metric("綜合診斷總分", f"{tech_data['score']} 分")

            st.markdown("---")

            # 2. 財務與法人目標價重點區
            st.subheader("🎯 法人與投信目標價預估")
            col_f1, col_f2, col_f3 = st.columns(3)
            col_f1.metric("法人平均目標價", fin["analyst_target"])
            col_f2.metric("法人目標價區間", fin["target_range"])
            col_f3.metric("技術面多頭目標價 (+0.382)", f"${tech_data['bull_target']:.2f}")

            st.markdown("---")

            # 3. 籌碼與財務數據分頁
            st.subheader("📊 財務面與籌碼面明細")
            tab1, tab2 = st.tabs(["💰 財務基本面指標", "🏛️ 法人與主力籌碼"])

            with tab1:
                f1, f2, f3, f4 = st.columns(4)
                f1.metric("近 4 季 EPS", fin["eps"])
                f2.metric("預估 Forward EPS", fin["est_eps"])
                f3.metric("本益比 (P/E)", fin["pe_ratio"])
                f4.metric("股價淨值比 (P/B)", fin["pb_ratio"])
                st.write(f"**近全年度營收年增率 (YoY)**：{fin['revenue_yoy']}")

            with tab2:
                c1, c2, c3 = st.columns(3)
                c1.metric("外資買賣超", chip["foreign_buy"])
                c2.metric("投信買賣超", chip["trust_buy"])
                c3.metric("自營商買賣超", chip["dealer_buy"])

                c4, c5 = st.columns(2)
                c4.metric("主力籌碼動向", chip["major_buy"])
                c5.metric("資料更新日期", tech_data["latest_date"])

            st.markdown("---")

            # 4. 診斷訊號導覽
            st.subheader("🔍 綜合診斷訊號與建議")
            for sig in tech_data["signals"]:
                st.write(sig)