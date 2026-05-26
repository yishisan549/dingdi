import akshare as ak
import pandas as pd
import time
import warnings
import smtplib
import sys
import os
from email.mime.text import MIMEText
from email.header import Header
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from email.utils import formataddr

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding='utf-8')

# ====================== K线结构 ======================
class KLine:
    def __init__(self, open_, high, low, close):
        self.open = open_
        self.high = high
        self.low = low
        self.close = close

def has_contain(k1: KLine, k2: KLine) -> bool:
    case1 = k2.high <= k1.high and k2.low >= k1.low
    case2 = k1.high <= k2.high and k1.low >= k2.low
    return case1 or case2

def is_up_trend(k1: KLine, k2: KLine) -> bool:
    if has_contain(k1, k2):
        return False
    return k2.high > k1.high

def is_down_trend(k1: KLine, k2: KLine) -> bool:
    if has_contain(k1, k2):
        return False
    return k2.high < k1.high

def merge_up(k1: KLine, k2: KLine) -> KLine:
    new_high = max(k1.high, k2.high)
    new_low = max(k1.low, k2.low)
    return KLine(k2.open, new_high, new_low, k2.close)

def merge_down(k1: KLine, k2: KLine) -> KLine:
    new_high = min(k1.high, k2.high)
    new_low = min(k1.low, k2.low)
    return KLine(k2.open, new_high, new_low, k2.close)

def merge_all_contain(klines):
    stack = []
    for k in klines:
        while stack and has_contain(stack[-1], k):
            last = stack.pop()
            if is_up_trend(last, k):
                k = merge_up(last, k)
            elif is_down_trend(last, k):
                k = merge_down(last, k)
        stack.append(k)
    return stack

def four_up_trend(a,b,c,d) -> bool:
    return is_up_trend(a,b) and is_up_trend(b,c) and is_up_trend(c,d)

def four_down_trend(a,b,c,d) -> bool:
    return is_down_trend(a,b) and is_down_trend(b,c) and is_down_trend(c,d)

# ====================== 分型判断 ======================
def check_top_fractal(a,b,c,d,e,f) -> bool:
    base = e.high > d.high and e.high > f.high
    if not base:
        return False
    is_yin = f.close < f.open
    low_cond = f.low < d.low
    half_range = (d.high + d.low) / 2
    close_cond = f.close < half_range
    pre_up = four_up_trend(a,b,c,d)
    return is_yin and low_cond and close_cond and pre_up

def check_bottom_fractal(a,b,c,d,e,f) -> bool:
    base = e.low < d.low and e.low < f.low
    if not base:
        return False
    is_yang = f.close > f.open
    high_cond = f.high > d.high
    half_range = (d.high + d.low) / 2
    close_cond = f.close > half_range
    pre_down = four_down_trend(a,b,c,d)
    return is_yang and high_cond and close_cond and pre_down

# ====================== 单只股票检测 ======================
def check_one_stock(code, name):
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date="20251201", adjust="qfq")
        if len(df) < 20:
            return code, name, False, False, None

        klines_raw = [KLine(row["开盘"], row["最高"], row["最低"], row["收盘"]) for _, row in df.iterrows()]
        klines_merge = merge_all_contain(klines_raw)

        if len(klines_merge) < 6:
            return code, name, False, False, None

        a = klines_merge[-6]
        b = klines_merge[-5]
        c = klines_merge[-4]
        d = klines_merge[-3]
        e = klines_merge[-2]
        f = klines_merge[-1]

        top = check_top_fractal(a,b,c,d,e,f)
        bottom = check_bottom_fractal(a,b,c,d,e,f)
        lines = (d,e,f)
        return code, name, top, bottom, lines
    except Exception:
        return code, name, False, False, None

from email.utils import formataddr  # 顶部也要导入

# ====================== 安全邮件发送（修复QQ邮箱550报错版） ======================
def send_email(content):
    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_AUTH_CODE")
    receiver = os.getenv("EMAIL_RECEIVER")

    smtp_server = "smtp.qq.com"
    smtp_port = 465

    subject = f"A股分型筛选结果 {time.strftime('%Y-%m-%d %H:%M')}"
    msg = MIMEText(content, "plain", "utf-8")

    # 关键修复：用 formataddr 自动遵守RFC标准，中文自动编码
    msg["From"] = formataddr(("A股自动选股机器人", sender))
    msg["To"] = formataddr(("收件人", receiver))
    msg["Subject"] = Header(subject, "utf-8")

    try:
        with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
            server.login(sender, password)
            server.sendmail(sender, [receiver], msg.as_string())
        print("✅ 邮件发送成功")
    except Exception as e:
        print(f"❌ 邮件发送失败：{e}")

# ====================== 主程序 ======================
if __name__ == "__main__":
    print("正在获取A股列表...")
    for _ in range(5):
        try:
            df_all = ak.stock_info_a_code_name()
            break
        except Exception:
            time.sleep(2)
    else:
        print("获取股票列表失败")
        exit()

    df_all.columns = ["代码", "名称"]
    df_all["代码"] = df_all["代码"].astype(str).str.zfill(6)
    df_all = df_all[~df_all["名称"].str.contains(r"ST|\*ST|退", na=False)]

    main_board = df_all[df_all["代码"].str.match(r"^60")]
    cy_board = df_all[df_all["代码"].str.match(r"^30")]
    target_stocks = pd.concat([main_board, cy_board], ignore_index=True)

    total = len(target_stocks)
    print(f"共 {total} 只股票开始筛选...")

    top_list = []
    bottom_list = []

    MAX_WORKERS = 6
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(check_one_stock, row["代码"], row["名称"]): row for _, row in target_stocks.iterrows()}

        for idx, future in enumerate(as_completed(futures)):
            code, name, top, bottom, lines = future.result()
            if top:
                top_list.append((code, name, lines))
            if bottom:
                bottom_list.append((code, name, lines))

    # ====================== 生成报告（修复版） ======================
    report = "===== A股顶底分型自动筛选 =====\n"
    utc_now = datetime.utcnow()
    bj_now = utc_now + timedelta(hours=8)
    report += f"筛选时间：{bj_now.strftime('%Y-%m-%d %H:%M')}\n\n"

    report += "===== 顶分型股票（前4根上升）=====\n"
    if top_list:
        for c, n, (k1, k2, k3) in top_list:
            report += f"\n【{c} {n}】\n"
            report += f"K1: O={k1.open:.2f}, H={k1.high:.2f}, L={k1.low:.2f}, C={k1.close:.2f}\n"
            report += f"K2: O={k2.open:.2f}, H={k2.high:.2f}, L={k2.low:.2f}, C={k2.close:.2f}\n"
            report += f"K3: O={k3.open:.2f}, H={k3.high:.2f}, L={k3.low:.2f}, C={k3.close:.2f}\n"
    else:
        report += "无符合条件股票\n"

    report += "\n===== 底分型股票（前4根下降）=====\n"
    if bottom_list:
        for c, n, (k1, k2, k3) in bottom_list:
            report += f"\n【{c} {n}】\n"
            report += f"K1: O={k1.open:.2f}, H={k1.high:.2f}, L={k1.low:.2f}, C={k1.close:.2f}\n"
            report += f"K2: O={k2.open:.2f}, H={k2.high:.2f}, L={k2.low:.2f}, C={k2.close:.2f}\n"
            report += f"K3: O={k3.open:.2f}, H={k3.high:.2f}, L={k3.low:.2f}, C={k3.close:.2f}\n"
    else:
        report += "无符合条件股票\n"

    print(report)
    send_email(report)
