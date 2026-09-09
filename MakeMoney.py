import io
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import pandas as pd
import requests
import streamlit as st
import ta  # 技術分析套件
import yfinance as yf

# 頁面配置
st.set_page_config(
    page_title="股市大亨 之 難盤見高手",
    page_icon="📈",
    layout="wide"
)

# 通用 Header
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# 修正 yfinance 快取
cache_dir = os.path.join(os.getcwd(), ".cache")
os.makedirs(cache_dir, exist_ok=True)
yf.set_tz_cache_location(cache_dir)

# 全域台股中文名稱字典
DEFAULT_STOCK_CODE_TO_NAME = {
    "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2382": "廣達",
    "2881": "富邦金", "2882": "國泰金", "2308": "台達電", "2303": "聯電",
    "3231": "緯創", "2376": "技嘉", "2603": "長榮", "2609": "陽明",
    "2615": "萬海", "2618": "長榮航", "2610": "華航", "2002": "中鋼",
    "2408": "南亞科", "2357": "華碩", "2377": "微星", "3661": "世芯-KY",
    "3443": "創意", "2345": "智邦", "3017": "奇鋐", "3324": "雙鴻", "2059": "川湖",
}

@st.cache_data(ttl=86400)
def load_taiwan_stock_official_list():
    code_to_name = DEFAULT_STOCK_CODE_TO_NAME.copy()
    name_to_code = {v: k for k, v in code_to_name.items()}
    try:
        url = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
        res = requests.get(url, headers=HEADERS, timeout=3)
        if res.status_code == 200:
            data = res.json()
            for item in data:
                code = item.get("Code", "").strip()
                name = item.get("Name", "").strip()
                if code and name:
                    code_to_name[code] = name
                    name_to_code[name] = code
    except Exception as e:
        pass
    return code_to_name, name_to_code

STOCK_CODE_TO_NAME, STOCK_NAME_TO_CODE = load_taiwan_stock_official_list()

def fetch_chinese_name_online(stock_id: str) -> str:
    try:
        url = f"https://tw.stock.yahoo.com/quote/{stock_id}"
        res = requests.get(url, headers=HEADERS, timeout=3)
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(res.text, "html.parser")
        h1 = soup.find("h1")
        if h1:
            title = h1.text.strip()
            clean_name = re.sub(r"\([^)]*\)|Yahoo|股市|行情|個股", "", title).strip()
            if clean_name and clean_name != stock_id:
                return clean_name
    except Exception:
        pass
    return "未知股票"

def resolve_stock_info(input_str: str) -> tuple[str, str]:
    query = input_str.strip().replace(" ", "")
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
                c_name = STOCK_CODE_TO_NAME.get(code) or fetch_chinese_name_online(code)
                return code, c_name
    except Exception:
        pass

    return query, fetch_chinese_name_online(query)

def get_realtime_quote(stock_id: str) -> dict:
    quote = {
        "current_price": 0.0, "change": 0.0, "pct_change": 0.0,
        "arrow": "➡️", "color": "black", "summary": "即時行情載入中...",
    }

    try:
        url = f"https://tw.stock.yahoo.com/quote/{stock_id}"
        res = requests.get(url, headers=HEADERS, timeout=3)
        if res.status_code == 200:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(res.text, "html.parser")
            price, open_price = 0.0, 0.0

            price_el = soup.find("span", class_=re.compile(r"Fz\(32px\)|Fz\(36px\)|Fz\(28px\)|C\(\$c-trend-.*?\)")) or soup.find("span", {"data-price": True})
            if price_el:
                price_str = price_el.get("data-price") or price_el.text.replace(",", "").strip()
                try: price = float(price_str)
                except ValueError: price = 0.0

            open_labels = soup.find_all("span", text=re.compile(r"^開盤|^開盤價"))
            for l in open_labels:
                parent = l.parent
                if parent:
                    val_el = parent.find_next_sibling() or parent.find("span", class_=re.compile(r"Fw\(b\)|Fz\(16px\)"))
                    if val_el:
                        try:
                            open_price = float(val_el.text.replace(",", "").strip())
                            if open_price > 0: break
                        except ValueError: pass

            if price > 0:
                change = price - open_price if open_price > 0 else 0.0
                pct_change = (change / open_price) * 100 if open_price > 0 else 0.0
                arrow, color = ("🔺", "red") if change > 0 else (("🔻", "green") if change < 0 else ("➡️", "gray"))

                quote.update({
                    "current_price": price, "change": change, "pct_change": pct_change,
                    "arrow": arrow, "color": color,
                    "summary": f"最新價: ${price:.2f} {arrow} ({pct_change:+.2f}%)",
                })
                return quote
    except Exception:
        pass

    return quote

