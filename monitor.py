import os
import json
import time
import random
import re
import requests
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

# ================= 配置区域：人物监控矩阵 =================
OFFICIALS = [
    {"name": "胡继军", "base_keyword": "胡继军 沈阳", "tags": ["和平区", "教委", "书记", "教育", "任免", "公示", "调研", "区委", "视察"]},
    {"name": "王丽娟", "base_keyword": "王丽娟 辽宁", "tags": ["广电局", "书记", "广播", "电视", "任免", "公示", "调研", "厅长", "视察"]},
    {"name": "赵正武", "base_keyword": "赵正武 沈阳", "tags": ["水务局", "水利", "处长", "任免", "公示", "调研", "局长", "视察"]},
    {"name": "范骁锋", "base_keyword": "范骁锋 辽宁", "tags": ["水利厅", "副厅长", "厅长", "任免", "公示", "调研", "防汛", "视察"]},
    {"name": "张春达", "base_keyword": "张春达 长春", "tags": ["副市长", "市长", "政府", "任免", "公示", "调研", "书记", "视察"]},
    {"name": "杜鑫", "base_keyword": "杜鑫 辽宁", "tags": ["农科院", "农业", "书记", "院长", "任免", "公示", "调研", "视察"]},
    {"name": "王长军", "base_keyword": "王长军 抚顺", "tags": ["望花区", "人大", "主席", "任免", "公示", "调研", "书记", "视察"]},
    {"name": "纪政", "base_keyword": "纪政 沈阳", "tags": ["政协", "副主席", "主席", "任免", "公示", "调研", "视察"]},
    {"name": "陈万松", "base_keyword": "陈万松 辽宁", "tags": ["司法厅", "政治部", "主任", "任免", "公示", "调研", "厅长", "视察"]},
    {"name": "张君昶", "base_keyword": "张君昶 抚顺", "tags": ["副市长", "市长", "政府", "任免", "公示", "调研", "书记", "视察"]},
    {"name": "刘宇星", "base_keyword": "刘宇星 抚顺", "tags": ["社会工作部", "市委", "部长", "任免", "公示", "调研", "视察"]},
    {"name": "王昕", "base_keyword": "王昕 辽阳", "tags": ["政府", "副秘书长", "秘书长", "任免", "公示", "调研", "市长", "视察"]},
    {"name": "白勒", "base_keyword": "白勒 辽阳", "tags": ["交通运输局", "办公室", "主任", "任免", "公示", "调研", "局长", "视察"]},
    {"name": "申建军", "base_keyword": "申建军 国研中心", "tags": ["国务院发展研究中心", "任免", "公示", "调研", "主任", "视察"]},
    {"name": "李军", "base_keyword": "李军 总政治部", "tags": ["总政", "军队", "军委", "任免", "公示", "视察", "主任", "少将", "中将"]}
]

# 抓取渠道
SCRAPE_SOURCES = {
    "百度资讯": "https://www.baidu.com/s?tn=news&word={keyword}",
    "头条搜索": "https://so.toutiao.com/search?dvpf=pc&source=input&keyword={keyword}",
    "澎湃新闻": "https://www.thepaper.cn/searchResult?cont={keyword}"
}

HISTORY_FILE = "history.json"
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK_URL")
TZ_BJ = timezone(timedelta(hours=8))

# ================= 辅助函数 =================

def is_published_today(text, today_str):
    """判断文本中包含的日期是否是今天"""
    if not text:
        return False, "未知"
        
    match = re.search(r'(20\d{2})[-/年\.](\d{1,2})[-/月\.](\d{1,2})', text)
    if match:
        year, month, day = match.groups()
        date_str = f"{year}-{int(month):02d}-{int(day):02d}"
        if date_str == today_str:
            return True, date_str
            
    relative_time_keywords = ["分钟前", "小时前", "刚刚", "今天"]
    for kw in relative_time_keywords:
        if kw in text:
            context_match = re.search(fr'(\d+)?{kw}', text)
            show_date = context_match.group() if context_match else kw
            return True, f"今天 ({show_date})"
            
    return False, "非今日"

def clean_summary_text(raw_text, title):
    """清理提取出的摘要文字，去除多余空白和标题重复部分"""
    if not raw_text:
        return "暂无详细摘要描述，请点击下方按钮查看原文。"
    # 去除多余换行与连续空格
    clean_text = " ".join(raw_text.split())
    # 去除标题本身，只保留摘要
    clean_text = clean_text.replace(title, "").strip()
    
    # 限制最大长度，避免飞书卡片过长
    if len(clean_text) > 200:
        clean_text = clean_text[:200] + "..."
        
    return clean_text if clean_text else "暂无详细摘要描述，请点击下方按钮查看原文。"

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_history(history_list):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history_list, f, ensure_ascii=False, indent=2)

