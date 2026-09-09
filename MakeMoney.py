import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st
import yfinance as yf
from bs4 import BeautifulSoup

# ==========================================
# 0. 頁面配置與快取設定
# ==========================================
st.set_page_config(
    page_title="股市大亨 - 完整台股診斷系統 (Web版)",
    page_icon="📈",
    layout="wide",
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

# ==========================================
# 1. 熱門話題族群
# ==========================================
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


# ==========================================
# 2. 自動載入證交所/櫃買中心官方名單 (Web快取)
# ==========================================
@st.cache_data(ttl=86400)
def load_taiwan_stock_official_list():
    code_to_name = {}
    name_to_code = {}
    official_ind = {}
    market_type = {}

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
                    official_ind[code] = ind
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
                        official_ind[code] = ind
    except Exception as e:
        print(f"櫃買中心 API 清單載入異常: {e}")

    return code_to_name, name_to_code, official_ind, market_type


(
    STOCK_CODE_TO_NAME,
    STOCK_NAME_TO_CODE,
    STOCK_OFFICIAL_INDUSTRY,
    STOCK_MARKET_TYPE,
) = load_taiwan_stock_official_list()


# ==========================================
# 3. 核心抓取與算號邏輯
# ==========================================
def fetch_chinese_name_online(stock_id: str) -> str:
    try:
        url = f"https://tw.stock.yahoo.com/quote/{stock_id}"
        res = requests.get(url, headers=HEADERS, timeout=3)
        soup = BeautifulSoup(res.text, "html.parser")
        h1 = soup.find("h1")
        if h1:
            title = h1.text.strip()
            clean_name = re.sub(
                r"\([^)]*\)|Yahoo|股市|行情|個股", "", title
            ).strip()
            if clean_name and clean_name != stock_id:
                return clean_name
    except Exception as e:
        print(f"線上抓取股名失敗: {e}")
    return "台股個股"


def resolve_stock_info(input_str: str) -> tuple[str, str]:
    query = str(input_str).strip().replace(" ", "")
    if query.isdigit():
        name = STOCK_CODE_TO_NAME.get(query)
        if name:
            return query, name
        c_name = fetch_chinese_name_online(query)
        return query, c_name

    if query in STOCK_NAME_TO_CODE:
        return STOCK_NAME_TO_CODE[query], query

    for name, code in STOCK_NAME_TO_CODE.items():
        if query in name or name in query:
            return code, name

    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&quotesCount=5"
        res = requests.get(url, headers=HEADERS, timeout=3).json()
        for q in res.get("quotes", []):
            symbol = q.get("symbol", "")
            if symbol.endswith(".TW") or symbol.endswith(".TWO"):
                code = symbol.split(".")[0]
                c_name = STOCK_CODE_TO_NAME.get(
                    code
                ) or fetch_chinese_name_online(code)
                return code, c_name
    except Exception as e:
        print(f"線上備援查詢失敗: {e}")

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
            soup = BeautifulSoup(res.text, "html.parser")
            price, prev_close = 0.0, 0.0

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

            close_labels = soup.find_all(
                "span", string=re.compile(r"^前收|^昨日收盤|^前日收盤")
            )
            for l in close_labels:
                parent = l.parent
                if parent:
                    val_el = parent.find_next_sibling() or parent.find(
                        "span", class_=re.compile(r"Fw\(b\)|Fz\(16px\)")
                    )
                    if val_el:
                        try:
                            prev_close = float(
                                val_el.text.replace(",", "").strip()
                            )
                            if prev_close > 0:
                                break
                        except ValueError:
                            pass

            if price > 0:
                base_price = prev_close if prev_close > 0 else price
                change = price - base_price
                pct_change = (
                    (change / base_price) * 100 if base_price > 0 else 0.0
                )

                if change > 0:
                    arrow, color = "🔺", "#d9534f"
                elif change < 0:
                    arrow, color = "🔻", "#5cb85c"
                else:
                    arrow, color = "➡️", "black"

                quote.update(
                    {
                        "current_price": price,
                        "change": change,
                        "pct_change": pct_change,
                        "arrow": arrow,
                        "color": color,
                        "summary": f"最新價: ${price:.2f} {arrow} ({pct_change:+.2f}%) [即時]",
                    }
                )
                return quote
    except Exception as e:
        print(f"Yahoo 網頁爬取失敗 ({stock_id}): {e}")

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
        members = INDUSTRY_GROUPS[primary_group]
    else:
        primary_group = f"官方產業-{official_ind}"
        category_title = f"[{market_type}] {official_ind}"
        members = [clean_stock_id]

    up_count, down_count, total_pct, valid_count = 0, 0, 0.0, 0

    with ThreadPoolExecutor(max_workers=min(len(members), 10)) as executor:
        results = list(
            executor.map(get_realtime_quote, [str(m).strip() for m in members])
        )

    for rt in results:
        if rt and rt.get("current_price", 0) > 0:
            pct = rt.get("pct_change", 0.0)
            total_pct += pct
            valid_count += 1
            if pct > 0.5:
                up_count += 1
            elif pct < -0.5:
                down_count += 1

    if valid_count == 0:
        return {
            "group_name": category_title,
            "status_text": "個股行情載入中...",
            "color": "black",
        }

    avg_pct = total_pct / valid_count

    if len(members) == 1:
        if avg_pct > 0:
            status_text = f"📈 個股走揚 (漲跌幅 {avg_pct:+.2f}%)"
            color = "#d9534f"
        elif avg_pct < 0:
            status_text = f"📉 個股拉回 (漲跌幅 {avg_pct:+.2f}%)"
            color = "#5cb85c"
        else:
            status_text = "⚖️ 平盤震盪"
            color = "#337ab7"
    else:
        if up_count == valid_count or (avg_pct >= 2.0 and down_count == 0):
            status_text = (
                f"🔥 熱度極高 (族群強勢全面走升，平均 {avg_pct:+.2f}%)"
            )
            color = "#d9534f"
        elif avg_pct >= 0.5:
            status_text = (
                f"📈 熱度增溫 (偏多震盪上揚，平均 {avg_pct:+.2f}%)"
            )
            color = "#d9534f"
        elif down_count == valid_count or (avg_pct <= -2.0 and up_count == 0):
            status_text = (
                f"🩸 熱度退燒 (族群弱勢全面走跌，平均 {avg_pct:+.2f}%)"
            )
            color = "#5cb85c"
        elif avg_pct <= -0.5:
            status_text = (
                f"📉 觀望修正 (偏空震盪拉回，平均 {avg_pct:+.2f}%)"
            )
            color = "#5cb85c"
        else:
            status_text = (
                f"⚖️ 溫和盤整 (多空分歧整理，平均 {avg_pct:+.2f}%)"
            )
            color = "#337ab7"

    return {
        "group_name": category_title,
        "status_text": status_text,
        "color": color,
    }


def calculate_volume_levels(df: pd.DataFrame) -> dict:
    res = {
        "vol_resistance": None,
        "vol_support": None,
        "latest_vol_date": "無",
        "signal": None,
    }
    if len(df) < 20:
        return res

    df_vol = df.copy()
    df_vol["Vol_MA20"] = df_vol["Volume"].rolling(20).mean()
    heavy_vol_df = df_vol[df_vol["Volume"] > df_vol["Vol_MA20"] * 2.0]

    if not heavy_vol_df.empty:
        latest_heavy = heavy_vol_df.iloc[-1]
        heavy_date = latest_heavy.name.strftime("%Y-%m-%d")

        high_p = latest_heavy["High"]
        low_p = latest_heavy["Low"]
        close_p = latest_heavy["Close"]

        res["vol_resistance"] = high_p
        res["vol_support"] = min(low_p, close_p)
        res["latest_vol_date"] = heavy_date
        res["signal"] = (
            f"• [關鍵爆量K線] 最近爆量日 ({heavy_date})：高點天花板壓在 ${high_p:.2f}，"
            f"爆量防守低點在 ${res['vol_support']:.2f}"
        )

    return res


def calculate_gap_levels(df: pd.DataFrame, max_lookback: int = 60) -> dict:
    res = {"bull_gap": None, "bear_gap": None, "signals": []}
    if len(df) < 5:
        return res

    sub_df = df.tail(max_lookback)
    gaps = []

    for i in range(1, len(sub_df)):
        prev = sub_df.iloc[i - 1]
        curr = sub_df.iloc[i]
        curr_date = curr.name.strftime("%Y-%m-%d")

        if curr["Low"] > prev["High"]:
            gaps.append(
                {
                    "type": "bull",
                    "date": curr_date,
                    "bottom": prev["High"],
                    "top": curr["Low"],
                    "idx": i,
                }
            )
        elif curr["High"] < prev["Low"]:
            gaps.append(
                {
                    "type": "bear",
                    "date": curr_date,
                    "bottom": curr["High"],
                    "top": prev["Low"],
                    "idx": i,
                }
            )

    latest_bull, latest_bear = None, None

    for gap in reversed(gaps):
        gap_idx = gap["idx"]
        subsequent_df = sub_df.iloc[gap_idx + 1 :]

        if gap["type"] == "bull":
            is_filled = (
                not subsequent_df.empty
                and (subsequent_df["Low"] <= gap["bottom"]).any()
            )
            gap["filled"] = is_filled
            if latest_bull is None:
                latest_bull = gap
        else:
            is_filled = (
                not subsequent_df.empty
                and (subsequent_df["High"] >= gap["top"]).any()
            )
            gap["filled"] = is_filled
            if latest_bear is None:
                latest_bear = gap

    if latest_bull:
        status = "已回補" if latest_bull["filled"] else "未回補（強勢支撐）"
        res["bull_gap"] = latest_bull
        res["signals"].append(
            f"• [多方缺口] 最近多方跳空 ({latest_bull['date']})：${latest_bull['bottom']:.2f} ~ ${latest_bull['top']:.2f} ({status})"
        )

    if latest_bear:
        status = "已回補" if latest_bear["filled"] else "未回補（強勢壓力）"
        res["bear_gap"] = latest_bear
        res["signals"].append(
            f"• [空方缺口] 最近空方跳空 ({latest_bear['date']})：${latest_bear['bottom']:.2f} ~ ${latest_bear['top']:.2f} ({status})"
        )

    return res


def get_large_shareholders_data(stock_id: str) -> dict:
    start_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": "TaiwanStockDepositShare",
        "data_id": stock_id,
        "start_date": start_date,
    }
    result = {
        "summary": "無集保數據",
        "score_change": 0,
        "signals": [],
        "history_df": pd.DataFrame(),
    }
    try:
        res = requests.get(url, params=params, headers=HEADERS, timeout=4)
        data = res.json()
        if data.get("msg") == "success" and data.get("data"):
            df = pd.DataFrame(data["data"])
            unique_dates = sorted(df["date"].unique())

            large_df = df[df["holding_shares_level"] == 15].sort_values("date")

            if not large_df.empty:
                history_df = large_df.tail(5)[
                    ["date", "percent", "people"]
                ].copy()
                history_df.rename(
                    columns={
                        "date": "集保日期",
                        "percent": "大戶持股比例(%)",
                        "people": "大戶人數(人)",
                    },
                    inplace=True,
                )
                result["history_df"] = history_df

            if len(unique_dates) >= 2:
                latest_friday = unique_dates[-1]
                prev_friday = unique_dates[-2]

                df_latest = df[df["date"] == latest_friday]
                df_prev = df[df["date"] == prev_friday]

                large_latest = df_latest[
                    df_latest["holding_shares_level"] == 15
                ]
                large_prev = df_prev[df_prev["holding_shares_level"] == 15]

                if not large_latest.empty and not large_prev.empty:
                    ratio_now = float(large_latest["percent"].values[0])
                    ratio_prev = float(large_prev["percent"].values[0])
                    diff_ratio = ratio_now - ratio_prev

                    people_now = int(large_latest["people"].values[0])
                    people_prev = int(large_prev["people"].values[0])
                    diff_people = people_now - people_prev

                    arrow_ratio = (
                        "⬆️"
                        if diff_ratio > 0
                        else ("⬇️" if diff_ratio < 0 else "➡️")
                    )

                    result["summary"] = (
                        f"[{latest_friday}] 集保大戶: {ratio_now:.2f}% ({arrow_ratio} {diff_ratio:+.2f}%) | "
                        f"人數: {people_now}人 ({diff_people:+}人)"
                    )

                    if diff_ratio >= 0.3:
                        result["score_change"] = 2
                        result["signals"].append(
                            f"• [集保籌碼] 最新一期({latest_friday}) 集保大戶持股增加 {diff_ratio:+.2f}% 至 {ratio_now:.2f}% (+2分)"
                        )
                    elif diff_ratio <= -0.3:
                        result["score_change"] = -2
                        result["signals"].append(
                            f"• [集保籌碼] 最新一期({latest_friday}) 集保大戶持股減少 {diff_ratio:+.2f}% 至 {ratio_now:.2f}% (-2分)"
                        )
    except Exception as e:
        print(f"集保大戶資料抓取失敗: {e}")
    return result


