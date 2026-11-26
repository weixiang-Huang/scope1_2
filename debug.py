import streamlit as st
import pandas as pd
import requests
import time
import re
import os
import plotly.express as px
import datetime

# 設定網頁介面
st.set_page_config(page_title="ESG 溫室氣體統計平台", layout="wide")

# ================= 1. 爬蟲核心功能區 =================

def load_twse_companies():
    """載入全台上市公司清單"""
    url = "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo"
    try:
        response = requests.get(url, timeout=5)
        result = response.json()
        target_companies = {}
        for item in result.get("data", []):
            code = str(item.get("stock_id", "")).strip()
            name = str(item.get("stock_name", "")).strip()
            market_type = str(item.get("type", "")).strip()
            industry = str(item.get("industry_category", "其他")).strip()
            if market_type == 'twse' and len(code) == 4:
                target_companies[code] = {"name": name, "industry": industry}
        return target_companies
    except:
        return {}

def extract_number(text):
    """清洗數據，提取純數字"""
    if not text or text in ["N/A", "None", ""]: 
        return 0.0
    text = str(text).strip()
    
    # 🛡️ 防禦 1：排除明確表示「沒有資料」或「還在算」的文字
    exclude_keywords = ["無", "尚未", "認證中", "待", "預計", "不適用", "未盤查", "未揭露", "盤查中", "查證中"]
    if any(k in text for k in exclude_keywords):
        return 0.0
    
     # 🛡️ 防禦 2：如果字數超過 15 個字，且包含中文，通常是在寫作文說明，不是單純填數字
    # （正常的排放量通常只會填「12345」或「12345公噸」）
    if len(text) > 15 and re.search(r"[\u4e00-\u9fa5]", text):
        return 0.0
    
    # 正常抓取數字
    text = text.replace(',', '')
    match = re.search(r"[-+]?\d*\.\d+|\d+", text)
    return float(match.group()) if match else 0.0

def fetch_esg_emissions(stock_code, year):
    """抓取單一公司 ESG 範疇一、二排放量 (支援 2021 含以前的 old 暗門 API)"""
    if year <= 2021:
        url = "https://esggenplus.twse.com.tw/api/api/mopsEsg/singleCompanyData/old"
    else:
        url = "https://esggenplus.twse.com.tw/api/api/mopsEsg/singleCompanyData"
        
    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    payload = {"companyCode": stock_code, "yearList": [year], "year": year}
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code != 200: 
            return None
        result = response.json()
        
        if isinstance(result, list):
            data_source = result
        elif isinstance(result, dict):
            data_source = result.get("data", [])
            if isinstance(data_source, dict):
                data_source = [data_source]
        else:
            data_source = []
            
        scope1, scope2, scope3 = 0.0, 0.0, 0.0
        found = False
        
        for data_obj in data_source:
            if not isinstance(data_obj, dict): 
                continue
            for model in data_obj.get("treeModels", []):
                for item in model.get('items', []):
                    for sec in item.get('sections', []):
                        sec_name = str(sec.get('showName', '')).strip()
                        
                        # 🌟 關鍵修正：擴大雷達網，涵蓋新舊法規的所有可能稱呼
                        is_s1 = any(k in sec_name for k in ["範疇一", "範疇1", "直接溫室氣體", "類別1", "類別一"])
                        is_s2 = any(k in sec_name for k in ["範疇二", "範疇2", "能源間接", "間接溫室氣體", "類別2", "類別二"])
                        is_s3 = any(k in sec_name for k in ["範疇三", "範疇3", "其他間接", "類別3", "類別三", "類別4", "類別5", "類別6"])

                        
                        if is_s1 or is_s2 or is_s3:
                            val_str = "N/A"
                            for ctrl in sec.get('controls', []):
                                title_name = str(ctrl.get('title') or ctrl.get('code') or ctrl.get('name') or '').strip()
                                value_content = str(ctrl.get('value', 'N/A')).strip()
                                
                                if not value_content or value_content in ['None', 'N/A']:
                                    continue
                                    
                                if any(k in title_name for k in ['邊界', '機構', '標準', '範圍', '意見', 'dataBoundary', 'assurance']):
                                    continue
                                else:
                                    if val_str == "N/A":
                                        val_str = value_content
                                        
                            if val_str != "N/A":
                                num = extract_number(val_str)
                                # 🌟 關鍵修正：只抓取大於 0 的數字，且避免被後面的空白欄位覆蓋成 0
                                if num > 0:
                                    found = True
                                    if is_s1 and scope1 == 0: 
                                        scope1 = num
                                    if is_s2 and scope2 == 0: 
                                        scope2 = num
                                    if is_s3 and scope3 == 0:
                                        scope3 = num  
                                
        if not found: 
            return None
            
        return {"範疇一(噸)": scope1, "範疇二(噸)": scope2, "範疇三(噸)": scope3}
    except Exception as e:
        return None
    
