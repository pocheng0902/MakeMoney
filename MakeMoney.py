import os
import re
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# 頁面標題與佈局
st.set_page_config(
    page_title="股市大亨 - 技術籌碼診斷系統", page_icon="📈", layout="wide"
)

# 快取目錄設定
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

# 1. 熱門話題族群對照
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


def get_realtime_quote(stock_id: str) -> float:
    try:
        url = f"https://tw.stock.yahoo.com/quote/{stock_id}"
        res = requests.get(url, headers=HEADERS, timeout=4)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            # 依據 data-price 屬性或搜尋特定包含價格的標籤
            price_el = soup.find("span", {"data-price": True})
            if price_el and price_el.get("data-price"):
                return float(price_el["data-price"])

            # 備用方案：搜尋網頁中的價格字串
            for el in soup.find_all(["span", "div"]):
                if "Fz(32px)" in str(el) or "Fz(36px)" in str(el):
                    txt = el.text.replace(",", "").strip()
                    if re.match(r"^\d+(\.\d+)?$", txt):
                        return float(txt)
    except Exception:
        pass
    return 0.0


def get_chip_and_institutional_data(stock_id: str) -> dict:
    """強化版籌碼解析：改採純文字匹配避免標籤變化問題"""
    chip = {
        "foreign_buy": "待更新",
        "trust_buy": "待更新",
        "dealer_buy": "待更新",
        "major_buy": "待更新",
        "chip_score": 0,
        "chip_signals": [],
    }

    try:
        url = f"https://tw.stock.yahoo.com/quote/{stock_id}/institutional-trading"
        res = requests.get(url, headers=HEADERS, timeout=4)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            text_content = soup.get_text()

            # 解析三大法人買賣超 (搜尋關鍵字與數字)
            patterns = {
                "foreign_buy": r"外資[^\d\-+]*?([-+]?\d[\d,]*)\s*張",
                "trust_buy": r"投信[^\d\-+]*?([-+]?\d[\d,]*)\s*張",
                "dealer_buy": r"自營商[^\d\-+]*?([-+]?\d[\d,]*)\s*張",
                "major_buy": r"主力[^\d\-+]*?([-+]?\d[\d,]*)\s*張",
            }

            for key, pat in patterns.items():
                match = re.search(pat, text_content)
                if match:
                    val_str = match.group(1).replace(",", "")
                    try:
                        val = int(val_str)
                        if val > 0:
                            chip[key] = f"買超 {val:,} 張"
                        elif val < 0:
                            chip[key] = f"賣超 {abs(val):,} 張"
                        else:
                            chip[key] = "無變化"
                    except ValueError:
                        pass

            # 若無法透過正則捕獲，改為列舉表格行
            if chip["foreign_buy"] == "待更新":
                rows = soup.find_all("li") or soup.find_all("tr")
                for row in rows:
                    r_text = row.text.replace(" ", "")
                    if "外資" in r_text:
                        chip["foreign_buy"] = parse_row_value(r_text)
                    elif "投信" in r_text:
                        chip["trust_buy"] = parse_row_value(r_text)
                    elif "自營商" in r_text:
                        chip["dealer_buy"] = parse_row_value(r_text)

        # 評分機制
        if "買超" in chip["foreign_buy"]:
            chip["chip_score"] += 2
            chip["chip_signals"].append(
                f"• [籌碼利多] 外資呈現買超 ({chip['foreign_buy']}) (+2分)"
            )
        elif "賣超" in chip["foreign_buy"]:
            chip["chip_score"] -= 2
            chip["chip_signals"].append(
                f"• [籌碼偏空] 外資呈現賣超 ({chip['foreign_buy']}) (-2分)"
            )

        if "買超" in chip["trust_buy"]:
            chip["chip_score"] += 2
            chip["chip_signals"].append(
                f"• [籌碼利多] 投信加碼買超 ({chip['trust_buy']}) (+2分)"
            )
    except Exception:
        pass
    return chip


def parse_row_value(text: str) -> str:
    nums = re.findall(r"[-+]?\d[\d,]*", text)
    if nums:
        try:
            val = int(nums[0].replace(",", ""))
            return f"買超 {val:,} 張" if val > 0 else f"賣超 {abs(val):,} 張"
        except ValueError:
            pass
    return "無資料"