def get_financial_and_analyst_data(ticker: yf.Ticker) -> dict:
    info = ticker.info or {}
    fin_data = {
        "analyst_target": "無數據",
        "revenue_yoy": "無數據",
        "eps": "無數據",
        "last_year_eps": "無數據",
        "est_eps": "無數據",
        "pe_ratio": "無數據",
        "pb_ratio": "無數據",
        "gross_margin": "無數據",
        "fundamental_score": 0,
        "fundamental_signals": [],
    }

    try:
        target_mean = info.get("targetMeanPrice")
        target_high = info.get("targetHighPrice")
        target_low = info.get("targetLowPrice")
        if target_mean and target_low and target_high:
            fin_data["analyst_target"] = (
                f"${target_mean:.2f} (範圍: ${target_low:.2f} ~ ${target_high:.2f})"
            )

        pe = info.get("trailingPE")
        pb = info.get("priceToBook")
        if pe:
            fin_data["pe_ratio"] = f"{pe:.2f} 倍"
        if pb:
            fin_data["pb_ratio"] = f"{pb:.2f} 倍"

        est_eps = info.get("forwardEps")
        if est_eps:
            fin_data["est_eps"] = f"${est_eps:.2f}"

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

        eps = info.get("trailingEps")
        if eps is not None:
            fin_data["eps"] = f"${eps:.2f}"

        gross_m = info.get("grossMargins")
        if gross_m is not None:
            fin_data["gross_margin"] = f"{gross_m * 100:.2f}%"

    except Exception as e:
        print(f"基本面資料擷取異常: {e}")

    return fin_data