# ================= 2. 介面與互動區 =================

st.sidebar.title("⚙️ 資料管理")
st.sidebar.write("如果尚未建立資料，或需要更新最新數據，請點擊下方按鈕。")

# 1. 預先檢查資料庫裡面已經有哪些年份的資料
existing_years = []
if os.path.exists("esg_database.csv"):
    try:
        # 只讀取「年度」欄位來加速檢查
        temp_df = pd.read_csv("esg_database.csv", usecols=['年度'])
        existing_years = temp_df['年度'].unique().tolist()
    except:
        pass

# 2. 選擇年份與測試模式
# --- 自動計算年度邏輯 ---
# 取得真實世界的現在年份 (例如 2026)
current_real_year = datetime.datetime.now().year

dynamic_years = list(range(current_real_year-1, 2020, -1))

target_year = st.sidebar.selectbox("選擇要抓取的年度", dynamic_years)
is_test_mode = st.sidebar.checkbox("✅ 測試模式 (加入台積電等 10 家)", value=True)

# 3. 🛡️ 防呆機制：動態改變警告與按鈕樣式
already_exists = target_year in existing_years

if already_exists:
    st.sidebar.warning(f"⚠️ 發現資料庫中【已經有 {target_year} 年度】的資料！\n除非您想更新最新申報進度，否則不需重新抓取浪費時間。")
    btn_text = f"⚠️ 強制重新抓取 {target_year} 年資料"
    btn_type = "secondary" # 灰色按鈕，降低誤觸機率
else:
    st.sidebar.info(f"🆕 資料庫尚無 {target_year} 年度資料，準備就緒。")
    btn_text = f"🚀 開始抓取 {target_year} 年資料"
    btn_type = "primary"   # 藍色或紅色主要按鈕