def get_financial_and_analyst_data(ticker: yf.Ticker) -> dict:
    fin_data = {
        "analyst_target": "暫無資料",
        "revenue_yoy": "暫無資料",
        "eps": "暫無資料",
        "est_eps": "暫無資料",
        "pe_ratio": "暫無資料",
        "pb_ratio": "暫無資料",
        "fundamental_score": 0,
        "fundamental_signals": [],
    }

    try:
        info = ticker.info or {}

        target_mean = info.get("targetMeanPrice")
        if target_mean and float(target_mean) > 0:
            fin_data["analyst_target"] = f"${target_mean:.2f}"

        pe = info.get("trailingPE")
        if pe:
            fin_data["pe_ratio"] = f"{float(pe):.2f} 倍"

        pb = info.get("priceToBook")
        if pb:
            fin_data["pb_ratio"] = f"{float(pb):.2f} 倍"

        est_eps = info.get("forwardEps")
        if est_eps:
            fin_data["est_eps"] = f"${float(est_eps):.2f}"

        eps = info.get("trailingEps")
        if eps is not None:
            fin_data["eps"] = f"${float(eps):.2f}"

        rev_growth = info.get("revenueGrowth")
        if rev_growth is not None:
            rev_yoy_pct = float(rev_growth) * 100
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
        # 上市 (.TW) / 上櫃 (.TWO) 自動嘗試
        df = pd.DataFrame()
        ticker = yf.Ticker(f"{stock_id}.TW")
        df = ticker.history(period="6mo")

        if df.empty:
            ticker = yf.Ticker(f"{stock_id}.TWO")
            df = ticker.history(period="6mo")

        if df.empty or len(df) < 10:
            return {
                "error": f"無法獲取股票代碼 {stock_id} ({stock_name}) 充足的歷史數據，請確認代碼是否正確。"
            }

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        rt_price = get_realtime_quote(stock_id)
        latest_close = (
            rt_price if rt_price > 0 else float(df["Close"].iloc[-1])
        )

        fin_data = get_financial_and_analyst_data(ticker)
        chip_data = get_chip_and_institutional_data(stock_id)

        df["MA10"] = df["Close"].rolling(window=10).mean()
        df["MA20"] = df["Close"].rolling(window=20).mean()
        df["MA60"] = df["Close"].rolling(window=60).mean()

        latest = df.iloc[-1]
        latest_date_str = df.index[-1].strftime("%Y-%m-%d")

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
            ma_analysis_str = f"處於多頭格局，月線(${ma20:.1f}) 與季線(${ma60:.1f}) 為下檔強支撐"
            signals.append(f"• [均線支撐] {ma_analysis_str}")
        else:
            ma_analysis_str = f"處於調整或空頭格局，月線(${ma20:.1f}) 與季線(${ma60:.1f}) 為上方壓力"
            signals.append(f"• [均線狀態] {ma_analysis_str}")

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

        bull_target = latest_close + (diff * 0.382)
        bear_target = max(0.0, latest_close - (diff * 0.382))

        return {
            "stock_name": stock_name,
            "close_price": latest_close,
            "latest_date": latest_date_str,
            "trend_status": trend_status,
            "score": total_score,
            "signals": signals,
            "ma_analysis": ma_analysis_str,
            "bias_str": f"{bias_20:+.2f}%",
            "high_6m": high_6m,
            "low_6m": low_6m,
            "bull_target": bull_target,
            "bear_target": bear_target,
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

            # 2. 技術與產業
            st.subheader("📌 產業與技術指標")
            st.write(f"**所屬產業族群**：{group_display}")
            st.write(f"**均線排列結構**：{tech_data['ma_analysis']}")

            col_t1, col_t2, col_t3, col_t4 = st.columns(4)
            col_t1.metric("20MA 乖離率", tech_data["bias_str"])
            col_t2.metric("近 6 個月最高", f"${tech_data['high_6m']:.2f}")
            col_t3.metric("近 6 個月最低", f"${tech_data['low_6m']:.2f}")
            col_t4.metric("法人平均目標價", fin["analyst_target"])

            col_tg1, col_tg2 = st.columns(2)
            col_tg1.metric(
                "多頭推算目標價 (+0.382)",
                f"${tech_data['bull_target']:.2f}",
            )
            col_tg2.metric(
                "空頭推算支撐價 (-0.382)",
                f"${tech_data['bear_target']:.2f}",
            )

            st.markdown("---")

            # 3. 籌碼與基本面
            st.subheader("📊 籌碼面與基本面數據")
            tab1, tab2 = st.tabs(["🏛️ 法人與主力籌碼", "💰 基本面財務指標"])

            with tab1:
                c1, c2, c3 = st.columns(3)
                c1.metric("外資買賣超", chip["foreign_buy"])
                c2.metric("投信買賣超", chip["trust_buy"])
                c3.metric("自營商買賣超", chip["dealer_buy"])

                c4, c5 = st.columns(2)
                c4.metric("主力籌碼動向", chip["major_buy"])
                c5.metric("資料更新日期", tech_data["latest_date"])

            with tab2:
                f1, f2, f3, f4 = st.columns(4)
                f1.metric("近 4 季累計 EPS", fin["eps"])
                f2.metric("預估 Forward EPS", fin["est_eps"])
                f3.metric("本益比 (P/E)", fin["pe_ratio"])
                f4.metric("股價淨值比 (P/B)", fin["pb_ratio"])
                st.caption(f"全年度營收年增率 (YoY)：{fin['revenue_yoy']}")

            st.markdown("---")

            # 4. 診斷訊號
            st.subheader("🔍 綜合診斷訊號與建議")
            for sig in tech_data["signals"]:
                st.write(sig)