def send_feishu_card(title, person_name, source, url, publish_date, current_time, summary):
    """
    发送美化后的飞书富文本交互卡片消息（包含推送信息详情）
    """
    if not FEISHU_WEBHOOK:
        print("未配置 FEISHU_WEBHOOK_URL，跳过推送")
        return

    card_payload = {
        "msg_type": "interactive",
        "card": {
            "config": {
                "wide_screen_mode": True
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": "📰 政务信息推送 (今日最新进展)"
                },
                "template": "orange"  # 标题栏颜色: 橙色醒目提醒
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**📌 消息标题：**\n**{title}**"
                    }
                },
                {
                    "tag": "hr"  # 分隔线
                },
                {
                    "tag": "div",
                    "fields": [
                        {
                            "is_short": True,
                            "text": {
                                "tag": "lark_md",
                                "content": f"**👤 政务对象：**\n<font color='red'>{person_name}</font>"
                            }
                        },
                        {
                            "is_short": True,
                            "text": {
                                "tag": "lark_md",
                                "content": f"**🌐 信息渠道：**\n{source}"
                            }
                        },
                        {
                            "is_short": True,
                            "text": {
                                "tag": "lark_md",
                                "content": f"**📅 发布时间：**\n{publish_date}"
                            }
                        },
                        {
                            "is_short": True,
                            "text": {
                                "tag": "lark_md",
                                "content": f"**⏰ 推送时间：**\n{current_time.split(' ')[1]}"
                            }
                        }
                    ]
                },
                {
                    "tag": "hr"
                },
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**📝 内容详情：**\n>{summary}"  # 引用框展示详情/摘要
                    }
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {
                                "tag": "plain_text",
                                "content": "🔗 点击查看原文"
                            },
                            "type": "primary",
                            "url": url
                        }
                    ]
                }
            ]
        }
    }
    
    try:
        response = requests.post(FEISHU_WEBHOOK, json=card_payload, timeout=10)
        print(f"飞书推送状态: {response.text}")
    except Exception as e:
        print(f"飞书推送请求失败: {e}")

# ================= 核心逻辑 =================

def run_scraper():
    history = load_history()
    new_findings_count = 0
    
    today_str = datetime.now(TZ_BJ).strftime("%Y-%m-%d")
    current_time_str = datetime.now(TZ_BJ).strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"--- 启动飞书人物监控扫描，目标匹配日期: {today_str} ---")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()
        page.set_default_timeout(25000) 

        for official in OFFICIALS:
            person_name = official["name"]
            base_keyword = official["base_keyword"]
            
            for source_name, source_url_template in SCRAPE_SOURCES.items():
                target_url = source_url_template.format(keyword=base_keyword)
                print(f"扫描: {source_name} - {person_name}")
                
                try:
                    page.goto(target_url)
                    page.wait_for_timeout(random.randint(2000, 4000)) 
                    
                    links = page.locator("a").element_handles()
                    
                    for link in links:
                        try:
                            title = link.inner_text().strip()
                            url = link.get_attribute("href")
                            
                            if url and url.startswith("http") and len(title) > 4:
                                if person_name in title:
                                    try:
                                        # 提取上下文信息（标题+正文摘要+时间）
                                        context_text = link.evaluate(
                                            "node => { "
                                            "  let p = node.parentElement; "
                                            "  let gp = p ? p.parentElement : null; "
                                            "  let ggp = gp ? gp.parentElement : null; "
                                            "  return node.textContent + ' ' + (p ? p.textContent : '') + ' ' + (gp ? gp.textContent : '') + ' ' + (ggp ? ggp.textContent : ''); "
                                            "}"
                                        )
                                    except:
                                        context_text = title

                                    is_today, display_date = is_published_today(context_text, today_str)
                                    
                                    if is_today:
                                        context_clean = context_text.replace(" ", "").replace("\n", "")
                                        match_tag = any(tag in context_clean for tag in official["tags"])
                                        
                                        critical_keywords = ["任免", "公示", "履新", "去职", "调任", "提名"]
                                        has_critical = any(kw in context_clean for kw in critical_keywords)

                                        if match_tag or has_critical:
                                            data_hash = f"{person_name}_{source_name}_{url}"
                                            
                                            if data_hash not in history:
                                                # 清理并提炼出详细摘要内容
                                                summary_details = clean_summary_text(context_text, title)
                                                
                                                print(f"发现今日新动态: [{person_name}] - {title}")
                                                
                                                # 调用包含详情信息的飞书卡片推送
                                                send_feishu_card(
                                                    title=title, 
                                                    person_name=person_name, 
                                                    source=source_name, 
                                                    url=url, 
                                                    publish_date=display_date,
                                                    current_time=current_time_str,
                                                    summary=summary_details
                                                )
                                                
                                                history.append(data_hash)
                                                new_findings_count += 1
                        except Exception:
                            continue

                except Exception as outer_e:
                    continue
                    
        browser.close()
        
    if new_findings_count > 0:
        save_history(history)
        print(f"本次运行完成，共向飞书推送 {new_findings_count} 条含详情的今日动态。")
    else:
        print("本次运行完成，未发现今日新动态。")

if __name__ == "__main__":
    run_scraper()