# 4. 執行按鈕
if st.sidebar.button(btn_text, type=btn_type):
    with st.spinner(f"正在載入公司清單...準備抓取 {target_year} 年資料"):
        companies = load_twse_companies()
        company_codes = list(companies.keys())
        
    if not companies:
        st.sidebar.error("載入公司清單失敗，請檢查網路連線。")
    else:
        if is_test_mode:
            test_list = ["2330", "1101", "2317", "2454", "2881", "2002", "1301", "2882", "1216", "2308"]
            company_codes = [c for c in test_list if c in companies]
            
        total = len(company_codes)
        progress_bar = st.sidebar.progress(0)
        status_text = st.sidebar.empty()
        
        all_data = []
        for i, code in enumerate(company_codes):
            comp_name = companies[code]["name"]
            status_text.text(f"抓取中 ({i+1}/{total}): {code} {comp_name}")
            
            emissions = fetch_esg_emissions(code, target_year)
            if emissions:
                all_data.append({
                    "年度": target_year,
                    "股票代號": code,
                    "公司名稱": comp_name,
                    "產業類別": companies[code]["industry"],
                    "範疇一(噸)": emissions["範疇一(噸)"],
                    "範疇二(噸)": emissions["範疇二(噸)"],
                    "範疇三(噸)": emissions["範疇三(噸)"],
                    "總排放量": emissions["範疇一(噸)"] + emissions["範疇二(噸)"]+ emissions["範疇三(噸)"]
                })
            
            progress_bar.progress((i + 1) / total)
            time.sleep(0.5) 
            
        # --- 存檔與智慧合併邏輯 ---
        if all_data:
            new_df = pd.DataFrame(all_data)
            
            if os.path.exists("esg_database.csv"):
                old_df = pd.read_csv("esg_database.csv")
                # 強制轉成文字，避免重複覆蓋失敗
                old_df['股票代號'] = old_df['股票代號'].astype(str)
                new_df['股票代號'] = new_df['股票代號'].astype(str)
                old_df['年度'] = old_df['年度'].astype(int)
                new_df['年度'] = new_df['年度'].astype(int)
                
                combined_df = pd.concat([old_df, new_df], ignore_index=True)
                combined_df = combined_df.drop_duplicates(subset=['年度', '股票代號'], keep='last')
            else:
                combined_df = new_df
                
            combined_df.to_csv("esg_database.csv", index=False, encoding="utf-8-sig")
            st.sidebar.success(f"更新完成！資料庫目前共有 {len(combined_df)} 筆跨年度資料。")
            time.sleep(1)
            st.rerun() 
        else:
            st.sidebar.warning(f"這 {total} 家公司在 {target_year} 年度沒有找到排放量數字。")

st.sidebar.markdown("---")

# ================= 3. 資料分析展示區 (前台) =================
st.title("🌱 台灣上市櫃公司 溫室氣體排放量 統計系統")

if not os.path.exists("esg_database.csv"):
    st.info("👈 目前尚無資料庫。請先到左側邊欄點擊「開始抓取 / 更新資料庫」。")
    st.stop()

# 建立證交所標準產業代碼字典
TWSE_INDUSTRY_DICT = {
    "水泥工業": "01", "食品工業": "02", "塑膠工業": "03", "紡織纖維": "04",
    "電機機械": "05", "電器電纜": "06", "玻璃陶瓷": "08", "造紙工業": "09",
    "鋼鐵工業": "10", "橡膠工業": "11", "汽車工業": "12", "建材營造": "14",
    "航運業": "15", "觀光餐旅": "16", "金融保險業": "17", "貿易百貨": "18",
    "綜合": "19", "其他業": "20", "化學工業": "21", "生技醫療業": "22",
    "油電燃氣業": "23", "半導體業": "24", "電腦及週邊設備業": "25",
    "光電業": "26", "通信網路業": "27", "電子零組件業": "28", "電子通路業": "29",
    "資訊服務業": "30", "其他電子業": "31", "農業科技業": "32", "電子商務業": "33",
    "運動休閒": "34", "居家生活": "35", "數位雲端": "36", "綠能環保": "37"
}

# ✅ 拿掉快取裝飾器，每次網頁重新整理都會強制去硬碟讀取最新 CSV
def load_csv_data():
    df = pd.read_csv("esg_database.csv")
    df['股票代號'] = df['股票代號'].astype(str)
    df['年度'] = df['年度'].astype(int) # 強制年度為整數
    
    def format_industry(ind):
        code = TWSE_INDUSTRY_DICT.get(ind, "99")
        return f"[{code}] {ind}"
        
    df['標準產業分類'] = df['產業類別'].apply(format_industry)
    return df

df = load_csv_data()

# --- 🔍 升級版的篩選條件 ---
# --- 側邊欄：前台觀看篩選器 (只顯示資料庫真正擁有的年份) ---
st.sidebar.title("🔍 統計篩選條件")

# 1. 強制轉為整數並取出不重複年份、由大到小排序
available_years = sorted(df['年度'].astype(int).unique().tolist(), reverse=True)

if available_years:
    selected_view_year = st.sidebar.selectbox("📅 選擇要觀看的年度", available_years)
