"""LLM 输出 JSON 自愈工具

为什么需要它：
    本地 Ollama（qwen3:8b）在 json 模式下偶发输出"坏 JSON"：
    ① 输出被 num_predict 截断（缺右括号 / 引号没闭合）
    ② 前后夹带说明文字（"好的，这是结果：{...}"）
    ③ 数组/对象末尾多了个逗号（{"a":1,}）
    ④ 包在 markdown 代码围栏里（```json ... ```）

    之前 script_agent 遇到这些直接抛错，靠重试 5 次兜底仍可能失败。
    这里提供 repair_json()，用"多级自愈"策略尽量救回，而不是盲目重试。

策略（按顺序尝试，越靠前越稳）：
    1. 直接 json.loads
    2. 去掉 markdown 围栏再解析
    3. 提取第一个 { 到匹配的 } 之间的内容（剥离前后杂质）
    4. 清理"尾逗号"（,} / ,]）
    5. 若因"截断"导致括号/引号没闭合 → 用括号平衡算法补全缺失的右括号/引号
    6. 最后兜底：从尾部逐步丢弃字符，找到能解析的最大合法前缀
"""

import json
import re


def _strip_fence(raw: str) -> str:
    """去掉 markdown 代码围栏（```json ... ``` 或 ``` ... ```）"""
    s = raw.strip()
    if not s.startswith("```"):
        return s
    lines = s.split("\n")
    # 去掉第一行（可能是 ```json / ```）
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    # 去掉结尾的 ```
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _remove_trailing_commas(s: str) -> str:
    """去掉对象/数组里的尾逗号：{"a":1,} -> {"a":1}；[1,2,] -> [1,2]"""
    return re.sub(r',\s*([}\]])', r'\1', s)


def _extract_and_balance(raw: str) -> str:
    """提取第一个 { 或 [ 到与之匹配的闭合符之间的内容；结尾被截断则补全。

    支持 JSON 对象（{...}）和数组（[...]）：谁先出现就按谁解析。
    核心思路：从左到右做"括号匹配"扫描，用栈记录未闭合的开括号，同时区分"是否在字符串内"。
    - 正常走到"栈清空"的闭合符就返回完整片段
    - 走到字符串末尾还没闭合 → 说明被截断，按栈逆序补齐缺失的闭合符；若还卡在字符串内，
      先补一个引号把字符串闭合（可能丢半句内容，但 JSON 至少合法）
    """
    idx_brace = raw.find("{")
    idx_bracket = raw.find("[")
    if idx_brace == -1 and idx_bracket == -1:
        raise ValueError("找不到 JSON 起始符 { 或 [")
    # 取更靠前出现的起始符作为解析目标
    starts = [(i, ch) for i, ch in ((idx_brace, "{"), (idx_bracket, "[")) if i != -1]
    start, _ = min(starts, key=lambda x: x[0])

    s = raw[start:]
    stack = []              # 记录所有未闭合的开括号（用栈，才能正确处理嵌套 { 和 [）
    pair = {"{": "}", "[": "]"}
    in_str = False
    esc = False
    i = 0
    while i < len(s):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append(ch)
            elif ch in "}]":
                if stack:
                    stack.pop()
                if not stack:
                    # 顶层结构闭合完毕，返回完整片段
                    return s[:i + 1]
        i += 1

    # 走到末尾还没闭合 → 截断：先补引号，再按栈"逆序"补对应的闭合符
    tail = '"' if in_str else ""
    tail += "".join(pair[c] for c in reversed(stack))
    return s + tail


def _try_truncate(s: str) -> object:
    """最后兜底：从尾部逐步丢字符，找到能解析的最大合法 JSON 前缀。

    只在括号补全也失败时用。为控制耗时，每隔 3 个字符试一次，最多试 500 次。
    """
    n = len(s)
    step = 3
    max_tries = min(500, (n // step) + 1)
    for k in range(max_tries):
        cut = n - k * step
        if cut <= 0:
            break
        try:
            return json.loads(s[:cut])
        except json.JSONDecodeError:
            continue
    raise ValueError("自愈失败：无法从输出中提取合法 JSON")


def repair_json(raw: str) -> object:
    """把一个可能残缺/带杂质的 LLM 输出解析成 Python 对象（dict/list）。

    参数 raw：LLM 原始返回文本
    返回：解析后的 dict / list
    失败：抛 ValueError（交给上层 retry_async 重试）
    """
    if raw is None:
        raise ValueError("LLM 输出为空")

    text = raw.strip()
    if not text:
        raise ValueError("LLM 输出为空字符串")

    # 候选文本列表，依次尝试解析（按可靠程度排序）
    candidates = []

    # 1) 原始文本
    candidates.append(text)

    # 2) 去围栏
    no_fence = _strip_fence(text)
    if no_fence != text:
        candidates.append(no_fence)

    # 3) 提取 {} 块（并补全截断）
    try:
        balanced = _extract_and_balance(no_fence)
        if balanced not in candidates:
            candidates.append(balanced)
    except ValueError:
        pass

    # 4) 对每个候选再做"去尾逗号"变体
    for c in list(candidates):
        t = _remove_trailing_commas(c)
        if t != c and t not in candidates:
            candidates.append(t)

    # 依次尝试直接解析
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue

    # 5) 括号补全后的候选再试一次截断兜底（针对补引号仍非法的情况）
    for c in candidates:
        try:
            return _try_truncate(c)
        except ValueError:
            continue

    raise ValueError("LLM 返回内容无法解析为合法 JSON")