def calculate_volume_levels(df: pd.DataFrame) -> dict:
    res = {"vol_resistance": None, "vol_support": None, "latest_vol_date": "無", "signal": None}
    if len(df) < 20: return res
    df_vol = df.copy()
    df_vol["Vol_MA20"] = df_vol["Volume"].rolling(20).mean()
    heavy_vol_df = df_vol[df_vol["Volume"] > df_vol["Vol_MA20"] * 2.0]

    if not heavy_vol_df.empty:
        latest_heavy = heavy_vol_df.iloc[-1]
        heavy_date = latest_heavy.name.strftime("%Y-%m-%d")
        high_p, low_p, close_p = latest_heavy["High"], latest_heavy["Low"], latest_heavy["Close"]

        res["vol_resistance"] = high_p
        res["vol_support"] = min(low_p, close_p)
        res["latest_vol_date"] = heavy_date
        res["signal"] = (
            f"• [關鍵爆量K線] 最近爆量日 ({heavy_date})：高點天花板壓在 ${high_p:.2f}，"
            f"爆量防守低點在 ${res['vol_support']:.2f}"
        )
    return res

@st.cache_data(ttl=1800)
def get_large_shareholders_data(stock_id: str) -> dict:
    start_date = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {"dataset": "TaiwanStockDepositShare", "data_id": stock_id, "start_date": start_date}
    result = {"summary": "無大戶數據", "score_change": 0, "signals": []}
    try:
        res = requests.get(url, params=params, headers=HEADERS, timeout=4).json()
        if res.get("msg") == "success" and res.get("data"):
            df = pd.DataFrame(res["data"])
            unique_dates = sorted(df["date"].unique())
            if len(unique_dates) >= 2:
                latest_f, prev_f = unique_dates[-1], unique_dates[-2]
                df_l, df_p = df[df["date"] == latest_f], df[df["date"] == prev_f]
                l_latest = df_l[df_l["holding_shares_level"] == 15]
                l_prev = df_p[df_p["holding_shares_level"] == 15]

                if not l_latest.empty and not l_prev.empty:
                    ratio_now, ratio_prev = float(l_latest["percent"].values[0]), float(l_prev["percent"].values[0])
                    diff_ratio = ratio_now - ratio_prev
                    people_now, people_prev = int(l_latest["people"].values[0]), int(l_prev["people"].values[0])
                    diff_people = people_now - people_prev

                    a_r = "⬆️" if diff_ratio > 0 else ("⬇️" if diff_ratio < 0 else "➡️")
                    a_p = "⬆️" if diff_people > 0 else ("⬇️" if diff_people < 0 else "➡️")
                    result["summary"] = f"[{latest_f}] 千張大戶: {ratio_now:.2f}% ({a_r} {diff_ratio:+.2f}%) | 人數: {people_now}人 ({a_p} {diff_people:+}人)"

                    if diff_ratio >= 0.3:
                        result["score_change"] = 2
                        result["signals"].append(f"• [大戶籌碼] 千張大戶持股增加 {diff_ratio:+.2f}% 至 {ratio_now:.2f}% (+2分)")
                    elif diff_ratio <= -0.3:
                        result["score_change"] = -2
                        result["signals"].append(f"• [大戶籌碼] 千張大戶持股減少 {diff_ratio:+.2f}% 至 {ratio_now:.2f}% (-2分)")
                    else:
                        result["signals"].append(f"• [大戶籌碼] 千張大戶持股變化不大 ({diff_ratio:+.2f}%)")
    except Exception: pass
    return result