else:
    st.sidebar.warning("資料庫中無有效年份資料")
    st.stop()

# 2. 產業多重選單 
#all_standard_industries = sorted([f"[{code}] {name}" for name, code in TWSE_INDUSTRY_DICT.items()])
all_standard_industries = sorted(df['標準產業分類'].unique().tolist())
selected_industries = st.sidebar.multiselect(
    "🏭 選擇產業 (支援代碼搜尋，可多選)", 
    options=all_standard_industries,
    default=[],
    help="輸入代碼 (例如: 23, 31) 或名稱 (如: 燃氣, 電子) 可快速搜尋。"
)

# 3. 排放量最低門檻
#min_emission = st.sidebar.number_input("總排放量大於 (噸)", min_value=0, value=0)

# 加入動態 Top N 滑桿 (預設 50，範圍 5 到 100，每次跳 5)
top_n = st.sidebar.slider("🏆 顯示排放量前幾名 (Top N)", min_value=5, max_value=100, value=50, step=5)
# --- 執行資料篩選 ---
filtered_df = df.copy()
filtered_df = filtered_df[filtered_df['年度'] == selected_view_year]

if selected_industries:
    filtered_df = filtered_df[filtered_df['標準產業分類'].isin(selected_industries)]

#filtered_df = filtered_df[filtered_df['總排放量'] >= min_emission]

# --- 執行資料篩選 ---
filtered_df = df.copy()
filtered_df = filtered_df[filtered_df['年度'] == selected_view_year]

if selected_industries:
    filtered_df = filtered_df[filtered_df['標準產業分類'].isin(selected_industries)]

#filtered_df = filtered_df[filtered_df['總排放量'] >= min_emission]

# ================= 升級版：ESG 漂綠偵測與佔比運算引擎 =================
import numpy as np # 需要用到 NaN 來標示未申報

# 1. 預先算出範疇一+二的總和欄位 (方便後續排名)
filtered_df['範疇一二(噸)'] = filtered_df['範疇一(噸)'] + filtered_df['範疇二(噸)']

# 2. 找出「有申報範疇三」的公司清單 (作為計算分母的基準)
has_scope3 = filtered_df['範疇三(噸)'] > 0

# 3. 只針對「有申報範疇三」的群體，計算總和分母
total_s12_valid = filtered_df.loc[has_scope3, '範疇一二(噸)'].sum()
total_s3_valid = filtered_df.loc[has_scope3, '範疇三(噸)'].sum()

# 4. 初始化佔比欄位為空值 (NaN)，這樣表格會顯示為空，不會是一堆 0.0
filtered_df['範疇一二佔比(%)'] = np.nan
filtered_df['範疇三佔比(%)'] = np.nan
filtered_df['佔比落差'] = np.nan
filtered_df['Decision'] = np.nan

# 5. 只對有申報的公司寫入佔比數據
if total_s12_valid > 0:
    filtered_df.loc[has_scope3, '範疇一二佔比(%)'] = (filtered_df.loc[has_scope3, '範疇一二(噸)'] / total_s12_valid) * 100
if total_s3_valid > 0:
    filtered_df.loc[has_scope3, '範疇三佔比(%)'] = (filtered_df.loc[has_scope3, '範疇三(噸)'] / total_s3_valid) * 100

# 6. 計算落差：一二佔比很高，但三佔比很低，落差就會是正的 (紅色警戒)
filtered_df.loc[has_scope3, '佔比落差'] = filtered_df.loc[has_scope3, '範疇一二佔比(%)'] - filtered_df.loc[has_scope3, '範疇三佔比(%)']

# 🌟 7. 新增：計算同群體內的「排名」與「排名落差」 (完全針對你的需求)
# 只針對有交作業的公司進行排名 (1代表排碳最多)
filtered_df.loc[has_scope3, '一二排碳名次'] = filtered_df.loc[has_scope3, '範疇一二(噸)'].rank(ascending=False, method='min')
filtered_df.loc[has_scope3, '範疇三名次'] = filtered_df.loc[has_scope3, '範疇三(噸)'].rank(ascending=False, method='min')

