"""
BenchmarkEngine: 深度融合 viral-video-benchmark 规范的爆款视频逆向拆解引擎。
基于音频转写稿进行开篇黄金钩子、逐句职能拆解、起承转合结构推进与可复用机制诊断。

来源：情报站 `launcher/engine/benchmark_engine.py`（纯标准库，零外部依赖）。
本文件为 AIGC intel 层的复制/封装，遵守「零破坏」接入原则，不引入跨项目硬依赖。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class BenchmarkEngine:
    """爆款视频 Benchmark 拆解引擎"""

    HOOK_PATTERNS = [
        {"pattern": r"(你(目前|是否|是不是|有没有|还在)|大家(有没有|都在)|很多人(不知道|以为))", "type": "痛点反常识", "desc": "以第二人称直接唤醒目标受众现实卡点，迅速收拢注意力。"},
        {"pattern": r"(如何|怎么|怎样|带你|手把手|教你|一分钟|分钟搞定|全流程)", "type": "强承诺教程", "desc": "结果与交付物先行，明确给出观众看完能获得的确定性回报。"},
        {"pattern": r"(别再|不要再|千万别|真正意义上|颠覆|颠覆了|取代|死掉)", "type": "认知反转断言", "desc": "通过高冲突性行业断言激发好奇与认知失调，引发停留。"},
        {"pattern": r"(开源|免费|工具|插件|网站|神器|代码|一键)", "type": "高价值资源清单", "desc": "抛出稀缺或高效实用工具，直接激发收藏与转发冲动。"},
        {"pattern": r"(我做|我在|一年|复盘|实测|深度体验)", "type": "第一人称实战复盘", "desc": "以真实战绩与第一视角建立信任锚点，增加内容说服力。"},
    ]

    SENTENCE_ROLES = [
        ("hook", ["你", "有没有", "为什么", "怎么", "如何", "今天", "到底", "最近", "颠覆", "其实"], "开篇建立痛点与高价值承诺", "真人出镜＋动效 (大字断言)", "★ 极高复用：短视频前3秒黄金开场，建议保留框架换用你自己的业务主题"),
        ("problem", ["痛点", "问题", "很多", "难", "困扰", "麻烦", "慢", "效率低", "成本", "卡住"], "指出行业/受众常见卡点与误区", "真人出镜＋动效 (左右对照/痛点卡)", "★ 结构可复用：精准戳中用户焦虑，建议替换为同类目标人群的真实场景"),
        ("concept", ["其实", "本质", "核心", "定义", "逻辑", "原理", "所谓", "真正", "区别"], "拆解底层逻辑与认知升级", "真人出镜＋动效 (概念标签墙/教学图解)", "▲ 观点可复用：提炼底层认知，可用你的降维比喻重新阐释"),
        ("solution", ["方法", "步骤", "第一", "第二", "第三", "首先", "其次", "接着", "方案", "技巧"], "给出系统化解决方案与操作路径", "真实录屏 (操作步骤演示)", "★ 极高复用：清晰的递进清单化交付，观众极易收藏"),
        ("proof", ["演示", "看这里", "打开", "输入", "点击", "生成", "效果", "测试", "实操", "跑一下"], "展示真实操作与确定性结果证明", "真实录屏 (输入→动作→结果链)", "★ 极高复用：真实可复现的屏幕演示，杜绝假界面"),
        ("bonus", ["注意", "避坑", "秘诀", "彩蛋", "关键点", "窍门", "隐藏功能", "顺便"], "抛出隐藏技巧或高阶避坑点", "全屏AI视频 (空间隐喻/氛围换气)", "▲ 节奏重置：在视频中后段提供额外信息奖励，提升完播率"),
        ("cta", ["总结", "收藏", "关注", "下期", "评论区", "领取", "源码", "试试", "建议", "一起来"], "升维收束总结与行动转化指引", "真人出镜＋动效 (认知收束/行动卡)", "★ 极高复用：双层转化收拢（收藏+评论区互动）"),
    ]

    @classmethod
    def analyze(cls, video_meta: dict[str, Any], transcript_text: str) -> dict[str, Any]:
        title = str(video_meta.get("video_title") or video_meta.get("title") or "未命名作品").strip()
        creator = str(video_meta.get("creator_name") or video_meta.get("creator") or "未知创作者").strip()
        platform = str(video_meta.get("platform") or "短视频").strip()
        duration_sec = int(video_meta.get("duration_seconds") or 0)

        # 1. 拆分清洗后的有效句子
        raw_lines = [
            l.strip() for l in transcript_text.splitlines()
            if l.strip() and not l.strip().startswith("#")
        ]

        if not raw_lines:
            return cls._empty_result(title, creator, platform)

        # 2. 黄金钩子剖析 (Hook Analysis)
        first_line = raw_lines[0] if raw_lines else title
        hook_info = cls._analyze_hook(title, first_line)

        # 3. 逐句时间轴与职能拆解 (Line-by-Line Breakdown)
        line_by_line = cls._generate_line_by_line(raw_lines, duration_sec)

        # 4. 起承转合与结构推进 (Structure & Cadence)
        progression = cls._analyze_progression(raw_lines, duration_sec)

        # 5. 可复用机制与避坑清单 (Do's & Don'ts)
        dos_and_donts = cls._generate_dos_and_donts(title, hook_info, raw_lines)

        # 6. 下游二创交接包 (Creation Handoff Contract)
        handoff = {
            "sourceTitle": title,
            "creator": creator,
            "platform": platform,
            "recommendedMode": "SHORT" if (duration_sec and duration_sec < 90) or len("".join(raw_lines)) < 500 else "LONG",
            "recommendedTemplate": "教程型收藏闭环" if any(w in title for w in ["如何", "怎么", "插件", "学习", "教程", "方法"]) else "判断先于界面",
            "coreContradiction": hook_info["promise"],
            "keyTakeaways": [line["sentence"] for line in line_by_line if "可复用" in line["reusable"]][:3],
        }

        return {
            "version": "viral-video-benchmark/v1",
            "title": title,
            "creator": creator,
            "platform": platform,
            "durationSeconds": duration_sec,
            "totalWords": len("".join(raw_lines)),
            "hook": hook_info,
            "lineByLine": line_by_line,
            "structureProgression": progression,
            "dosAndDonts": dos_and_donts,
            "handoff": handoff,
        }

    @classmethod
    def _analyze_hook(cls, title: str, first_line: str) -> dict[str, Any]:
        matched_type = "悬念吸引型"
        matched_desc = "开篇通过抛出核心思考引发观众好奇心与停留意愿。"

        for item in cls.HOOK_PATTERNS:
            if re.search(item["pattern"], first_line) or re.search(item["pattern"], title):
                matched_type = item["type"]
                matched_desc = item["desc"]
                break

        # 提取核心承诺与视觉配合建议
        promise = f"通过《{title}》向观众拆解高效方法与实战路径"
        if len(first_line) > 6:
            promise = first_line.rstrip("，。？！,.?!")

        visible_evidence = "真人近景半身出镜 + 核心大字断言贴片 (前1.5秒完成视觉微变化)"
        if any(w in title.lower() for w in ["插件", "演示", "代码", "网站", "ppt", "html", "tool"]):
            visible_evidence = "真实产品/插件界面高清录屏 + 最终成品效果特写 (0.0s 结果先亮)"

        return {
            "sentence": first_line,
            "type": matched_type,
            "description": matched_desc,
            "promise": promise,
            "visibleEvidence": visible_evidence,
            "punchScore": 92 if len(first_line) <= 35 else 85,
        }

    @classmethod
    def _generate_line_by_line(cls, raw_lines: list[str], duration_sec: int) -> list[dict[str, Any]]:
        total_lines = len(raw_lines)
        total_chars = sum(len(l) for l in raw_lines)
        base_speed = (total_chars / max(duration_sec, 30)) if duration_sec else 4.2  # 字/秒

        results = []
        elapsed_sec = 0.0

        for idx, line in enumerate(raw_lines):
            # 计算时间戳
            mins = int(elapsed_sec // 60)
            secs = int(elapsed_sec % 60)
            timestamp = f"{mins:02d}:{secs:02d}"

            # 语义角色与画面匹配
            role, visual, reusable = cls._classify_sentence(line, idx, total_lines)

            results.append({
                "index": idx + 1,
                "timestamp": timestamp,
                "sentence": line,
                "role": role,
                "visual": visual,
                "reusable": reusable,
            })

            elapsed_sec += max(1.5, len(line) / base_speed)

        return results

    @classmethod
    def _classify_sentence(cls, line: str, idx: int, total: int) -> tuple[str, str, str]:
        ratio = idx / max(1, total - 1)

        # 首句固定 hook
        if idx == 0:
            return cls.SENTENCE_ROLES[0][2], cls.SENTENCE_ROLES[0][3], cls.SENTENCE_ROLES[0][4]

        # 尾句固定 CTA
        if idx >= total - 2 or ratio >= 0.9:
            return cls.SENTENCE_ROLES[6][2], cls.SENTENCE_ROLES[6][3], cls.SENTENCE_ROLES[6][4]

        # 关键词匹配
        for _, keywords, role, visual, reusable in cls.SENTENCE_ROLES[1:6]:
            if any(k in line for k in keywords):
                return role, visual, reusable

        # 根据进度 fallback
        if ratio < 0.25:
            return "铺垫问题背景与行业现状", "真人出镜＋动效 (说明图/痛点卡)", "▲ 场景可复用：引入观众共鸣点"
        elif ratio < 0.7:
            return "核心实操拆解与论点递进", "真实录屏 (操作演示 / 局部特写)", "★ 极高复用：核心价值交付段落"
        else:
            return "进阶技巧提示与逻辑收束", "真人出镜＋动效 (认知收束金句)", "★ 极高复用：强化结论与专业度"

    @classmethod
    def _analyze_progression(cls, raw_lines: list[str], duration_sec: int) -> dict[str, Any]:
        total_len = len("".join(raw_lines))
        density_label = "极高密度信息流（每8-10秒提供一个新认知/操作成果）" if total_len > 800 else "标准紧凑叙事（节奏平稳，适合教程理解）"

        beats = [
            {"phase": "起 · 痛点唤醒", "timeRange": "00:00 - 00:15", "coreJob": "前3秒抛出黄金钩子，明确指出观众痛点与预期收益"},
            {"phase": "承 · 认知反转", "timeRange": "00:15 - 00:45", "coreJob": "打破传统低效做法，给出“判断先于界面”的高效新认知"},
            {"phase": "转 · 证明链条", "timeRange": "00:45 - 02:00", "coreJob": "真实录屏演示核心功能与实操步骤，建立确定性证据"},
            {"phase": "合 · 升维收束", "timeRange": "02:00 - 结束", "coreJob": "一句话升维回收痛点（不是X而是Y），引导观众收藏实践"},
        ]

        return {
            "beats": beats,
            "density": density_label,
            "rewardCadence": "采用【认知刺激 → 录屏实操 → 隐藏彩蛋】三层节奏交替，防止视觉疲劳",
            "proofMoment": "在进入实操前先亮出成品效果，缩短观众建立信任的等待时间",
        }

    @classmethod
    def _generate_dos_and_donts(cls, title: str, hook: dict[str, Any], raw_lines: list[str]) -> dict[str, list[str]]:
        return {
            "borrow": [
                "🎯 【开篇直奔主题】：前3秒坚决不做冗长的自我介绍，第一句话即给出核心判断或结果承诺；",
                "🪜 【递进逻辑骨架】：遵循「痛点唤醒 → 认知反转 → 真实证明 → 升维收束」的标准短视频爆款路径；",
                "💎 【一处必截图资产】：设置清晰的清单、步骤图或参数配置卡片，提供明确的收藏价值锚点；",
                "🎥 【三通道视觉切换】：真人观点用大字动效板，操作用真实录屏，抽象隐喻适度穿插全屏AI镜头。",
            ],
            "avoid": [
                "🚫 【切勿硬套人设】：不要复制原作者的独家口癖、个人履历、面部特征或社区身份；",
                "🚫 【切勿空口承诺】：涉及工具或代码演示时，必须有真实操作结果，杜绝仅靠口播假吹；",
                "🚫 【切勿贪多嚼不烂】：单条短视频聚焦解决 1 个最核心痛点，多层复杂体系应拆为合集。",
            ],
        }

    @classmethod
    def _empty_result(cls, title: str, creator: str, platform: str) -> dict[str, Any]:
        return {
            "version": "viral-video-benchmark/v1",
            "title": title,
            "creator": creator,
            "platform": platform,
            "durationSeconds": 0,
            "totalWords": 0,
            "hook": {"sentence": title, "type": "基础标题", "description": "暂无转写文本", "promise": title, "visibleEvidence": "—", "punchScore": 60},
            "lineByLine": [],
            "structureProgression": {"beats": [], "density": "—", "rewardCadence": "—", "proofMoment": "—"},
            "dosAndDonts": {"borrow": [], "avoid": []},
            "handoff": {"sourceTitle": title, "creator": creator, "platform": platform, "recommendedMode": "SHORT", "recommendedTemplate": "教程型收藏闭环", "coreContradiction": "", "keyTakeaways": []},
        }