def get_margin_trading_data(stock_id: str, days: int = 20) -> dict:
    """透過 FinMind API 抓取融資融券真實數據"""
    result = {
        "summary": "無數據",
        "signals": [],
        "margin_df": pd.DataFrame(),
    }
    try:
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        url = "https://api.finmindtrade.com/api/v4/data"
        params = {
            "dataset": "TaiwanStockMarginPurchaseShortSale",
            "data_id": stock_id,
            "start_date": start_date,
        }
        res = requests.get(url, params=params, headers=HEADERS, timeout=5)
        data = res.json()

        if data.get("msg") == "success" and data.get("data"):
            df = pd.DataFrame(data["data"])
            if not df.empty:
                df = df.sort_values("date", ascending=False)
                latest = df.iloc[0]

                # 取出融資餘額與融券餘額 (FinMind 單位為股/張數)
                m_balance = int(latest.get("MarginPurchaseTodayBalance", 0))
                m_diff = int(latest.get("MarginPurchaseBuy", 0)) - int(latest.get("MarginPurchaseSell", 0))
                s_balance = int(latest.get("ShortSaleTodayBalance", 0))
                s_diff = int(latest.get("ShortSaleBuy", 0)) - int(latest.get("ShortSaleSell", 0))

                result["summary"] = (
                    f"[{latest['date']}] 融資餘額: {m_balance:,}張 ({m_diff:+,}張) | "
                    f"融券餘額: {s_balance:,}張 ({s_diff:+,}張)"
                )

                # 建立前幾日明細表
                df_display = df.head(10).copy()
                df_display["融資增減"] = df_display["MarginPurchaseBuy"] - df_display["MarginPurchaseSell"]
                df_display["融券增減"] = df_display["ShortSaleBuy"] - df_display["ShortSaleSell"]
                
                df_display = df_display[[
                    "date", "MarginPurchaseTodayBalance", "融資增減", 
                    "ShortSaleTodayBalance", "融券增減"
                ]]
                df_display.columns = ["日期", "融資餘額(張)", "融資增減(張)", "融券餘額(張)", "融券增減(張)"]
                result["margin_df"] = df_display
    except Exception as e:
        print(f"FinMind 融資融券抓取失敗: {e}")
    return result


