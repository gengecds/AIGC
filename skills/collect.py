#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件名：skills/collect.py
作用：自进化闭环的【采集】环节——把"网上好内容"抓成可供提炼的干净素材文本。

它只负责"把外部内容变成干净文本"，不做提炼、不落盘。提炼与落盘分别由
evolve.suggest_words_llm / evolve.ingest 承担，三者串起来就是完整闭环：

    source(url/粘贴文本) → collect.load_source_text()
        → evolve.suggest_words_llm()          # 提炼
        → 人工确认 / save=true
        → evolve.ingest()                     # 落盘
        → resolver 缓存刷新                   # 生效

采集通道（video 平台如抖音很难直接抓正文，故 extra 支持"手动粘贴文稿"）：
- fetch_url_text(url)：requests 抓取网页（跟随重定向 + 浏览器 UA），
  再用标准库 html.parser 提取正文纯文本（零额外依赖），截断到 max_chars。
- load_source_text(source)：source 以 http(s) 开头 → 抓取；否则当已粘贴文本。
"""

import html as _html
import logging
import re
from html.parser import HTMLParser

import requests

logger = logging.getLogger(__name__)

_SUPPORTED_SCHEMES = ("http://", "https://")

# 常见正文黑名单：这些标签内的文字几乎不是"内容"
_SKIP_TAGS = {
    "script", "style", "noscript", "template", "svg", "iframe",
    "textarea", "select", "option", "head", "meta",
}

# 自闭合（void）元素：只有开始标签、没有结束标签，不能包裹内容，
# 因此绝不能触发 skip 计数（否则会让 skip 永远为 1 并吞掉整个正文）。
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

# 块级标签：遇到它们在文本间加入段分隔，避免中文句子粘连
_BLOCK_TAGS = {
    "p", "div", "section", "article", "h1", "h2", "h3", "h4",
    "li", "tr", "blockquote", "header", "footer",
}


class _TextExtractor(HTMLParser):
    """极简正文提取器：跳过脚本/样式，其余文本压成可读段落。"""

    _SKIP_DEPTH_DISALLOWED = 0  # 置位后丢弃深度大于等于该值的最近跳过的嵌套

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip = 0
        self._block_break = True

    def handle_starttag(self, tag, attrs):
        if tag in _VOID_TAGS:
            if tag in ("br", "hr"):
                self._flush_para()
            return
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self._flush_para()

    def handle_startendtag(self, tag, attrs):
        # 自闭合写法 <br/>、<img/> 等
        if tag in ("br", "hr"):
            self._flush_para()

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip > 0:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self._flush_para()

    def handle_data(self, data):
        if self._skip:
            return
        text = data.strip()
        if not text:
            return
        self._chunks.append(text)

    def handle_entityref(self, name):
        if not self._skip:
            try:
                self._chunks.append(_html.unescape(f"&{name};"))
            except Exception:  # noqa: BLE001
                pass

    def _flush_para(self):
        # 块级结束：加入一个段落分隔，保证中文标题/句子不粘连
        if self._chunks and self._chunks[-1] != "\n":
            self._chunks.append("\n")

    def text(self) -> str:
        body = "".join(self._chunks)
        body = re.sub(r"[ \t\u00a0]+", " ", body)          # 合并空白/不间断空格
        body = re.sub(r" *, *", "，", body)                  # 收紧英文逗号粘连（谨慎，网络正文常无空格）
        lines = [l.strip() for l in body.splitlines()]
        lines = [l for l in lines if l and not re.fullmatch(r"[\W_]+", l)]  # 丢纯符号行
        body = "\n".join(lines)
        body = re.sub(r"\n{2,}", "\n", body)
        return body.strip()


def _extract_body(html_text: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html_text or "")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[collect] HTML 解析中断: {e}")
    return p.text()


def fetch_url_text(url: str, max_chars: int = 6000, timeout: int = 20) -> str:
    """抓取网页并提取正文纯文本，截断到 max_chars。

    :raises requests.RequestException: 网络层失败
    :raises ValueError: url 不是 http(s) 链接，或抓取结果为空
    """
    if not url.lower().startswith(_SUPPORTED_SCHEMES):
        raise ValueError("url 必须以 http:// 或 https:// 开头")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    # follow_redirects 默认 False，这里显式开启（抖音短链/公众号长链都需要 302）
    resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()

    # 若服务端返回的是非 HTML（如纯 JSON/A P下载提示），先透出大小信息
    ctype = resp.headers.get("Content-Type", "")
    if "html" not in ctype.lower():
        # 尝试当作纯文本处理
        text = resp.text.strip()
        if text:
            return text[:max_chars]
        raise ValueError(f"返回 {ctype or '未知'} 内容，无文本可提取")

    text = _extract_body(resp.text)
    if not text:
        raise ValueError("抓取成功但未提取到正文（页面可能由 JS 动态渲染）")
    return text[:max_chars]


def load_source_text(source: str, max_chars: int = 6000) -> str:
    """把 source 变成素材文本：URL → 抓取正文；其它 → 按粘贴文本处理。

    source 为空时抛 ValueError。
    """
    src = (source or "").strip()
    if not src:
        raise ValueError("素材为空：请提供网页链接或直接粘贴文稿/文章文本")
    if src.lower().startswith(_SUPPORTED_SCHEMES):
        return fetch_url_text(src, max_chars=max_chars)
    # 已是正文文本：原样返回，仅做一次空白压缩
    body = re.sub(r"[ \t\u00a0]+", " ", src)
    body = re.sub(r"\n{2,}", "\n", body)
    return body.strip()[:max_chars]