def get_financial_and_analyst_data(ticker: yf.Ticker) -> dict:
    info = ticker.info
    fin_data = {
        "analyst_target": "無數據", "revenue_yoy": "無數據", "eps": "無數據",
        "last_year_eps": "無數據", "est_eps": "無數據", "pe_ratio": "無數據",
        "pb_ratio": "無數據", "gross_margin": "無數據", "fundamental_score": 0,
        "fundamental_signals": [],
    }
    try:
        t_mean, t_high, t_low = info.get("targetMeanPrice"), info.get("targetHighPrice"), info.get("targetLowPrice")
        if t_mean and t_low and t_high:
            fin_data["analyst_target"] = f"${t_mean:.2f} (範圍: ${t_low:.2f} ~ ${t_high:.2f})"
            fin_data["fundamental_signals"].append(f"• [參考資訊] 法人平均目標價: ${t_mean:.2f}")

        if info.get("trailingPE"): fin_data["pe_ratio"] = f"{info['trailingPE']:.2f} 倍"
        if info.get("priceToBook"): fin_data["pb_ratio"] = f"{info['priceToBook']:.2f} 倍"

        est_eps = info.get("forwardEps")
        if est_eps:
            fin_data["est_eps"] = f"${est_eps:.2f}"
            fin_data["fundamental_signals"].append(f"• [未來展望] 今年預估 EPS: ${est_eps:.2f}")

        rev_growth = info.get("revenueGrowth")
        if rev_growth is not None:
            rev_yoy_pct = rev_growth * 100
            fin_data["revenue_yoy"] = f"{rev_yoy_pct:+.2f}%"
            if rev_yoy_pct > 15:
                fin_data["fundamental_score"] += 3
                fin_data["fundamental_signals"].append(f"• [基本面亮眼] 營收 YoY 強勁增長 ({rev_yoy_pct:+.2f}%) (+3分)")
            elif rev_yoy_pct < -15:
                fin_data["fundamental_score"] -= 3
                fin_data["fundamental_signals"].append(f"• [基本面衰退] 營收 YoY 大幅衰退 ({rev_yoy_pct:+.2f}%) (-3分)")

        eps = info.get("trailingEps")
        if eps is not None:
            fin_data["eps"] = f"${eps:.2f}"
            if eps <= 0:
                fin_data["fundamental_score"] -= 2
                fin_data["fundamental_signals"].append(f"• 近四季累計 EPS 虧損: ${eps:.2f} (-2分)")

        if info.get("grossMargins"): fin_data["gross_margin"] = f"{info['grossMargins'] * 100:.2f}%"
    except Exception: pass
    return fin_data

@st.cache_data(ttl=1800)
def get_margin_trading_data(stock_id: str, days: int = 10) -> dict:
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y-%m-%d")
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {"dataset": "TaiwanStockMarginPurchaseShortSale", "data_id": stock_id, "start_date": start_date}
    result = {"summary": "無數據", "signals": []}
    try:
        res = requests.get(url, params=params, headers=HEADERS, timeout=4).json()
        if res.get("msg") == "success" and res.get("data"):
            df = pd.DataFrame(res["data"]).tail(5)
            if not df.empty:
                m_diff = int(df["MarginPurchaseBuy"].sum() - df["MarginPurchaseSell"].sum())
                s_diff = int(df["ShortSaleSell"].sum() - df["ShortSaleBuy"].sum())
                m_bal = int(df.iloc[-1].get("MarginPurchaseTodayBalance", 0))
                s_bal = int(df.iloc[-1].get("ShortSaleTodayBalance", 0))

                result["summary"] = f"融資: {m_bal:,}張 ({m_diff:+,}張) | 融券: {s_bal:,}張 ({s_diff:+,}張)"
                if m_diff > 1000: result["signals"].append(f"• [資券動態] 融資 5 日增加 {m_diff:+,} 張（散戶進場）")
                elif m_diff < -1000: result["signals"].append(f"• [資券動態] 融資 5 日減少 {m_diff:+,} 張（籌碼沉澱）")
    except Exception: pass
    return result