def get_day_trading_data(stock_id: str, days: int = 20) -> dict:
    """透過 FinMind API 抓取當沖交易真實數據"""
    result = {
        "summary": "無數據",
        "signals": [],
        "day_trade_df": pd.DataFrame(),
    }
    try:
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        url = "https://api.finmindtrade.com/api/v4/data"
        params = {
            "dataset": "TaiwanStockDayTrading",
            "data_id": stock_id,
            "start_date": start_date,
        }
        res = requests.get(url, params=params, headers=HEADERS, timeout=5)
        data = res.json()

        if data.get("msg") == "success" and data.get("data"):
            df = pd.DataFrame(data["data"])
            if not df.empty:
                df = df.sort_values("date", ascending=False)
                latest = df.iloc[0]

                vol = int(latest.get("Volume", 0))
                ratio = float(latest.get("DayTradingRate", 0)) * 100 if latest.get("DayTradingRate") else 0.0

                result["summary"] = (
                    f"[{latest['date']}] 最新當沖量: {vol:,}張 | 當沖比率: {ratio:.1f}%"
                )

                df_display = df.head(10).copy()
                df_display["DayTradingRate"] = (df_display["DayTradingRate"] * 100).round(1).astype(str) + "%"
                df_display = df_display[["date", "Volume", "DayTradingRate"]]
                df_display.columns = ["日期", "當沖張數(張)", "當沖比率(%)"]
                result["day_trade_df"] = df_display
    except Exception as e:
        print(f"FinMind 當沖資料抓取失敗: {e}")
    return result


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

        ma10 = latest["MA10"]
        ma20 = latest["MA20"]
        ma60 = latest["MA60"] if not pd.isna(latest["MA60"]) else ma20

        bias_20 = ((close_price - ma20) / ma20) * 100

        signals = []
        tech_score = fin_data["fundamental_score"]
        signals.extend(fin_data["fundamental_signals"])

        if close_price >= ma20 and close_price >= ma60:
            ma_analysis_str = (
                f"處於多頭格局，月線(${ma20:.1f}) 與季線(${ma60:.1f}) 為下檔強支撐"
            )
            signals.append(f"• [均線支撐] {ma_analysis_str}")
        elif close_price < ma20 and close_price < ma60:
            ma_analysis_str = (
                f"處於空頭格局，上方面臨月線(${ma20:.1f}) 與季線(${ma60:.1f}) 反壓"
            )
            signals.append(f"• [均線壓力] {ma_analysis_str}")
        elif close_price >= ma20 and close_price < ma60:
            ma_analysis_str = (
                f"站上月線支撐(${ma20:.1f})，但上方面臨季線(${ma60:.1f}) 下彎壓力"
            )
            signals.append(f"• [均線交會] {ma_analysis_str}")
        else:
            ma_analysis_str = (
                f"跌破月線壓力(${ma20:.1f})，回測季線支撐(${ma60:.1f})"
            )
            signals.append(f"• [均線交會] {ma_analysis_str}")

        bias_str = f"20MA 乖離率: {bias_20:+.2f}%"
        if bias_20 >= 8.0:
            tech_score -= 2
            signals.append(
                f"• [乖離警訊] 正乖離率達 {bias_20:+.2f}% (過熱)，短線易引發獲利了結賣壓 (-2分)"
            )
        elif bias_20 <= -8.0:
            tech_score += 1
            signals.append(
                f"• [乖離訊號] 負乖離率達 {bias_20:+.2f}% (超跌)，短線具技術性反彈契機 (+1分)"
            )
        else:
            signals.append(f"• [乖離動態] 月線乖離率位處合理區間 ({bias_20:+.2f}%)")

        trend_status = "NEUTRAL"
        if close_price > ma20 and ma20 > ma60:
            trend_status = "BULL"
            tech_score += 3
        elif close_price < ma20 and ma20 < ma60:
            trend_status = "BEAR"
            tech_score -= 3

        recent_20 = df.tail(20)
        resistance = recent_20["High"].max()
        support = recent_20["Low"].min()

        vol_levels = calculate_volume_levels(df)
        if vol_levels.get("signal"):
            signals.append(vol_levels["signal"])

        gap_info = calculate_gap_levels(df)
        if gap_info.get("signals"):
            signals.extend(gap_info["signals"])

        high_6m = df["High"].max()
        low_6m = df["Low"].min()
        diff = high_6m - low_6m
        bull_target = close_price + (diff * 0.382)
        bear_target = max(0, close_price - (diff * 0.382))

        return {
            "stock_name": stock_name,
            "close_price": close_price,
            "latest_date": latest_date_str,
            "realtime_quote": realtime_quote,
            "trend_status": trend_status,
            "score": tech_score,
            "signals": signals,
            "support": support,
            "resistance": resistance,
            "ma_analysis": ma_analysis_str,
            "bias_20": bias_20,
            "bias_str": bias_str,
            "vol_levels": vol_levels,
            "gap_info": gap_info,
            "bull_target": bull_target,
            "bear_target": bear_target,
            "analyst_target": fin_data["analyst_target"],
            "revenue_yoy": fin_data["revenue_yoy"],
            "eps": fin_data["eps"],
            "last_year_eps": fin_data["last_year_eps"],
            "est_eps": fin_data["est_eps"],
            "pe_ratio": fin_data["pe_ratio"],
            "pb_ratio": fin_data["pb_ratio"],
            "gross_margin": fin_data["gross_margin"],
            "df": df,
        }

    except Exception as e:
        print(f"技術指標計算失敗: {e}")
        return {"error": "資料處理異常"}


