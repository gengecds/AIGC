"""Agent 6 - 字幕生成（FFmpeg SRT + drawtext）

根据剧本对话 + 分镜时长 → 生成SRT字幕文件

支持「当前激活风格」的字幕模型选择：
- subtitle_mode: rule(规则引擎,默认) / llm(大模型润色/翻译)
- subtitle_lang: zh(仅中文) / en(英) / biling(中英双语)
"""

import logging
from datetime import datetime

from agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)


class SubtitleAgent(Agent):
    """Agent 6：字幕生成"""

    name = "subtitle_agent"

    def __init__(self, llm_provider=None):
        super().__init__(name="subtitle_agent")
        self._llm_provider = llm_provider

    async def _translate(self, text: str, lang: str, model: str | None = None) -> str:
        """字幕语言切换：用本地 LLM 把对白翻译成目标语言（失败时原样返回）。"""
        if lang == "zh":
            return text  # 中文即源语言，不翻译
        try:
            from providers.llm import OllamaProvider
            llm = self._llm_provider or OllamaProvider(model=model)
            target = "English" if lang == "en" else "English and Chinese"
            raw = await llm.generate(
                prompt=f"只翻译并输出字幕文本，不要任何解释：{text}\n目标：{target}",
                system_prompt="你是字幕翻译助手，直接输出翻译结果，禁止输出额外说明。",
                model=model,
                temperature=0.3,
                num_predict=512,
            )
            return raw.strip() or text
        except Exception as e:
            logger.warning(f"[SubtitleAgent] 翻译失败，使用原文: {e}")
            return text

    async def run(self, script: dict, storyboard: dict,
                  output_dir: str = "storage/output") -> AgentResult:
        logger.info("[SubtitleAgent] 生成字幕")

        # 读取「当前激活风格」的字幕模式/语言（可在前端步骤面板选择）
        mode, lang = "rule", "zh"
        try:
            from config.style_resolver import subtitle_config_for_style
            cfg = subtitle_config_for_style()
            mode = cfg.get("subtitle_mode") or "rule"
            lang = cfg.get("subtitle_lang") or "zh"
        except Exception:
            pass

        episodes = storyboard.get("data", {}).get("episodes", []) or storyboard.get("episodes", [])
        script_episodes = script.get("episodes", [])
        all_srt = []

        # llm 模式：整本国字幕待翻译对白一次性收集，再逐句翻译
        use_llm = mode == "llm" and lang != "zh"
        translate_cache: dict[str, str] = {}

        for ep in episodes:
            ep_num = ep.get("episode_number", 1)
            shots = ep.get("shots", [])
            srt_lines = []
            time_cursor = 0  # 秒

            for shot in shots:
                dur = shot.get("duration", 5)
                dialogue = shot.get("dialogue", "")

                if dialogue:
                    if use_llm:
                        if dialogue not in translate_cache:
                            translate_cache[dialogue] = await self._translate(dialogue, lang)
                        dialogue = translate_cache[dialogue]

                    start_s = time_cursor
                    end_s = time_cursor + dur

                    def fmt_time(seconds: int) -> str:
                        h = seconds // 3600
                        m = (seconds % 3600) // 60
                        s = seconds % 60
                        return f"{h:02d}:{m:02d}:{s:02d},000"

                    srt_lines.append(f"{len(srt_lines) + 1}")
                    srt_lines.append(
                        f"{fmt_time(start_s)} --> {fmt_time(end_s)}"
                    )
                    srt_lines.append(dialogue)
                    srt_lines.append("")

                time_cursor += dur

            srt_content = "\n".join(srt_lines)
            srt_path = f"{output_dir}/ep_{ep_num}.srt"
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(srt_content)

            all_srt.append({
                "episode_number": ep_num,
                "srt_path": srt_path,
            })
            logger.info(f"[SubtitleAgent] 字幕已生成: {srt_path}")

        result = AgentResult(
            success=True,
            data={"subtitles": all_srt},
            metadata={
                "agent": self.name,
                "timestamp": datetime.utcnow().isoformat(),
                "files": len(all_srt),
                "subtitle_mode": mode,
                "subtitle_lang": lang,
                "translated": len(translate_cache) if use_llm else 0,
            },
        )
        return result
