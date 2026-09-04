"""
DraftEngine: 深度融合 draft-ai-work-video 规范的导演级口播二创脚本引擎。
依据爆款骨架或用户自定义主题，一键生成 Sxx 逐句口播、内联画面指示【画面：…】与高转化截图贴图包。

来源：情报站 `launcher/engine/draft_engine.py`（纯标准库，零外部依赖）。
本文件为 AIGC intel 层的复制/封装，遵守「零破坏」接入原则，不引入跨项目硬依赖。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class DraftEngine:
    """导演级口播脚本二创引擎"""

    TEMPLATES = {
        "tutorial_save_loop": {
            "name": "教程型收藏闭环",
            "desc": "强承诺 → 降维比喻 → 清单提炼 → 关键实操 → 隐藏避坑 → 升维追更",
            "film_job": "手把手入门 / 资源清单 / 教程型收藏",
        },
        "judgment_first": {
            "name": "判断先于界面",
            "desc": "认知断言 → 痛点对照 → 真实录屏证明 → 降维模型 → 行动回收",
            "film_job": "认知口播 / 成品秀 / 改习惯",
        },
        "tool_demo": {
            "name": "工具实战演示",
            "desc": "结果先亮 → 输入动作结果链 → 隐藏技巧彩蛋 → 资料包领取",
            "film_job": "工具测评 / 深度案例拆解",
        },
        "short_fast": {
            "name": "极速短视频 (35-65s)",
            "desc": "前3秒黄金钩子 → 1个核心矛盾 → 1套极速解法 → 1张保存卡片",
            "film_job": "单点技巧 / 极速认知提炼",
        },
    }

    @classmethod
    def generate_script(
        cls,
        topic: str,
        template_key: str = "tutorial_save_loop",
        mode: str = "SHORT",
        source_context: dict[str, Any] | None = None,
        custom_instructions: str = "",
    ) -> dict[str, Any]:
        source = source_context or {}
        tpl = cls.TEMPLATES.get(template_key, cls.TEMPLATES["tutorial_save_loop"])
        title = topic or source.get("title") or "未命名二创作品"
        raw_text = source.get("transcript", "") or source.get("summary", "")

        # 提取核心关键词与痛点
        keywords = cls._extract_keywords(title, raw_text)
        main_topic = keywords.get("topic", title)
        core_pain = keywords.get("pain", "很多人还在用老办法死磕，效率低还容易出错")
        core_solution = keywords.get("solution", "一套真正跑通的 AI-native 工作流")

        # 构造 Sxx 导演级逐句口播与分镜
        beats, cards = cls._compose_beats(
            title=title,
            main_topic=main_topic,
            core_pain=core_pain,
            core_solution=core_solution,
            template_key=template_key,
            mode=mode,
            raw_text=raw_text,
        )

        # 组装完整的 script.md
        script_markdown = cls._render_script_markdown(
            title=title,
            mode=mode,
            template_name=tpl["name"],
            film_job=tpl["film_job"],
            beats=beats,
            cards=cards,
        )

        # 提取纯提词器文本
        teleprompter_text = "\n\n".join([f"{b['id']}｜{b['speech']}" for b in beats])

        return {
            "version": "draft-ai-work-video/v1",
            "title": title,
            "mode": mode,
            "templateKey": template_key,
            "templateName": tpl["name"],
            "filmJob": tpl["film_job"],
            "totalBeats": len(beats),
            "estimatedDurationSeconds": int(sum(len(b["speech"]) for b in beats) / 4.0),
            "beats": beats,
            "cards": cards,
            "teleprompterText": teleprompter_text,
            "scriptMarkdown": script_markdown,
        }

    @classmethod
    def _extract_keywords(cls, title: str, raw_text: str) -> dict[str, str]:
        # 简单提取主题与认知点
        topic = title.replace("如何", "").replace("怎样", "").replace("为什么", "").strip()
        pain = "很多人面对这个场景还在手动盲测，反复踩坑效率极低"
        solution = "掌握这套核心底层逻辑与工具链，一个人就能顶一个团队"

        if "ai" in title.lower() or "native" in title.lower() or "组织" in title:
            pain = "传统团队把 AI 当打工外挂，结果组织臃肿、交付依然缓慢"
            solution = "真正的 AI-native 是从第一天就用自闭环代码和工作流重构生产方式"
        elif "twitter" in title.lower() or "涨粉" in title or "内容" in title:
            pain = "天天日更几小时却毫无互动，找不到爆款的底层机制"
            solution = "用内容杠杆和高信息密度清单，精准抓住目标受众的核心注意力"
        elif "youtube" in title.lower() or "视频" in title or "插件" in title:
            pain = "长视频动辄半小时根本看不完，抓不住重点浪费大量时间"
            solution = "用自动化插件一键提取精准逐字稿与核心要点，快速吸取精华"

        return {"topic": topic, "pain": pain, "solution": solution}

    @classmethod
    def _compose_beats(
        cls,
        title: str,
        main_topic: str,
        core_pain: str,
        core_solution: str,
        template_key: str,
        mode: str,
        raw_text: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        beats: list[dict[str, Any]] = []
        cards: list[dict[str, Any]] = []

        if template_key == "judgment_first":
            # 判断先于界面模式
            beats = [
                {
                    "id": "S01",
                    "channel": "真人出镜＋动效",
                    "visualCue": "【画面：真人近景出镜，背景微暗，正上方弹出高对比度大字断言卡「判断先于界面」】",
                    "speech": f"别再到处找教程了！关于{main_topic}，绝大多数人都把顺序完全搞反了。",
                },
                {
                    "id": "S02",
                    "channel": "真人出镜＋动效",
                    "visualCue": "【画面：画面左侧展示常见错误路径打叉，右侧切入高价值对比卡】",
                    "speech": f"{core_pain}。如果你不先想清楚底层交付物，换再多工具也是白搭。",
                },
                {
                    "id": "S03",
                    "channel": "真实录屏",
                    "visualCue": "【画面：屏幕切入真实工作界面，鼠标精准点击运行核心工作流，展示秒级输出】",
                    "speech": f"你看这套实操逻辑：{core_solution}，输入明确指令后直接跑出最终结果。",
                },
                {
                    "id": "S04",
                    "channel": "真实录屏",
                    "visualCue": "【画面：镜头特写局部核心配置参数与关键细节，停留2秒】",
                    "speech": "这里有个关键细节很多人会漏掉：把核心规则固定在系统提示词里，避免反复微调。",
                },
                {
                    "id": "S05",
                    "channel": "真人出镜＋动效",
                    "visualCue": "【画面：全屏展示贴图 A「核心方法对照清单」，停留2秒提示截图保存】",
                    "speech": "把这张核心对照表截图存好。记住：省的不是几分钟操作时间，而是你反复试错的决策成本。",
                },
                {
                    "id": "S06",
                    "channel": "真人出镜＋动效",
                    "visualCue": "【画面：切回真人半身，右下角弹出关注与评论区指引卡】",
                    "speech": "完整配置清单和提示词我整理好了，评论区回复对应关键词，直接发你！",
                },
            ]
            cards = [
                {
                    "id": "贴图 A",
                    "title": f"《{main_topic} · 核心认知与避坑对照表》",
                    "type": "对照清单卡",
                    "content": "【低效误区】把 AI 当纯外挂，手动搬运重复流程\n【高效解法】自闭环工作流，输入即出成品\n【关键细节】系统级规则沉淀，杜绝每次从零调试",
                }
            ]
        elif template_key == "short_fast":
            # 极速短视频模式
            beats = [
                {
                    "id": "S01",
                    "channel": "真人出镜＋动效",
                    "visualCue": "【画面：0.0s 极速开场，真人手持设备或直视镜头，上方大字「30秒搞定」】",
                    "speech": f"搞定{main_topic}，千万不要再去死记复杂的步骤！",
                },
                {
                    "id": "S02",
                    "channel": "真实录屏",
                    "visualCue": "【画面：极速切换到电脑录屏，光标一键点击执行，直接展示成品效果】",
                    "speech": f"直接用这套三步法：打开配置，填入核心提示词，点击一键生成。",
                },
                {
                    "id": "S03",
                    "channel": "全屏AI视频",
                    "visualCue": "【画面：全屏科幻数据流穿梭氛围换气镜头，无人出镜，节奏轻快】",
                    "speech": "以前要折腾一整天的事情，现在只要一分钟就能搞定。",
                },
                {
                    "id": "S04",
                    "channel": "真人出镜＋动效",
                    "visualCue": "【画面：弹出贴图 A 全屏高清卡片，提示长按收藏】",
                    "speech": "这套口诀先点赞收藏起来，下期带你实操进阶玩法！",
                },
            ]
            cards = [
                {
                    "id": "贴图 A",
                    "title": f"{main_topic} 极速极简实操卡",
                    "type": "极简操作卡",
                    "content": "步骤一：拉取基础模板\n步骤二：注入核心业务规则\n步骤三：一键导出并验收",
                }
            ]
        else:
            # 默认：教程型收藏闭环 (Tutorial Save Loop)
            beats = [
                {
                    "id": "S01",
                    "channel": "真人出镜＋动效",
                    "visualCue": "【画面：真人出镜，眼神坚定，开篇右上角展示成品高分徽章，前1.5秒微变焦】",
                    "speech": f"今年你可以不学任何花哨的概念，但{main_topic}这套逻辑，你一定要彻底搞懂。",
                },
                {
                    "id": "S02",
                    "channel": "真人出镜＋动效",
                    "visualCue": "【画面：左侧展示痛点场景插画，右侧打出醒目红字警示】",
                    "speech": f"{core_pain}。今天我用最接地气的方式，帮你一次性理清。",
                },
                {
                    "id": "S03",
                    "channel": "真人出镜＋动效",
                    "visualCue": "【画面：切入信息板「核心三步法全景图」，建立框架预期】",
                    "speech": "整个流程其实就分三步：第一步定标准，第二步建流程，第三步做自动化交付。",
                },
                {
                    "id": "S04",
                    "channel": "真实录屏",
                    "visualCue": "【画面：屏幕切入实操演示，逐项录制操作过程，重点步骤放大高亮】",
                    "speech": f"首先看第一步实操：{core_solution}。这一步做扎实，后面就能全自动运转。",
                },
                {
                    "id": "S05",
                    "channel": "真实录屏",
                    "visualCue": "【画面：录屏展示关键避坑设置，鼠标在关键勾选项打圈提示】",
                    "speech": "这里有个 90% 的人都踩过的坑：参数千万别选默认值，调到这个档位才是最佳效果。",
                },
                {
                    "id": "S06",
                    "channel": "真人出镜＋动效",
                    "visualCue": "【画面：全屏展示贴图 A「实操避坑与完整清单」，全屏停留 2 秒建议截图】",
                    "speech": "这张完整的操作清单建议大家直接截图保存，做的时候随时对着看。",
                },
                {
                    "id": "S07",
                    "channel": "真人出镜＋动效",
                    "visualCue": "【画面：切回真人近景，收尾升维口播，右下角弹出互动提示】",
                    "speech": "工具永远只是放大器，真正的壁垒是你的业务思考。关注我，持续为你拆解实战方法！",
                },
            ]
            cards = [
                {
                    "id": "贴图 A",
                    "title": f"《{main_topic} · 完整实操闭环清单》",
                    "type": "收藏级清单包",
                    "content": "1. 核心准备：明确交付目标与标准样本\n2. 关键配置：自定义规则注入与边界限制\n3. 提效秘诀：避开默认参数误区，启用批处理\n4. 验收要点：严格对齐业务真实结果",
                }
            ]

        return beats, cards

    @classmethod
    def _render_script_markdown(
        cls,
        title: str,
        mode: str,
        template_name: str,
        film_job: str,
        beats: list[dict[str, Any]],
        cards: list[dict[str, Any]],
    ) -> str:
        md_lines = [
            f"# 二创导演口播脚本：{title}",
            "",
            "> 遵循 `draft-ai-work-video` 导演级标准交付：Sxx 逐句口播 + 三通道视觉分镜 + 贴图卡片包。",
            "",
            "## 📋 拍摄决策头 (Decision Header)",
            f"- **作品定位**：{film_job}",
            f"- **创作模式**：{mode} (预计时长约 {int(sum(len(b['speech']) for b in beats) / 4.0)} 秒)",
            f"- **采用模板**：{template_name}",
            "- **视觉通道**：真人出镜＋动效 (信任/断言) ｜ 真实录屏 (实操证明) ｜ 全屏AI视频 (空间换气)",
            "",
            "---",
            "",
            "## 🎬 逐句分镜与口播脚本 (Director Spoken Units)",
            "",
        ]

        for b in beats:
            md_lines.extend([
                f"### {b['id']} · 【{b['channel']}】",
                f"{b['visualCue']}",
                f"> **口播**：{b['speech']}",
                "",
            ])

        md_lines.extend([
            "---",
            "",
            "## 🖼️ 观众高转化截图贴图包 (On-screen Cards)",
            "",
        ])

        for c in cards:
            md_lines.extend([
                f"### {c['id']}：{c['title']} ({c['type']})",
                "```text",
                c["content"],
                "```",
                "",
            ])

        return "\n".join(md_lines)
