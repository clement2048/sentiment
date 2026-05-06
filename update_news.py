import requests
import datetime
import csv
import pandas as pd
import os
from pathlib import Path

# ===================== 核心配置 =====================
MASTER_CSV_PATH = "./dataset/csv/master_news_dataset_0505.csv"  # 你的主数据集路径
SAVE_DIR = Path("./dataset/csv")

# ===================== 函数定义 =====================
def get_latest_news_time(csv_path):
    """读取已有CSV，找出最新一条新闻的时间"""
    if not os.path.exists(csv_path):
        print(f"[-] 未找到历史数据集 {csv_path}，将执行全量爬取（从 2020 年开始）。")
        return datetime.datetime(2020, 1, 1, 0, 0, 0)
    
    try:
        # 只读取必要的列来节省内存
        df = pd.read_csv(csv_path, usecols=['时间'])
        # 过滤掉缺失时间的行
        df = df.dropna(subset=['时间'])
        if df.empty:
            return datetime.datetime(2020, 1, 1, 0, 0, 0)
            
        # 将字符串转为 datetime 并找出最大值（最新时间）
        df['时间'] = pd.to_datetime(df['时间'])
        latest_time = df['时间'].max()
        print(f"[+] 发现本地最新新闻时间: {latest_time}")
        return latest_time
    except Exception as e:
        print(f"[!] 读取历史数据集出错: {e}，将默认从 2020 年开始爬取。")
        return datetime.datetime(2020, 1, 1, 0, 0, 0)

def parse_binance_timestamp(ts):
    """自动判断 Binance 返回的是秒级还是毫秒级时间戳"""
    ts = int(ts)
    if ts > 10**12:
        return datetime.datetime.fromtimestamp(ts / 1000)
    else:
        return datetime.datetime.fromtimestamp(ts)

def update_master_dataset(new_csv_path, master_csv_path):
    """合并新老数据并去重"""
    print("\n[+] 开始合并数据并更新主数据集...")
    if os.path.exists(master_csv_path):
        df_master = pd.read_csv(master_csv_path)
    else:
        df_master = pd.DataFrame(columns=['新闻id', '时间', '内容', '链接'])

    df_new = pd.read_csv(new_csv_path)
    
    if df_new.empty:
        print("[-] 本次爬取没有新数据，无需更新。")
        return

    # 合并
    df_combined = pd.concat([df_master, df_new], ignore_index=True)
    
    # 根据链接去重，保留最新抓取的
    initial_len = len(df_combined)
    df_combined = df_combined.drop_duplicates(subset=['链接'], keep='last')
    
    # 按照时间降序排列 (最新的在最上面)
    df_combined = df_combined.sort_values(by='时间', ascending=False, na_position='last')
    
    # 重置新闻ID
    df_combined = df_combined.reset_index(drop=True)
    df_combined['新闻id'] = df_combined.index + 1
    
    # 保存
    df_combined[['新闻id', '时间', '内容', '链接']].to_csv(master_csv_path, index=False, encoding='utf-8-sig')
    print(f"[+] 更新完成！主数据集现包含 {len(df_combined)} 条新闻。")
    print(f"[+] 本次实际净增加了 {len(df_combined) - (initial_len - len(df_new))} 条新闻。")

# ===================== 1. 动态获取时间范围 =====================
# 获取本地最新新闻的时间作为本次爬取的 START_TIME
START_TIME = get_latest_news_time(MASTER_CSV_PATH)
# 设定一个微小的容错缓冲（比如往前多爬 1 小时），防止极端情况下的漏抓，去重逻辑会处理重复项
START_TIME = START_TIME - datetime.timedelta(hours=1) 
END_TIME = datetime.datetime.now() # 爬到当前时刻

print(f"\n[*] 本次爬虫时间窗口: {START_TIME}  ---->  {END_TIME}\n")

# ===================== 2. 初始化爬虫 =====================
s = requests.Session()
url = "https://www.binance.com/bapi/composite/v4/friendly/pgc/feed/news/list"

s.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.binance.com/zh-CN/square/news/all",
    "Origin": "https://www.binance.com",
    "clienttype": "web",
    "lang": "zh-CN",
})

s.get("https://www.binance.com/zh-CN/square/news/all")

params = {
    "pageIndex": 1,
    "pageSize": 20,
    "strategy": 6,
    "tagId": 0,
    "featured": "false",
}

SAVE_DIR.mkdir(parents=True, exist_ok=True)
now = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
daily_csv_path = SAVE_DIR / f"binance_news_{datetime.datetime.now().strftime('%Y%m%d')}.csv"

# ===================== 3. 开始爬取 =====================
with open(daily_csv_path, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["新闻id", "时间", "内容", "链接"])

    news_index = 1
    max_page = 500
    stop_flag = False

    for page in range(1, max_page + 1):
        params["pageIndex"] = page
        r = s.get(url, params=params)

        print(f"正在请求第 {page} 页...")

        try:
            js = r.json()
        except Exception as e:
            print("JSON 解析失败：", e)
            break

        if "data" not in js or "vos" not in js["data"]:
            print("接口返回异常或无数据。")
            break

        data = js["data"]["vos"]
        if not data:
            print("没有更多数据。")
            break

        for item in data:
            raw_date = item.get("date")
            if not raw_date:
                continue
                
            news_date = parse_binance_timestamp(raw_date)

            # 太新：跳过（应对极个别未来时间戳异常数据）
            if news_date > END_TIME:
                continue

            # 太旧：停止（核心增量逻辑：触碰到比本地数据还老的新闻，直接砍断爬虫）
            if news_date < START_TIME:
                stop_flag = True
                break

            news_link = item.get("webLink") or ""
            news_title = item.get("title") or ""
            news_text = item.get("subTitle") or ""

            writer.writerow([
                news_index,
                news_date.strftime("%Y-%m-%d %H:%M:%S"),
                news_title + " " + news_text,
                news_link,
            ])
            news_index += 1

        if stop_flag:
            print(f"[*] 遇到早于 {START_TIME} 的新闻，停止爬取。")
            break

print(f"\n[+] 爬取阶段完成！共抓取 {news_index - 1} 条有效新数据。")
print(f"临时文件保存在：{daily_csv_path}")

# ===================== 4. 更新主数据集 =====================
if news_index > 1:
    update_master_dataset(daily_csv_path, MASTER_CSV_PATH)
else:
    print("\n[-] 没有抓取到新数据，主数据集未更改。")
    # 可选：删除无用的临时空文件
    os.remove(daily_csv_path)