def get_chip_data(stock_id: str, days: int = 20) -> pd.DataFrame:
    """透過 FinMind API 抓取三大法人買賣超資料"""
    try:
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        url = "https://api.finmindtrade.com/api/v4/data"
        params = {
            "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
            "data_id": stock_id,
            "start_date": start_date,
        }
        res = requests.get(url, params=params, headers=HEADERS, timeout=5)
        data = res.json()

        if data.get("msg") == "success" and data.get("data"):
            raw_df = pd.DataFrame(data["data"])
            raw_df["net_buy_vol"] = (
                (raw_df["buy"] - raw_df["sell"]) / 1000
            ).round(0)

            name_map = {
                "Foreign_Investor": "外資",
                "Foreign_Dealer_Self": "外資自營商",
                "Investment_Trust": "投信",
                "Dealer_Self": "自營商(自營)",
                "Dealer_Hedging": "自營商(避險)",
            }
            raw_df["name"] = raw_df["name"].map(lambda x: name_map.get(x, x))

            pivot_df = raw_df.pivot_table(
                index="date", columns="name", values="net_buy_vol", aggfunc="sum"
            ).fillna(0)

            for col in ["外資", "投信", "自營商(自營)", "自營商(避險)"]:
                if col not in pivot_df.columns:
                    pivot_df[col] = 0

            pivot_df["三大法人合計"] = (
                pivot_df["外資"]
                + pivot_df["投信"]
                + pivot_df["自營商(自營)"]
                + pivot_df["自營商(避險)"]
            )

            pivot_df = pivot_df.sort_index(ascending=False)
            pivot_df.index.name = "日期"

            display_cols = [
                "外資",
                "投信",
                "自營商(自營)",
                "自營商(避險)",
                "三大法人合計",
            ]
            final_df = pivot_df[[c for c in display_cols if c in pivot_df.columns]]
            return final_df.astype(int)

    except Exception as e:
        print(f"FinMind 法人籌碼抓取失敗: {e}")

    return pd.DataFrame()


