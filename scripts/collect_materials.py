#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件名：collect_materials.py
作用：需求研究的"素材收集"前置工具 —— 用本地 Chrome（CDP 9222）搜索类似成品，
      把竞品标题/简介/来源落盘到 storage/materials/{关键词}/，供 research_agent 消费。

为什么用本地 Chrome + CDP：
1. 用户全局规则：所有浏览器操作必须用本机 Google Chrome（真实登录态），
   禁止 headless / 独立 Chromium；抖音等内容平台对无头浏览器有风控。
2. 参考 scripts/music_downloader.py 的成熟模式（agent-browser --cdp 9222）。

前置准备（每次会话先做一次，保证登录态最新）：
    pkill -9 -f "Google Chrome"
    rm -rf /tmp/chrome-real-profile
    cp -R ~/Library/Application Support/Google/Chrome /tmp/chrome-real-profile
    rm -f /tmp/chrome-real-profile/SingletonLock /tmp/chrome-real-profile/SingletonCookie /tmp/chrome-real-profile/SingletonSocket
    open -a "Google Chrome" --args --remote-debugging-port=9222 --remote-allow-origins="*" \
        --user-data-dir="/tmp/chrome-real-profile" --no-first-run --no-default-browser-check

用法：
    python scripts/collect_materials.py "锅贴广告"          # 抖音搜索（默认）
    python scripts/collect_materials.py "锅贴广告" --site bilibili   # 改用 B 站搜索
输出：
    storage/materials/{关键词}/summary.json   # 竞品清单（research_agent 读取）
    storage/materials/{关键词}/screenshot.png # 搜索结果截图（人工可看）
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

MATERIALS_ROOT = Path(__file__).parent.parent / "storage" / "materials"


def ab(*args: str) -> str:
    """执行 agent-browser --cdp 9222 命令，返回 stdout（连接本地真实 Chrome）"""
    r = subprocess.run(
        ["agent-browser", "--cdp", "9222", *args],
        capture_output=True, text=True, timeout=60,
    )
    return (r.stdout or r.stderr or "").strip()


def eval_js(js: str) -> str:
    """在页面里执行 JS，返回结果字符串"""
    return ab("eval", js)


def clean_dir_name(keyword: str) -> str:
    """把关键词清洗成安全目录名（保留中英文，去掉非法字符）"""
    return re.sub(r'[\\/:*?"<>|\s]+', "_", keyword).strip("_") or "default"


def collect_douyin(keyword: str) -> list:
    """抖音搜索：打开搜索页 → 滚动触发懒加载 → 抓取结果条目"""
    # 1. 打开抖音搜索页（关键词 URL 编码）
    url = f"https://www.douyin.com/search/{quote(keyword)}?type=video"
    print("打开抖音搜索页:", ab("open", url))
    time.sleep(6)

    # 2. 滚动 3 次，每次间隔 2 秒，触发懒加载拿到更多结果
    for i in range(3):
        eval_js("window.scrollTo(0, document.body.scrollHeight); 'scrolled'")
        time.sleep(2)

    # 3. 通用抓取：优先 a[title]（抖音视频标题多在 title 属性），
    #    没有则退回带 .title 类的元素 / 可见长文本行
    js = """(()=>{
      const seen = new Set();
      const out = [];
      // 候选 1：a[title] 链接（标题在 title 属性）
      document.querySelectorAll('a[title]').forEach(a => {
        const t = (a.title || a.textContent || '').trim();
        if (t.length >= 4 && !seen.has(t)) { seen.add(t); out.push({t, u: a.href || ''}); }
      });
      // 候选 2：常见标题类名
      document.querySelectorAll('[class*="title"]').forEach(el => {
        const t = (el.textContent || '').trim();
        if (t.length >= 4 && t.length <= 60 && !seen.has(t)) { seen.add(t); out.push({t, u: ''}); }
      });
      return JSON.stringify(out.slice(0, 12));
    })()"""
    try:
        raw = eval_js(js)
        items = json.loads(raw)
        # 过滤纯数字/纯符号的噪声条目
        items = [i for i in items if re.search(r"[\u4e00-\u9fffA-Za-z]", i["t"])]
        print(f"抓取到 {len(items)} 条结果")
        return items
    except Exception as e:
        print(f"抓取失败: {e}")
        return []


def collect_bilibili(keyword: str) -> list:
    """B 站搜索：打开搜索结果页 → 抓取卡片标题/UP主"""
    url = f"https://search.bilibili.com/all?keyword={quote(keyword)}"
    print("打开B站搜索页:", ab("open", url))
    time.sleep(6)

    js = """(()=>{
      const out = [];
      document.querySelectorAll('.bili-video-card__info--tit, a[title]').forEach(a => {
        const t = (a.title || a.textContent || '').trim();
        if (t.length >= 4 && t.length <= 80 && !out.includes(t)) out.push(t);
      });
      return JSON.stringify(out.slice(0, 12));
    })()"""
    try:
        raw = eval_js(js)
        return [{"t": t, "u": ""} for t in json.loads(raw)]
    except Exception as e:
        print(f"抓取失败: {e}")
        return []


def main():
    # 解析参数：第一个是关键词，--site 选择站点（默认抖音）
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print("用法: python scripts/collect_materials.py <关键词> [--site douyin|bilibili]")
        sys.exit(1)
    keyword = args[0]
    site = "bilibili" if "--site" in args and "bilibili" in args else "douyin"

    out_dir = MATERIALS_ROOT / clean_dir_name(keyword)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 收集：按站点抓取竞品条目
    items = collect_bilibili(keyword) if site == "bilibili" else collect_douyin(keyword)
    if not items:
        print("✗ 未抓到结果（可能被风控/页面结构变化），跳过截图与落盘")
        sys.exit(1)

    # 截图存档（人工可回看当时搜索到了什么）
    shot = out_dir / "screenshot.png"
    try:
        ab("shot", str(shot))
        print(f"截图已保存: {shot}")
    except Exception:
        print("截图失败（跳过，不影响落盘）")

    # 写 summary.json —— research_agent 读取此文件生成 reference_cases
    summary = {
        "query": keyword,                       # 本次搜索的需求关键词
        "site": "douyin" if site == "douyin" else "bilibili",
        "source": f"https://www.douyin.com/search/{quote(keyword)}" if site == "douyin"
                  else f"https://search.bilibili.com/all?keyword={quote(keyword)}",
        "desc": "本地 Chrome 实时搜索的类似成品清单（供 research_agent 参考）",
        "items": [{"title": i["t"], "url": i["u"]} for i in items],
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n✓ 素材已落盘: {out_dir / 'summary.json'}")
    print(f"  research_agent 下次研究 '{keyword}' 类需求时自动消费参考")


if __name__ == "__main__":
    main()