# 算出退步幾名：例如一二是第1名，三是第10名。 10 - 1 = 9 (退步9名，正數代表有漏報嫌疑)
filtered_df.loc[has_scope3, '名次落差(正值為嫌疑)'] = filtered_df.loc[has_scope3, '一二排碳名次'] - filtered_df.loc[has_scope3, '範疇三名次']
# 8.decision5%
#filtered_df.loc[has_scope3, 'Decision'] = 1-(abs((filtered_df.loc[has_scope3, '範疇一二(噸)']-filtered_df.loc[has_scope3, '範疇三(噸)'])/(filtered_df.loc[has_scope3, '範疇一二(噸)']+filtered_df.loc[has_scope3, '範疇三(噸)'])))

# 9.樣本差
filtered_df.loc[has_scope3, '樣本數的5%'] =len(filtered_df) * 0.05
# =================================================================

# --- 主畫面呈現 ---
st.subheader(f"📊 {selected_view_year} 年度統計數據")
col1, col2, col3, col4 = st.columns(4)
col1.metric("符合條件公司數", f"{len(filtered_df):,} 家")
col2.metric("範疇一 總和", f"{filtered_df['範疇一(噸)'].sum():,.0f}", "tCO2e")
col3.metric("範疇二 總和", f"{filtered_df['範疇二(噸)'].sum():,.0f}", "tCO2e")
col4.metric("範疇三 總和", f"{filtered_df['範疇三(噸)'].sum():,.0f}", "tCO2e")

st.markdown("---")

if not filtered_df.empty:
    st.subheader(f"🏆 碳排放量 Top {top_n} 專業分析圖表")
    
    # 建立三個分頁
    tab1, tab2, tab3 = st.tabs(["🏭 廠區排放排行 (範疇一+二)", "🔗 供應鏈排行 (範疇三)", "🕵️‍♂️ 漂綠異常稽核"])
    
    # 視角一：範疇一+二 排名 (包含所有公司)
    with tab1:
        top_s12_df = filtered_df.sort_values(by='範疇一二(噸)', ascending=False).head(top_n)
        fig1 = px.bar(
            top_s12_df, x='公司名稱', y=['範疇一(噸)', '範疇二(噸)'], 
            title="企業自身營運排碳量排名 (堆疊圖)", barmode='stack', text_auto='.2s'
        )
        fig1.update_layout(height=500, xaxis_tickangle=-45, yaxis_title="排放量 (tCO2e)")
        st.plotly_chart(fig1, use_container_width=True)

    # 視角二：範疇三 排名 (只抓有申報的公司)
    with tab2:
        top_s3_df = filtered_df[filtered_df['範疇三(噸)'] > 0].sort_values(by='範疇三(噸)', ascending=False).head(top_n)
        if not top_s3_df.empty:
            fig2 = px.bar(
                top_s3_df, x='公司名稱', y='範疇三(噸)', 
                title="供應鏈排碳量排名", text_auto='.2s', color_discrete_sequence=['#00CC96']
            )
            fig2.update_layout(height=500, xaxis_tickangle=-45, yaxis_title="排放量 (tCO2e)")
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("此條件下無任何公司申報範疇三數據。")