def get_stock_news(stock_id: str, max_news: int = 5) -> list:
    url = f"https://tw.stock.yahoo.com/quote/{stock_id}/news"
    news_titles = []
    try:
        response = requests.get(url, headers=HEADERS, timeout=4)
        soup = BeautifulSoup(response.text, "html.parser")
        articles = soup.find_all("h3")
        for article in articles:
            title = article.get_text().strip()
            if title and len(title) > 6 and title not in news_titles:
                news_titles.append(title)
                if len(news_titles) >= max_news:
                    break
    except Exception as e:
        print(f"新聞抓取失敗: {e}")
    return news_titles


def plot_candlestick_chart(df: pd.DataFrame, stock_id: str, stock_name: str):
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.7, 0.3],
        subplot_titles=(f"{stock_id} {stock_name} K線與均線走勢", "成交量"),
    )

    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            name="K線",
            increasing_line_color="#d9534f",
            decreasing_line_color="#5cb85c",
        ),
        row=1,
        col=1,
    )

    if "MA10" in df:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["MA10"],
                mode="lines",
                name="MA10",
                line=dict(color="#f0ad4e", width=1.5),
            ),
            row=1,
            col=1,
        )
    if "MA20" in df:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["MA20"],
                mode="lines",
                name="MA20",
                line=dict(color="#0275d8", width=1.5),
            ),
            row=1,
            col=1,
        )
    if "MA60" in df:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["MA60"],
                mode="lines",
                name="MA60",
                line=dict(color="#5bc0de", width=1.5),
            ),
            row=1,
            col=1,
        )

    colors = [
        "#d9534f" if c >= o else "#5cb85c"
        for c, o in zip(df["Close"], df["Open"])
    ]
    fig.add_trace(
        go.Bar(
            x=df.index,
            y=df["Volume"] // 1000,
            name="成交量(張)",
            marker_color=colors,
        ),
        row=2,
        col=1,
    )

    fig.update_layout(
        xaxis_rangeslider_visible=False,
        height=500,
        margin=dict(l=20, r=20, t=40, b=20),
        showlegend=True,
    )
    return fig


