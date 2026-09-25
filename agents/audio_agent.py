"""Agent 8 - 音频合成（配音 + 背景音乐 + 音效）

把无声成片变成有声成片：
1. 用 Piper TTS（本地中文女声）把剧本对白合成配音
2. 用 numpy 程序合成柔和钢琴 BGM 垫底
3. 结尾叠加品牌"叮"提示音
4. FFmpeg 混音 → 音轨合进成片

输入：compose 成片 + 剧本（对白）
输出：带音频的成片 + 音轨文件
"""

import logging
import asyncio
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)

# 每句台词之后的自然停顿（秒）。镜头时长 = 该句配音真实时长 + LINE_GAP，
# 分镜/字幕/成片/混音四处共用它，保证成片时长 ≈ 配音总时长且逐句对位。
LINE_GAP = 0.4


class AudioAgent(Agent):
    """Agent 8：配音 + BGM + 音效合成"""

    name = "audio_agent"

    def __init__(self):
        super().__init__(name="audio_agent")

    async def run(self, compose_result, script_result, subtitle_result=None,
                  plan: dict | None = None,
                  storyboard_result=None,
                  voice_plan: list | None = None) -> AgentResult:
        # 音频合成全程是 CPU/子进程密集的同步代码（Piper TTS、numpy 合成、FFmpeg 混音），
        # 放到线程池执行，避免阻塞后端事件循环（否则视频生成期间 HTTP 无响应）
        return await asyncio.to_thread(
            self._run_sync, compose_result, script_result, subtitle_result, plan,
            storyboard_result, voice_plan,
        )

    def _run_sync(self, compose_result, script_result, subtitle_result=None,
                  plan: dict | None = None, storyboard_result=None,
                  voice_plan: list | None = None) -> AgentResult:
        logger.info("[AudioAgent] 开始音频合成（配音+BGM+音效）")

        # 1. 定位成片
        video_path = self._find_final_video(compose_result)
        if not video_path or not Path(video_path).exists():
            return AgentResult(success=False, error="找不到合成成片，无法加音频")

        # 2. 成片时长
        duration = self._get_duration(video_path)
        if duration <= 0:
            return AgentResult(success=False, error="无法读取成片时长")

        # 3. 逐句台词：有配音预测量就直接用它（顺序与时长与分镜、字幕完全一致），
        #    否则从分镜/剧本/字幕提取文本与角色（时间轴统一由 _assign_offsets 按真实时长铺开）
        if voice_plan:
            utterances = [
                {"character": p.get("character") or "", "text": p.get("text") or ""}
                for p in voice_plan
                if isinstance(p, dict) and (p.get("text") or "")
            ]
        else:
            utterances = self._build_utterances(script_result, storyboard_result, subtitle_result)
        if not utterances:
            return AgentResult(success=False, error="没有可配音的对白")
        logger.info(
            f"[AudioAgent] 配音 {len(utterances)} 句"
            f"（预测量{'已启用' if voice_plan else '未启用'}），成片 {duration:.1f}s"
        )

        # 4. 合成配音（每句 TTS，按角色分配音色）
        from providers import audio_tools
        audio_dir = Path("storage/audio")
        audio_dir.mkdir(parents=True, exist_ok=True)

        # TTS 引擎与默认音色：优先用「当前激活风格」的 tts_engine / voice_prompt（可在前端步骤面板选择），
        # 风格没配置时回退到 config.yaml 的 tts 段（tts.engine = piper|voxcpm，voice_prompt 为音色描述）
        tts_engine, base_voice = self._tts_setting()

        # 按角色解析专属音色（gender/age/personality → voice_prompt），角色信息缺失时回退默认音色
        character_voices = self._build_character_voices(script_result, base_voice)

        # 管线预测量阶段已合成过的配音（按台词文本索引）→ 直接复用 WAV，避免二次 TTS
        voice_cache = {}
        for item in (voice_plan or []):
            text = item.get("text") if isinstance(item, dict) else ""
            wav = item.get("wav_path") if isinstance(item, dict) else ""
            if text and wav and Path(wav).exists():
                voice_cache.setdefault(text, item)
        if voice_cache:
            logger.info(f"[AudioAgent] 复用预测量配音 {len(voice_cache)}/{len(utterances)} 句")

        voice_tracks = []
        for i, u in enumerate(utterances):
            text = u["text"]
            character = u.get("character") or ""
            cached = voice_cache.get(text)
            if cached:
                wav = cached["wav_path"]
                dur = float(cached.get("dur") or 0)
                voice_prompt = cached.get("voice_prompt") or self._voice_for(
                    character, character_voices, base_voice
                )
            else:
                voice_prompt = self._voice_for(character, character_voices, base_voice)
                wav = audio_dir / f"voice_{i:02d}.wav"
                dur = audio_tools.synthesize_speech(
                    text, str(wav), engine=tts_engine, voice_prompt=voice_prompt
                )
            if dur > 0:
                voice_tracks.append({
                    "path": str(wav), "dur": dur, "text": text,
                    "character": character, "voice_prompt": voice_prompt,
                    "offset": u.get("offset"),
                })

        if not voice_tracks:
            return AgentResult(success=False, error="配音生成失败（TTS 无输出）")

        # 5. 时间分配：按分镜时长（= 各句配音真实时长）顺序落位，句间首尾相接、绝不重叠
        self._assign_offsets(voice_tracks, duration)

        # 6. 生成 BGM（按剧情情绪：优先本地音乐库，其次程序合成多风格）+ 环境底噪
        #    从研究方案读取情绪与场景声效，缺省回退"温馨"
        mood = (plan or {}).get("bgm_mood", "温馨") if plan else "温馨"
        scene_sounds = (plan or {}).get("scene_sounds", []) if plan else []

        bgm_wav = audio_dir / "bgm.wav"
        # 优先使用 storage/music/ 里用户下载的真实音乐（文件名匹配情绪）
        music_file = audio_tools.pick_music_file(mood)
        if music_file:
            bgm_ok = audio_tools.make_bgm_from_file(music_file, duration, str(bgm_wav), volume=0.4)
        else:
            bgm_ok = audio_tools.generate_bgm_mood(mood, duration, str(bgm_wav), volume=0.38)
        if not bgm_ok:
            # 兜底：单风格程序合成
            bgm_ok = audio_tools.generate_bgm(duration, str(bgm_wav), bpm=70, volume=0.32)

        ambient_wav = audio_dir / "ambient.wav"
        audio_tools.generate_ambient(duration, str(ambient_wav), volume=0.12)

        # 7. 场景音效：按分镜时间轴插入（滋滋声/雨声/风声/车流等）
        #    每个音效按剧情时段铺开：前 1/4、中段、后 1/4 各一个窗口
        sfx_tracks = []
        if scene_sounds:
            audio_tools.generate_sfx(str(scene_sounds[0]), duration, str(audio_dir / "sfx_main.wav"), volume=0.5)
            sfx_tracks.append({"path": str(audio_dir / "sfx_main.wav"), "offset": 0, "volume": 0.5})
            if len(scene_sounds) > 1:
                audio_tools.generate_sfx(str(scene_sounds[1]), duration * 0.5, str(audio_dir / "sfx_mid.wav"), volume=0.4)
                sfx_tracks.append({"path": str(audio_dir / "sfx_mid.wav"), "offset": duration * 0.25, "volume": 0.4})

        # 8. 结尾提示音（成片足够长时）
        chime_track = None
        if duration > 4.0:
            chime_wav = audio_dir / "chime.wav"
            audio_tools.generate_chime(1.6, str(chime_wav), volume=0.5)
            chime_track = {"path": str(chime_wav), "offset": max(0, duration - 2.0), "volume": 0.55}

        # 9. 混合所有音轨（环境音打底 + BGM + 场景音效 + 配音 + 提示音）
        #    BGM/环境让位人声，配音为主（配合 mix 软限幅防爆音）
        mix_tracks = [
            {"path": str(ambient_wav), "offset": 0, "volume": 0.10},
            {"path": str(bgm_wav), "offset": 0, "volume": 0.32},
        ]
        mix_tracks += sfx_tracks
        mix_tracks += [{"path": t["path"], "offset": t["offset"], "volume": 1.15} for t in voice_tracks]
        if chime_track:
            mix_tracks.append(chime_track)

        mixed_wav = audio_dir / "audio_track.wav"
        audio_tools.mix_audio(mix_tracks, duration, str(mixed_wav))

        # 9. 音轨合进成片（轻增益 + 温和压缩 + 首尾淡入淡出）
        #    不再叠 aecho 混响：混响会把台词糊成一团，是"音质渣"的主因之一
        final_out = str(Path(video_path).with_name(
            Path(video_path).stem + "_audio.mp4"
        ))
        af = (
            "volume=1.1,"
            "acompressor=threshold=-20dB:ratio=2:attack=10:release=180,"
            f"afade=t=in:st=0:d=0.25,afade=t=out:st={max(0, duration - 0.6):.2f}:d=0.6"
        )
        result_path = audio_tools.mux_audio_to_video(video_path, str(mixed_wav), final_out, audio_filter=af)

        if not result_path:
            return AgentResult(success=False, error="音视频合成失败（FFmpeg）")

        return AgentResult(
            success=True,
            data={
                "final_video": result_path,
                "audio_track": str(mixed_wav),
                "voices": [{"text": t["text"], "character": t.get("character", ""),
                            "offset": round(t["offset"], 2),
                            "duration": round(t["dur"], 2)} for t in voice_tracks],
                "bgm": str(bgm_wav),
                "duration": round(duration, 2),
            },
            metadata={
                "agent": self.name,
                "timestamp": datetime.utcnow().isoformat(),
                "dialogues": len(voice_tracks),
                "video_duration": round(duration, 2),
            },
        )

    # ── 辅助方法 ──────────────────────────

    def _find_final_video(self, compose_result) -> str:
        """从 compose 结果里找成片 mp4 路径（兼容多种结构）"""
        data = compose_result.data if hasattr(compose_result, "data") else {}
        data = data or {}

        def _walk(obj, found):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, str) and v.endswith((".mp4", ".webm", ".mov")):
                        found.append(v)
                    else:
                        _walk(v, found)
            elif isinstance(obj, list):
                for it in obj:
                    _walk(it, found)

        found = []
        _walk(data, found)
        # 优先 _audio 结尾的或 final 的
        for f in found:
            if "audio" in f:
                return f
        for f in found:
            if "final" in f:
                return f
        return found[0] if found else ""

    def _get_duration(self, video_path: str) -> float:
        """ffprobe 读取视频时长（秒）"""
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(video_path)],
                capture_output=True, text=True, timeout=20,
            )
            return float(r.stdout.strip())
        except Exception:
            return 0.0

    def _extract_dialogues(self, script_result) -> list[str]:
        """从剧本提取对白文本（兼容多种字段）"""
        data = script_result.data if hasattr(script_result, "data") else {}
        data = data or {}
        texts = []

        def _collect(obj):
            if isinstance(obj, dict):
                # 直接对白字段
                for key in ("text", "content", "line", "dialogue"):
                    if key in obj and isinstance(obj[key], str) and obj[key].strip():
                        texts.append(obj[key].strip())
                        return
                # 递归
                for v in obj.values():
                    _collect(v)
            elif isinstance(obj, list):
                for it in obj:
                    _collect(it)

        _collect(data)
        # 去重保序
        seen = set()
        result = []
        for t in texts:
            if t not in seen:
                seen.add(t)
                result.append(t)
        return result

    # ── 配音时长预测量 ──────────────────────

    def _tts_setting(self) -> tuple[str, str]:
        """取当前风格（或 config.yaml 兜底）的 TTS 引擎与默认音色"""
        try:
            from config.style_resolver import tts_for_style
            return tts_for_style()
        except Exception:
            pass
        try:
            from config.settings import settings
            tts_cfg = settings.tts
            return (
                getattr(tts_cfg, "engine", "piper") or "piper",
                getattr(tts_cfg, "voice_prompt", "") or "",
            )
        except Exception:
            return "piper", ""

    @staticmethod
    def _estimate_duration(text: str) -> float:
        """TTS 不可用时的时长兜底：按中文字数估算（约 4 字/秒）"""
        return round(max(1.0, len(text or "") * 0.24), 2)

    def plan_voices(self, script_result, output_dir: str = "storage/audio/plan",
                    on_progress: Callable | None = None) -> list[dict]:
        """预合成每句配音，返回「逐句时长表」。

        这是「时长由配音决定」方案的唯一时长数据源：分镜按它切镜、字幕按它排时间轴、
        成片按它拉伸、混音按它落位，四处共用同一套时长，才能做到台词逐句首尾相接、
        字幕与画面精确对位、音色与人物对应。

        返回 [{index, episode_number, text, character, voice_prompt, dur, slot, wav_path}]；
        dur 为 TTS 真实时长，slot = dur + LINE_GAP 即该句应占用的镜头时长；
        TTS 不可用时用字数估算 dur 兜底（wav_path 为空，后续由 audio_agent 重新合成）。

        on_progress(dict) 为可选的同步进度回调，逐句合成后（以及开始前一条）被调用，
        载荷 {done, total, percent, character, text, elapsed, eta}，供上层转发到前端
        展示「配音预测量 3/7」，避免用户在冗长的 TTS 合成期间看不到任何反馈。
        """
        from providers import audio_tools

        # 兼容两种入参：管线里 script_data 是纯 dict，独立调用时可能是 AgentResult
        if not hasattr(script_result, "data"):
            script_result = AgentResult(success=True, data=script_result or {})

        utterances = self._dialogues_with_character(script_result)
        if not utterances:
            logger.warning("[AudioAgent] 剧本无对白，跳过配音预测量")
            return []

        tts_engine, base_voice = self._tts_setting()
        character_voices = self._build_character_voices(script_result, base_voice)

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # 先预热模型：把首句二三十秒的「模型加载」显式打日志，避免用户以为卡死
        n = len(utterances)
        logger.info(
            f"[AudioAgent] 配音预测量开始: {n} 句，引擎={tts_engine}"
            f"（首次需加载模型，请稍候）"
        )
        # 先推一条「共 N 句」的起始进度：预热可能要二三十秒，前端需要早知道总量
        if on_progress:
            try:
                on_progress({
                    "done": 0, "total": n, "percent": 0,
                    "character": "", "text": "", "elapsed": 0.0, "eta": 0.0,
                })
            except Exception as e:
                logger.debug(f"[AudioAgent] 进度回调失败（忽略）: {e}")

        try:
            load_s = audio_tools.warm_up_tts(tts_engine)
            logger.info(f"[AudioAgent] TTS 预热完成，模型加载用时 {load_s:.1f}s")
        except Exception as e:
            logger.warning(f"[AudioAgent] TTS 预热失败（合成时再重试）: {e}")

        plan = []
        t_start = time.time()
        for i, u in enumerate(utterances):
            text = u["text"]
            character = u.get("character") or ""
            voice_prompt = self._voice_for(character, character_voices, base_voice)
            wav = out_dir / f"line_{i:03d}.wav"
            dur = 0.0
            t0 = time.time()
            try:
                dur = audio_tools.synthesize_speech(
                    text, str(wav), engine=tts_engine, voice_prompt=voice_prompt
                )
            except Exception as e:
                logger.warning(f"[AudioAgent] 第{i + 1}句预合成失败: {e}")
            if dur > 0:
                wav_path = str(wav)
            else:
                dur = self._estimate_duration(text)
                wav_path = ""
            plan.append({
                "index": i,
                "episode_number": u.get("episode_number") or 1,
                "text": text,
                "character": character,
                "voice_prompt": voice_prompt,
                "dur": round(float(dur), 3),
                "slot": round(float(dur) + LINE_GAP, 3),
                "wav_path": wav_path,
            })
            # 逐句进度：句序 / 百分比 / 本句用时 / 累计用时 / 预计剩余
            done = i + 1
            elapsed = time.time() - t_start
            eta = elapsed / done * (n - done)
            logger.info(
                f"[AudioAgent] 配音预测量 {done}/{n} ({done * 100 // n}%) "
                f"本句 {time.time() - t0:.1f}s 累计 {elapsed:.0f}s 剩余约 {eta:.0f}s | "
                f"{character or '旁白'}: {text[:16]}"
            )
            if on_progress:
                try:
                    on_progress({
                        "done": done, "total": n, "percent": done * 100 // n,
                        "character": character or "旁白", "text": text,
                        "elapsed": round(elapsed, 1), "eta": round(eta, 1),
                    })
                except Exception as e:
                    logger.debug(f"[AudioAgent] 进度回调失败（忽略）: {e}")

        logger.info(
            f"[AudioAgent] 配音预测量完成: {len(plan)} 句，总时长 "
            f"{sum(p['dur'] for p in plan):.1f}s，总用时 {time.time() - t_start:.1f}s"
        )
        return plan

    # ── 对白提取（角色 + 时间轴）──────────────

    def _build_utterances(self, script_result, storyboard_result, subtitle_result) -> list[dict]:
        """构建对白列表，每项 {"character", "text"}。

        优先级：分镜（有角色）> 剧本（有角色）> 字幕。

        分镜台词常把多角色台词合并成一段（"陈屿：…林小满：…"），这里按说话人拆句，
        并用剧本台词表反查角色。这里只负责「拿到逐句文本与角色」，不做时间轴估算：
        时间轴一律由 _assign_offsets 按配音真实时长顺序铺开（避免与真实语音时长脱节导致重叠）。
        """
        names = self._known_names(script_result)
        by_text = self._speaker_by_text(script_result)

        shots = self._collect_shots(storyboard_result)
        if shots:
            utts = []
            for s in shots:
                line = self._clean_line(s.get("dialogue"))
                shot_chars = s.get("characters") if isinstance(s.get("characters"), list) else []
                fallback = self._first_character(shot_chars)
                pieces = self._split_speakers(
                    line,
                    list(dict.fromkeys([n for n in shot_chars if isinstance(n, str)] + names)),
                ) if line else []
                for name, text in pieces:
                    utts.append({
                        "character": name or by_text.get(text, "") or fallback,
                        "text": text,
                    })
            if utts:
                return utts

        utts = self._dialogues_with_character(script_result)
        if utts:
            return utts

        return self._utterances_from_srt(subtitle_result)

    def _known_names(self, script_result) -> list[str]:
        """剧本 characters[].name 列表（用于识别分镜台词里的说话人前缀）"""
        data = script_result.data if hasattr(script_result, "data") else {}
        data = data or {}
        chars = data.get("characters") if isinstance(data, dict) else None
        names = []
        if isinstance(chars, list):
            for c in chars:
                if isinstance(c, dict):
                    n = self._clean_line(c.get("name"))
                    if n:
                        names.append(n)
        return names

    def _speaker_by_text(self, script_result) -> dict:
        """台词文本 → 角色名（分镜台词没标说话人时反查用）"""
        table = {}
        for u in self._dialogues_with_character(script_result):
            if u.get("character") and u.get("text"):
                table.setdefault(u["text"], u["character"])
        return table

    @staticmethod
    def _split_speakers(dialogue: str, names: list[str]) -> list[tuple[str, str]]:
        """把「陈屿：…林小满：…」拆成 [("陈屿", "…"), ("林小满", "…")]。

        仅把「行首或句末标点后」的「名字：」当作说话人标记，避免把台词正文里
        提到的角色名误判为说话人。无标记时返回 [("", dialogue)]。
        """
        if not dialogue:
            return []
        cand = sorted({n for n in names if n}, key=len, reverse=True)  # 长名优先，避免子串误切
        if not cand:
            return [("", dialogue)]
        pattern = re.compile("(" + "|".join(re.escape(n) for n in cand) + ")：")
        marks = []
        for m in pattern.finditer(dialogue):
            pos = m.start()
            if pos == 0 or dialogue[pos - 1] in "。！？…\n\r 　\t":
                marks.append((pos, m.end(), m.group(1)))
        if not marks:
            return [("", dialogue)]

        out = []
        lead = dialogue[:marks[0][0]].strip(" 　\t。，,、")
        if lead:
            out.append(("", lead))
        for i, (_s, e, name) in enumerate(marks):
            end = marks[i + 1][0] if i + 1 < len(marks) else len(dialogue)
            text = dialogue[e:end].strip(" 　\t")
            if text:
                out.append((name, text))
        return out or [("", dialogue)]

    @staticmethod
    def _clean_line(line) -> str:
        """清理台词文本（去引号/空白）"""
        if not isinstance(line, str):
            return ""
        return line.strip().strip('"').strip("“”").strip()

    @staticmethod
    def _first_character(characters) -> str:
        """取分镜 characters 里的第一个角色名"""
        if isinstance(characters, list) and characters:
            first = characters[0]
            if isinstance(first, str) and first.strip():
                return first.strip()
        return ""

    def _collect_shots(self, storyboard_result) -> list[dict]:
        """汇总分镜列表（兼容 episodes[].shots[] 与顶层 shots[]）"""
        data = storyboard_result.data if hasattr(storyboard_result, "data") else {}
        data = data or {}
        shots = []
        episodes = data.get("episodes") if isinstance(data, dict) else None
        if isinstance(episodes, list):
            for ep in episodes:
                if isinstance(ep, dict) and isinstance(ep.get("shots"), list):
                    shots.extend(s for s in ep["shots"] if isinstance(s, dict))
        if not shots and isinstance(data, dict) and isinstance(data.get("shots"), list):
            shots = [s for s in data["shots"] if isinstance(s, dict)]
        return shots

    def _dialogues_with_character(self, script_result) -> list[dict]:
        """从剧本 episodes[].dialogues[] 提取带角色的对白"""
        data = script_result.data if hasattr(script_result, "data") else {}
        data = data or {}
        utts = []
        episodes = data.get("episodes") if isinstance(data, dict) else None
        if isinstance(episodes, list):
            for ep in episodes:
                if not isinstance(ep, dict):
                    continue
                for dlg in ep.get("dialogues") or []:
                    if not isinstance(dlg, dict):
                        continue
                    line = self._clean_line(dlg.get("line") or dlg.get("text"))
                    if line:
                        utts.append({
                            "character": self._clean_line(dlg.get("character")),
                            "text": line,
                            "episode_number": ep.get("episode_number") or 1,
                            "offset": None,
                        })
        if utts:
            return utts
        # 兼容旧结构：递归取纯文本（无角色）
        return [{"character": "", "text": t, "offset": None}
                for t in self._extract_dialogues(script_result)]

    def _utterances_from_srt(self, subtitle_result) -> list[dict]:
        """从 SRT 提取对白；能解析出时间戳则带 offset，否则仅文本"""
        data = subtitle_result.data if hasattr(subtitle_result, "data") else {}
        data = data or {}
        utts = []
        for v in (data.values() if isinstance(data, dict) else []):
            if not isinstance(v, str):
                continue
            offset = None
            for line in v.splitlines():
                line = line.strip()
                if not line or line.isdigit():
                    continue
                if "-->" in line:
                    offset = self._parse_srt_time(line.split("-->")[0])
                    continue
                utts.append({"character": "", "text": line, "offset": offset})
                offset = None
        return utts

    @staticmethod
    def _parse_srt_time(stamp: str) -> float | None:
        """'00:00:01,200' → 1.2（解析失败返回 None）"""
        m = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)", stamp or "")
        if not m:
            return None
        h, mi, s, ms = (int(x) for x in m.groups())
        return h * 3600 + mi * 60 + s + ms / 1000

    # ── 角色音色 ──────────────────────────

    def _build_character_voices(self, script_result, base_voice: str) -> dict:
        """按剧本 characters[] 为每个角色生成专属音色描述"""
        data = script_result.data if hasattr(script_result, "data") else {}
        data = data or {}
        characters = data.get("characters") if isinstance(data, dict) else None
        voices = {}
        if isinstance(characters, list):
            for c in characters:
                if not isinstance(c, dict):
                    continue
                name = self._clean_line(c.get("name"))
                if name:
                    voices[name] = self._voice_prompt_for(c, base_voice)
        if voices:
            logger.info("[AudioAgent] 角色音色: "
                        + "；".join(f"{k}={v}" for k, v in voices.items()))
        return voices

    def _voice_prompt_for(self, character: dict, base_voice: str) -> str:
        """按 gender/age/personality 生成音色描述（男女老少区分开）"""
        gender = str(character.get("gender") or "")
        age = str(character.get("age") or "")
        personality = str(character.get("personality") or "")
        role = str(character.get("role") or "")

        if any(k in gender for k in ("女", "female", "woman")):
            g = "女"
        elif any(k in gender for k in ("男", "male", "man")):
            g = "男"
        else:
            g = ""

        voice_map = {
            ("女", "少年"): "少女声，音色清脆明亮",
            ("女", "青年"): "年轻女声，音色清澈柔和",
            ("女", "中年"): "成熟女声，音色温婉沉稳",
            ("女", "老年"): "老年女声，音色沙哑慈祥",
            ("男", "少年"): "少年男声，音色清亮稚气",
            ("男", "青年"): "年轻男声，音色清朗有力",
            ("男", "中年"): "中年男声，音色低沉浑厚",
            ("男", "老年"): "老年男声，音色沧桑缓慢",
        }
        desc = voice_map.get((g, self._age_stage(age)))
        if not desc:
            desc = base_voice or "自然清晰的中文配音"

        tone = self._tone_word(personality or role)
        if tone:
            desc = f"{tone}语气，{desc}"
        return desc

    @staticmethod
    def _age_stage(age: str) -> str:
        """年龄 → 少年/青年/中年/老年"""
        m = re.search(r"\d+", age or "")
        if m:
            n = int(m.group())
            return "少年" if n < 18 else "青年" if n < 35 else "中年" if n < 60 else "老年"
        if any(k in age for k in ("童", "儿", "少年")):
            return "少年"
        if any(k in age for k in ("老", "暮")):
            return "老年"
        if "中" in age:
            return "中年"
        return "青年"

    @staticmethod
    def _tone_word(text: str) -> str:
        """从 personality/role 里提取语气关键词"""
        for kw, tone in (
            ("活泼", "活泼"), ("开朗", "开朗"), ("调皮", "俏皮"), ("温柔", "温柔"),
            ("甜美", "甜美"), ("可爱", "甜美"), ("沉稳", "沉稳"), ("冷静", "冷静"),
            ("高冷", "冷峻"), ("冷", "冷峻"), ("热情", "热情"), ("成熟", "成熟"),
            ("豪爽", "豪爽"), ("严肃", "严肃"), ("忧郁", "低沉"), ("阴沉", "低沉"),
            ("狡", "狡黠"), ("憨", "憨厚"),
        ):
            if kw in (text or ""):
                return tone
        return ""

    def _voice_for(self, character: str, character_voices: dict, base_voice: str) -> str:
        """取角色音色；名字未精确匹配时做包含匹配，仍无则回退默认音色"""
        if not character:
            return base_voice
        if character in character_voices:
            return character_voices[character]
        for name, voice in character_voices.items():
            if name in character or character in name:
                return voice
        return base_voice

    # ── 时间轴分配 ──────────────────────────

    def _assign_offsets(self, voice_tracks: list[dict], duration: float):
        """把配音逐句首尾相接铺到时间轴上：每句起点 = 上一句起点 + 上一句时长 + LINE_GAP。

        新方案下镜头时长 = 配音时长 + LINE_GAP、成片时长 ≈ 配音总时长，
        因此顺序累加即可——既不重叠、也不会越界（混音时按成片时长截断尾部）。
        """
        offset = 0.0
        for t in voice_tracks:
            t["offset"] = round(offset, 3)
            offset += float(t["dur"]) + LINE_GAP

        need = offset - (LINE_GAP if voice_tracks else 0.0)
        if duration > 0 and need > duration + 0.05:
            logger.warning(
                f"[AudioAgent] 配音总长 {need:.1f}s 超过成片 {duration:.1f}s，"
                f"尾部台词会被截断（分镜时长应由配音决定）"
            )