# 視角三：稽核落差 (用雷達圖與排名找出嫌疑犯)
    with tab3:
        valid_audit_df = filtered_df[filtered_df['範疇三(噸)'] > 0].copy()
        
        if len(valid_audit_df) >= 2:
            st.markdown("#### 🎯 漂綠嫌疑雷達圖 (對數座標 Log Scale)")
            st.caption("說明：越靠近 **右下角** 的公司，代表其「自身排碳極高，但供應鏈排碳極低」，漏報嫌疑較高。")
            
            # 🌟 改良：使用「絕對噸數」並開啟「對數座標 (log_x, log_y)」
            fig3 = px.scatter(
                valid_audit_df, 
                x='範疇一二(噸)',  # 換回絕對噸數
                y='範疇三(噸)',    # 換回絕對噸數
                text='公司名稱',
                color='佔比落差',  # 顏色依然用落差來標示嚴重度
                color_continuous_scale='RdYlGn_r',
                hover_data=['一二排碳名次', '範疇三名次'],
                log_x=True,       # 啟動 X 軸對數座標
                log_y=True        # 啟動 Y 軸對數座標
            )
            
            # 畫一條 45 度基準線 (代表範疇一二 = 範疇三)
            # 因為是對數，所以最小值不能是 0，取 > 0 的最小值
            min_val = max(1, min(valid_audit_df['範疇一二(噸)'].min(), valid_audit_df['範疇三(噸)'].min()))
            max_val = max(valid_audit_df['範疇一二(噸)'].max(), valid_audit_df['範疇三(噸)'].max())
            
            fig3.add_shape(type='line', x0=min_val, y0=min_val, x1=max_val, y1=max_val, line=dict(color='gray', dash='dash'))
            
            fig3.update_traces(textposition='top center')
            fig3.update_layout(height=600)
            st.plotly_chart(fig3, use_container_width=True)
            
            st.markdown("#### 🚨 排名落差警示表 (只列出名次退步的公司)")
            suspect_df = valid_audit_df[valid_audit_df['名次落差(正值為嫌疑)'] > 0].sort_values(by='名次落差(正值為嫌疑)', ascending=False)
            
            if not suspect_df.empty:
                st.dataframe(
                    suspect_df[['公司名稱', '一二排碳名次', '範疇三名次', '名次落差(正值為嫌疑)', '樣本數的5%']].style.format({
                        '一二排碳名次': '第 {:.0f} 名',
                        '範疇三名次': '第 {:.0f} 名',
                        '名次落差(正值為嫌疑)': '⬇️ 退步 {:.0f} 名',
                        '佔比落差': '{:.2f}%'
                    }).background_gradient(subset=['名次落差(正值為嫌疑)'], cmap='Reds', vmin=0),
                    use_container_width=True, hide_index=True
                )
            else:
                st.success("🎉 目前群體中，沒有發現明顯名次退步的嫌疑公司。")
        else:
            st.info("需要有兩家以上的公司申報範疇三，才能進行雷達分析。")
else:
    st.warning("⚠️ 目前篩選條件下無符合的公司資料。")

# ================= 下方總表也加入排名 =================
st.subheader("📝 完整詳細資料表")

# 把你需要的欄位通通放進來
display_df = filtered_df[[
    '年度', '股票代號', '公司名稱', '標準產業分類', 
    '範疇一(噸)', '範疇二(噸)', '範疇三(噸)', 
    '範疇一二佔比(%)', '範疇三佔比(%)', '佔比落差', # 🌟 百分比加回來了
    '一二排碳名次', '範疇三名次', '名次落差(正值為嫌疑)'
]]

# 設定每一個欄位的顯示格式
st.dataframe(
    display_df.style.format({
        '範疇一(噸)': '{:,.0f}',
        '範疇二(噸)': '{:,.0f}',
        '範疇三(噸)': '{:,.0f}',
        '範疇一二佔比(%)': '{:.2f}%', # 🌟 百分比格式
        '範疇三佔比(%)': '{:.2f}%',   # 🌟 百分比格式
        '佔比落差': '{:.2f}%',
        '一二排碳名次': '{:.0f}',
        '範疇三名次': '{:.0f}',
        '名次落差(正值為嫌疑)': '{:.0f}'
    }, na_rep="未申報")

# 🌟 改成針對「名次落差」上色，且用 Reds (白色到深紅色)，退步越多的越紅！
    .background_gradient(subset=['名次落差(正值為嫌疑)'], cmap='Reds', vmin=0),    use_container_width=True,
    hide_index=True
)