def analyze_trend(
    chip_df: pd.DataFrame,
    margin_data: dict,
    day_trade_data: dict,
    large_holders_data: dict,
    group_status: dict,
    news_list: list,
    tech_data: dict,
) -> dict:
    score = 0
    signals = []
    trend_status = tech_data.get("trend_status", "NEUTRAL")

    if "error" not in tech_data:
        score += tech_data["score"]
        signals.extend(tech_data["signals"])
    else:
        signals.append(f"• {tech_data['error']}")

    if large_holders_data.get("score_change"):
        score += large_holders_data["score_change"]
    signals.extend(large_holders_data.get("signals", []))

    full_group = group_status.get("group_name", "台股市場類別")
    signals.append(
        f"• [市場與產業標籤] {full_group} | 動態評估: {group_status['status_text']}"
    )

    if not chip_df.empty:
        latest_chip = chip_df.iloc[0]
        foreign_val = latest_chip.get("外資", 0)
        trust_val = latest_chip.get("投信", 0)

        if foreign_val > 0:
            score += 2
            signals.append(f"• [籌碼利多] 最新一日外資買超 ({foreign_val:,} 張) (+2分)")
        elif foreign_val < 0:
            score -= 2
            signals.append(f"• [籌碼空頭] 最新一日外資賣超 ({abs(foreign_val):,} 張) (-2分)")

        if trust_val > 0:
            score += 2
            signals.append(f"• [籌碼利多] 最新一日投信買超 ({trust_val:,} 張) (+2分)")
        elif trust_val < 0:
            score -= 2
            signals.append(f"• [籌碼空頭] 最新一日投信賣超 ({abs(trust_val):,} 張) (-2分)")

    if trend_status == "BEAR":
        trend = "📉 弱勢空頭 (受制於均線反壓)"
        color = "#5cb85c"
        target_price = tech_data.get("bear_target", 0)
    elif trend_status == "BULL" and score >= 3:
        trend = "🚀 強勢多頭 (突破均線且籌碼力挺)"
        color = "#d9534f"
        target_price = tech_data.get("bull_target", 0)
    else:
        trend = "⚖️ 區間震盪 (遇均線尋求支撐/壓力)"
        color = "#337ab7"
        target_price = tech_data.get("bull_target", 0)

    return {
        "trend": trend,
        "score": score,
        "signals": signals,
        "color": color,
        "target_price": target_price,
    }


# ==========================================
# 4. Streamlit Web UI 主介面
# ==========================================
st.title("📈 股市大亨 - 完整台股診斷系統 (Web版)")
st.caption("同步證交所/櫃買中心官方產業類別，提供技術面、法人與信用籌碼綜合診斷")

with st.container():
    col_input, col_btn = st.columns([4, 1])
    with col_input:
        user_input = st.text_input(
            "請輸入股票代碼或中文名稱 (例: 2330 / 華通 / 欣興):",
            value="",
            key="stock_input",
            placeholder="請輸入台股代碼或名稱...",
        )
    with col_btn:
        st.write(" ")
        st.write(" ")
        search_clicked = st.button(
            "完整分析", type="primary", use_container_width=True
        )