@st.cache_data(ttl=1800)
def get_day_trading_data(stock_id: str, days: int = 10) -> dict:
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y-%m-%d")
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {"dataset": "TaiwanStockDayTrading", "data_id": stock_id, "start_date": start_date}
    result = {"summary": "無數據", "signals": []}
    try:
        res = requests.get(url, params=params, headers=HEADERS, timeout=4).json()
        if res.get("msg") == "success" and res.get("data"):
            df = pd.DataFrame(res["data"]).tail(5)
            if len(df) >= 2:
                last_row = df.iloc[-1]
                vol = int(last_row.get("Volume", 0) // 1000)
                ratio = float(last_row.get("DayTradingRate", 0)) * 100
                result["summary"] = f"當沖量: {vol:,}張 | 當沖比率: {ratio:.1f}%"
                if ratio > 50: result["signals"].append(f"• [當沖動態] 當沖比率高達 {ratio:.1f}%，短線波動顯著加大！")
    except Exception: pass
    return result

@st.cache_data(ttl=1800)
def get_tech_data(stock_id: str, stock_name: str) -> dict:
    try:
        ticker = yf.Ticker(f"{stock_id}.TW")
        df = ticker.history(period="6mo")
        if df.empty:
            ticker = yf.Ticker(f"{stock_id}.TWO")
            df = ticker.history(period="6mo")

        if df.empty or len(df) < 20:
            return {"error": f"無法獲取 {stock_id} ({stock_name}) 的歷史數據"}

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        realtime_quote = get_realtime_quote(stock_id)
        fin_data = get_financial_and_analyst_data(ticker)

        df["MA20"] = df["Close"].rolling(20).mean()
        df["MA60"] = df["Close"].rolling(60).mean()

        kd = ta.momentum.StochasticOscillator(df["High"], df["Low"], df["Close"], 14, 3)
        df["K"], df["D"] = kd.stoch(), kd.stoch_signal()

        macd = ta.trend.MACD(df["Close"], 26, 12, 9)
        df["MACD_Hist"] = macd.macd_diff()

        latest, prev = df.iloc[-1], df.iloc[-2]
        close_price = realtime_quote["current_price"] or latest["Close"]
        ma20, ma60 = latest["MA20"], latest["MA60"] if not pd.isna(latest["MA60"]) else latest["MA20"]

        signals = []
        tech_score = fin_data["fundamental_score"]
        signals.extend(fin_data["fundamental_signals"])

        trend_status = "NEUTRAL"
        if close_price > ma20 and ma20 > ma60:
            trend_status = "BULL"
            tech_score += 4
            signals.append(f"• [關鍵趨勢] 多頭排列 (股價 ${close_price:.1f} > 月線 ${ma20:.1f} > 季線 ${ma60:.1f}) (+4分)")
        elif close_price < ma20 and ma20 < ma60:
            trend_status = "BEAR"
            tech_score -= 4
            signals.append(f"• [關鍵趨勢] 空頭排列 (股價 ${close_price:.1f} < 月線 ${ma20:.1f} < 季線 ${ma60:.1f}) (-4分)")

        recent_20 = df.tail(20)
        vol_levels = calculate_volume_levels(df)
        if vol_levels.get("signal"): signals.append(vol_levels["signal"])

        diff = df["High"].max() - df["Low"].min()
        return {
            "stock_name": stock_name, "close_price": close_price,
            "latest_date": df.index[-1].strftime("%Y-%m-%d"),
            "realtime_quote": realtime_quote, "trend_status": trend_status,
            "score": tech_score, "signals": signals,
            "support": recent_20["Low"].min(), "resistance": recent_20["High"].max(),
            "vol_levels": vol_levels,
            "bull_target": close_price + (diff * 0.382),
            "bear_target": max(0, close_price - (diff * 0.382)),
            "analyst_target": fin_data["analyst_target"],
            "revenue_yoy": fin_data["revenue_yoy"], "eps": fin_data["eps"],
            "est_eps": fin_data["est_eps"], "pe_ratio": fin_data["pe_ratio"],
            "pb_ratio": fin_data["pb_ratio"], "gross_margin": fin_data["gross_margin"],
            "df": df
        }
    except Exception as e:
        return {"error": f"資料處理異常: {e}"}

@st.cache_data(ttl=1800)
def get_chip_data(stock_id: str, days: int = 10) -> pd.DataFrame:
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y-%m-%d")
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {"dataset": "TaiwanStockInstitutionalInvestorsBuySell", "data_id": stock_id, "start_date": start_date}
    try:
        res = requests.get(url, params=params, headers=HEADERS, timeout=4).json()
        if res.get("msg") == "success" and res.get("data"):
            df = pd.DataFrame(res["data"])
            df["buy_sell_shares"] = (df["buy"] - df["sell"]) // 1000
            pivot_df = df.pivot_table(index="date", columns="name", values="buy_sell_shares", aggfunc="sum").fillna(0)
            return pivot_df.tail(days)
    except Exception: pass
    return pd.DataFrame()

@st.cache_data(ttl=1800)
def get_stock_news(stock_id: str, max_news: int = 5) -> list:
    url = f"https://tw.stock.yahoo.com/quote/{stock_id}/news"
    news_titles = []
    try:
        res = requests.get(url, headers=HEADERS, timeout=4)
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(res.text, "html.parser")
        for article in soup.find_all("h3"):
            title = article.get_text().strip()
            if title and len(title) > 6 and title not in news_titles:
                news_titles.append(title)
                if len(news_titles) >= max_news: break
    except Exception: pass
    return news_titles

# --- UI 介面設計 ---

st.title("📈 股市大亨 之 難盤見高手")
st.caption("跨平台行動支援版 | 支援即時分析與多指標綜合評估")

col_search, col_btn = st.columns([3, 1])
with col_search:
    user_input = st.text_input("輸入股票代碼或名稱 (例如: 2330 / 台積電)", value="2330")
with col_btn:
    st.write(" ")
    run_btn = st.button("完整分析", type="primary", use_container_width=True)

if run_btn or user_input:
    with st.spinner("正在進行極速並行數據分析..."):
        stock_id, stock_name = resolve_stock_info(user_input)

        with ThreadPoolExecutor(max_workers=6) as executor:
            f_tech = executor.submit(get_tech_data, stock_id, stock_name)
            f_chip = executor.submit(get_chip_data, stock_id)
            f_margin = executor.submit(get_margin_trading_data, stock_id)
            f_day = executor.submit(get_day_trading_data, stock_id)
            f_holders = executor.submit(get_large_shareholders_data, stock_id)
            f_news = executor.submit(get_stock_news, stock_id)

            tech_data = f_tech.result()
            chip_df = f_chip.result()
            margin_data = f_margin.result()
            day_trade_data = f_day.result()
            large_holders_data = f_holders.result()
            news_list = f_news.result()

    if "error" in tech_data:
        st.error(tech_data["error"])
    else:
        st.subheader(f"標的：{stock_id} {stock_name}")

        # 核心數據指標卡片
        rt = tech_data.get("realtime_quote", {})
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("即時股價", f"${tech_data['close_price']:.2f}", f"{rt.get('pct_change', 0):+.2f}%")
        m2.metric("近20日壓力位", f"${tech_data['resistance']:.2f}")
        m3.metric("近20日支撐位", f"${tech_data['support']:.2f}")
        m4.metric("總評分", f"{tech_data['score']} 分")

        st.markdown("---")

        # 兩欄詳細資訊
        col_left, col_right = st.columns(2)
        with col_left:
            st.write(f"**大戶動態：** {large_holders_data.get('summary')}")
            st.write(f"**資券動態：** {margin_data.get('summary')}")
            st.write(f"**當沖熱度：** {day_trade_data.get('summary')}")
            st.write(f"**法人目標價：** {tech_data.get('analyst_target')}")

        with col_right:
            st.write(f"**本益比 (PE)：** {tech_data.get('pe_ratio')} | **淨值比 (PB)：** {tech_data.get('pb_ratio')}")
            st.write(f"**毛利率：** {tech_data.get('gross_margin')} | **營收 YoY：** {tech_data.get('revenue_yoy')}")
            st.write(f"**近四季 EPS：** {tech_data.get('eps')} | **預估 EPS：** {tech_data.get('est_eps')}")

        # 技術 K 線圖
        if "df" in tech_data:
            st.subheader("📊 近半年的 K 線與均線圖")
            st.line_chart(tech_data["df"][["Close", "MA20", "MA60"]])

        # 訊號與新聞分頁
        tab1, tab2 = st.tabs(["權重分析訊號", "最新市場新聞"])
        with tab1:
            for sig in tech_data.get("signals", []):
                st.write(sig)
            if margin_data.get("signals"):
                for sig in margin_data["signals"]: st.write(sig)

        with tab2:
            if news_list:
                for idx, news in enumerate(news_list, 1):
                    st.write(f"{idx}. {news}")
            else:
                st.write("暫無新聞資料。")