if search_clicked or user_input.strip():
    with st.spinner("正在進行極速並行數據分析中..."):
        stock_id, stock_name = resolve_stock_info(user_input)

        with ThreadPoolExecutor(max_workers=7) as executor:
            future_tech = executor.submit(get_tech_data, stock_id, stock_name)
            future_chip = executor.submit(get_chip_data, stock_id, 20)
            future_margin = executor.submit(get_margin_trading_data, stock_id, 20)
            future_day_trade = executor.submit(get_day_trading_data, stock_id, 20)
            future_large_holders = executor.submit(
                get_large_shareholders_data, stock_id
            )
            future_group = executor.submit(get_group_status, stock_id)
            future_news = executor.submit(get_stock_news, stock_id)

            tech_data = future_tech.result()
            chip_df = future_chip.result()
            margin_data = future_margin.result()
            day_trade_data = future_day_trade.result()
            large_holders_data = future_large_holders.result()
            group_status = future_group.result()
            news_list = future_news.result()

        if "error" in tech_data:
            st.error(tech_data["error"])
        else:
            result = analyze_trend(
                chip_df,
                margin_data,
                day_trade_data,
                large_holders_data,
                group_status,
                news_list,
                tech_data,
            )

            st.subheader(f"🔍 診斷標的：{stock_id} {stock_name}")

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            data_date = tech_data.get("latest_date", "未知")
            st.caption(f"當前系統時間: {now_str} | K線資料日: {data_date}")

            rt = tech_data.get("realtime_quote", {})
            m1, m2, m3, m4 = st.columns(4)
            m1.metric(
                "即時股價",
                f"${rt.get('current_price', 0):.2f}",
                f"{rt.get('pct_change', 0):+.2f}%",
            )
            m2.metric("核心趨勢", result["trend"].split(" ")[1], delta=None)
            m3.metric("預估波段目標價", f"${result.get('target_price', 0):.2f}")
            m4.metric("綜合評分", f"{result['score']} 分")

            st.markdown("---")

            c1, c2 = st.columns(2)

            with c1:
                st.markdown(
                    f"**🏢 產業/族群別：** {group_status.get('group_name', '--')}"
                )
                st.markdown(
                    f"**🔥 族群態勢：** {group_status.get('status_text', '--')}"
                )
                st.markdown(
                    f"**📊 日K均線狀態：** {tech_data.get('ma_analysis', '--')}"
                )
                st.markdown(
                    f"**📈 20MA 乖離率：** {tech_data.get('bias_str', '--')}"
                )

                vol_info = tech_data.get("vol_levels", {})
                if vol_info.get("vol_resistance"):
                    v_res = vol_info["vol_resistance"]
                    v_sup = vol_info["vol_support"]
                    v_date = vol_info["latest_vol_date"]
                    st.markdown(
                        f"**💥 最近爆量日 ({v_date})：** 壓力 `${v_res:.2f}` | 支撐 `${v_sup:.2f}`"
                    )
                else:
                    st.markdown(
                        "**💥 爆量關卡：** 近 60 日無顯著爆量K線 (大於2倍均量)"
                    )

            with c2:
                st.markdown(
                    f"**🎯 法人目標價：** {tech_data.get('analyst_target', '無數據')}"
                )
                st.markdown(
                    f"**💰 財務指標：** 營收YoY: `{tech_data.get('revenue_yoy', '無數據')}` | "
                    f"近4季EPS: `{tech_data.get('eps', '無數據')}` | "
                    f"預估EPS: `{tech_data.get('est_eps', '無數據')}`"
                )
                st.markdown(
                    f"**📊 本益比/股淨比：** PE `{tech_data.get('pe_ratio', '無數據')}` | "
                    f"PB `{tech_data.get('pb_ratio', '無數據')}`"
                )
                st.markdown(
                    f"**🐋 集保大戶動態：** {large_holders_data.get('summary', '無數據')}"
                )
                st.markdown(
                    f"**💳 融資融券(最新)：** {margin_data.get('summary', '無數據')}"
                )
                st.markdown(
                    f"**⚡ 當沖動態(最新)：** {day_trade_data.get('summary', '無數據')}"
                )

            st.markdown("---")

            st.subheader("📈 個股技術走勢圖")
            fig = plot_candlestick_chart(tech_data["df"], stock_id, stock_name)
            st.plotly_chart(fig, use_container_width=True)

            st.markdown("---")

            tab1, tab2, tab3, tab4, tab5 = st.tabs(
                [
                    "📋 權重分析訊號",
                    "📊 三大法人買賣超動態 (張)",
                    "💳 融資融券與當沖明細",
                    "🐋 最近集保大戶動態",
                    "📰 最新市場新聞",
                ]
            )

            with tab1:
                st.subheader("完整指標判讀訊號")
                for sig in result["signals"]:
                    st.info(sig)

            with tab2:
                st.subheader("三大法人買賣超近 20 日明細 (單位：張)")
                if not chip_df.empty:
                    st.dataframe(chip_df, use_container_width=True)
                else:
                    st.write("尚無三大法人詳細籌碼數據。")

            with tab3:
                st.subheader("信用交易與當沖明細 (近 10 日歷史紀錄)")
                col_m, col_d = st.columns(2)
                with col_m:
                    st.markdown("##### 融資融券變動表")
                    if not margin_data["margin_df"].empty:
                        st.dataframe(
                            margin_data["margin_df"], use_container_width=True
                        )
                    else:
                        st.write("無融資融券明細。")
                with col_d:
                    st.markdown("##### 當沖交易明細表")
                    if not day_trade_data["day_trade_df"].empty:
                        st.dataframe(
                            day_trade_data["day_trade_df"],
                            use_container_width=True,
                        )
                    else:
                        st.write("無當沖明細。")

            with tab4:
                st.subheader("最近 5 週集保大戶持股集中度趨勢")
                if not large_holders_data["history_df"].empty:
                    st.dataframe(
                        large_holders_data["history_df"],
                        use_container_width=True,
                    )
                else:
                    st.write("目前尚無集保大戶歷史資料。")

            with tab5:
                st.subheader("最新市場相關新聞")
                if news_list:
                    for news in news_list:
                        st.write(f"📰 {news}")
                else:
                    st.write("暫無即時新聞資料。")
else:
    st.info("💡 請在上方輸入框輸入股票代碼或名稱（例如：2330 或 華通）並按下「完整分析」按鈕。")