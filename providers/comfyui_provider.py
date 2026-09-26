"""ComfyUI Provider - 通过本地 ComfyUI API 实现 SD/FLUX 图片生成

注意：视频生成不走本文件 —— 统一由 providers/ltx_mlx_provider.py 的
LTX-2.3 MLX 本地原生引擎负责（曾有两个 ComfyUI 视频 Provider 已删除：
ComfyLTXVideoProvider / ComfyMiniMaxH3VideoProvider）。
"""

import logging
import os
import shutil
import requests as _requests
from typing import Optional
from pathlib import Path

from providers.base import ImageProvider
from providers.comfyui.client import ComfyUIClient


# 代理豁免：避免被 Grammarly 7890 本地代理拦截
def _noget(url, **kw):
    kw["proxies"] = {"http": None, "https": None}
    return _requests.get(url, **kw)

logger = logging.getLogger(__name__)

# FLUX 与 SD1.5 的采样口径完全不同：FLUX 的 cfg 必须≈1.0（沿用 SD1.5 的 7.5 会严重过曝），
# dev 底模建议 20 步（schnell 才用 4 步）。这里给出 FLUX 的兜底默认；
# 调用方显式传入 steps/cfg 时以调用方为准（SD1.5 口径维持原值不变）。
FLUX_DEFAULT_STEPS = 20
FLUX_DEFAULT_CFG = 1.0

# ComfyUI 桌面版的图片输出目录（FLUX/SD 出图落盘处）。
# 本机多套 ComfyUI 安装时可通过环境变量 COMFY_OUTPUT_DIR 覆盖。
COMFY_OUTPUT_DIR = Path(os.environ.get(
    "COMFY_OUTPUT_DIR",
    "/Users/a715/git/ComfyUI/ComfyUI-Installs/ComfyUI/ComfyUI/output",
))
# AIGC 项目本地图片目录（后续 agent：LTX 图生视频 / ControlNet 参考图都从这里读）
LOCAL_IMAGE_DIR = Path(os.environ.get(
    "AIGC_LOCAL_IMAGE_DIR", "storage/output/images",
))


def _negative_of(data: dict) -> str:
    """读负向提示词，兼容 negative_prompt（通用键）与 sd_negative（image_agent 的键）。

    历史 bug：image_agent 只写 sd_negative，而 provider 只读 negative_prompt，
    于是风格负向词（写实/真人…）与 anatomy 负向词（畸形脸/多余手指…）
    从未真正进入 ComfyUI，CLIPTextEncode 的 negative 一直是空串。
    """
    return (data.get("negative_prompt") or data.get("sd_negative") or "").strip()


def _first_error(status: dict) -> str:
    """从 ComfyUI history 的 status 里抽出第一条执行错误的可读描述。

    status 形如 {"completed": False, "status_str": "error",
                 "messages": [["execution_start", {...}],
                              ["execution_error", {"node_type", "exception_message", ...}]]}
    """
    for m in (status.get("messages") or []):
        if m and m[0] == "execution_error":
            p = m[1] or {}
            msg = str(p.get("exception_message") or "").strip().replace("\n", " ")
            return f"{p.get('node_type')}: {msg}"
    return status.get("status_str") or "error"


def sync_image_to_local(img_info: dict) -> str:
    """把 ComfyUI 返回的图片记录同步落地成 AIGC 本地文件，返回本地绝对路径。

    背景：image_agent / character_agent 从 ComfyUI 拿到的只是
    {filename, subfolder, type, ...} 元信息，图片实体还留在 ComfyUI 的
    output 目录；而 video_agent(LTX 读图)、ControlNet 上传都需要本机文件。
    这里把文件从 ComfyUI output 复制到 storage/output/images 并返回新路径，
    找不到源文件时返回空串（由调用方决定是否跳过/报错）。
    """
    fname = (img_info or {}).get("filename") or ""
    if not fname:
        return ""
    LOCAL_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    dest = LOCAL_IMAGE_DIR / Path(fname).name
    # ComfyUI 可能把文件放在子目录（subfolder），拼出源文件完整路径
    subfolder = (img_info or {}).get("subfolder") or ""
    src = (COMFY_OUTPUT_DIR / subfolder / fname) if subfolder else (COMFY_OUTPUT_DIR / fname)
    if src.exists():
        # 每次都覆盖：ComfyUI 的输出序号会随重启/清空而重置，可能再次出现与旧会话
        # 同名的文件（如 flux_output_00013_.png）。若沿用「已存在就复用」会拿到过期
        # 旧图，导致质检（拿到旧图误判）和后续图生视频全部用错图片。
        shutil.copy(src, dest)
        logger.info(f"[comfyui] 图片已落地本地: {src} -> {dest}")
        return str(dest)
    if dest.exists():  # 源文件已不在（被清理），退回本地已缓存的文件
        return str(dest)
    logger.warning(f"[comfyui] 图片源文件不存在: {src}")
    return ""


class ComfySDImageProvider(ImageProvider):
    """ComfyUI + SD 图片生成"""

    def __init__(self, client: Optional[ComfyUIClient] = None):
        from config.settings import settings
        cfg = settings.comfyui
        self.client = client or ComfyUIClient(
            server_addr=cfg.server_addr,
            server_port=cfg.server_port,
        )
        # 出图模型按「当前激活风格」选择，未配置时回退到 config.yaml 的 comfyui.ckpt_name
        from config.style_resolver import image_ckpt_for_style, image_model_type_for_style
        self._ckpt = image_ckpt_for_style(
            default=getattr(settings.comfyui, "ckpt_name", "v1-5-pruned-emaonly.safetensors")
        )
        # 引擎类型：sd15 | flux（默认 sd15，保持向后兼容）
        self._model_type = image_model_type_for_style(
            default=getattr(settings.comfyui, "model_type", "sd15")
        ) or "sd15"

    # ── FLUX 工作流构建（按 model_type 复用）─────────────
    def _build_txt2img(self, prompt, negative, width, height, seed, steps, cfg):
        """按引擎类型分发到 SD1.5 或 FLUX 工作流"""
        if self._model_type == "flux":
            return ComfyUIClient.build_flux_txt2img_workflow(
                unet_name=self._ckpt,
                prompt=prompt,
                width=width,
                height=height,
                seed=seed,
                steps=steps,
                cfg=cfg,
                loras=self._style_loras(),
            )
        return ComfyUIClient.build_txt2img_workflow(
            ckpt_name=self._ckpt,
            prompt=prompt,
            negative_prompt=negative,
            width=width,
            height=height,
            seed=seed,
            steps=steps,
            cfg=cfg,
            loras=self._style_loras(),
        )

    def _style_loras(self) -> list:
        """当前风格要叠加的 LoRA（SD1.5 基座；未配置则返回空列表）。"""
        from config.style_resolver import loras_for_style
        return loras_for_style()

    def _sampling(self, steps, cfg, sd_steps: int, sd_cfg: float = 7.5):
        """按引擎口径兜底采样参数，返回 (steps, cfg)。

        FLUX 用 FLUX_DEFAULT_STEPS/FLUX_DEFAULT_CFG；SD1.5 用调用方给定的口径。
        显式传入的 steps/cfg 永远优先（分镜里可单独覆盖）。
        """
        if self._model_type == "flux":
            return (int(steps) if steps else FLUX_DEFAULT_STEPS,
                    float(cfg) if cfg else FLUX_DEFAULT_CFG)
        return (int(steps) if steps else sd_steps,
                float(cfg) if cfg else sd_cfg)

    # ── IP-Adapter 角色一致性（SD1.5 专属）─────────────────
    def _ipadapter_kind(self) -> Optional[str]:
        """当前该走哪套 IP-Adapter 模板；未启用或引擎不匹配时返回 None。

        开关与模板名都来自 config.yaml 的 comfyui.ipadapter（风格可按同名段覆盖）。
        IP-Adapter 权重是 SD1.5 基座，FLUX 引擎下直接跳过（同 ControlNet）。
        """
        if self._model_type == "flux":
            return None
        from config.style_resolver import ipadapter_for_style
        cfg = ipadapter_for_style()
        if not cfg.get("enabled"):
            return None
        kind = str(cfg.get("kind") or "ipadapter")
        return kind if kind in ("ipadapter", "ipadapter_faceid") else "ipadapter"

    def _build_ipadapter(self, kind, prompt, negative, ref_image, width, height,
                         seed, steps, cfg, batch_size=1):
        """构建 IP-Adapter 工作流：preset/权重统一交给 workflow_templates 从配置取。"""
        from providers.comfyui.workflow_templates import api_workflow
        return api_workflow(
            kind,
            ckpt_name=self._ckpt,
            prompt=prompt,
            negative_prompt=negative,
            ref_image=ref_image,
            width=width,
            height=height,
            seed=seed,
            steps=steps,
            cfg=cfg,
            batch_size=batch_size,
        )

    @staticmethod
    def _resolve_local_ref(ref: str) -> Optional[str]:
        """把参考图解析成本机可上传的文件路径，找不到返回 None。

        character_agent 存的是 ComfyUI 输出文件名（如 xxx_00001_.png），既不是
        本机相对路径、也不在 ComfyUI 的 input/ 里；按「本机路径 → AIGC 本地图库
        → ComfyUI output 目录」依次找，找到才能上传给工作流的 LoadImage。
        """
        if not ref:
            return None
        if os.path.isfile(ref):
            return ref
        name = Path(ref).name
        for cand in (LOCAL_IMAGE_DIR / name, COMFY_OUTPUT_DIR / name):
            if cand.is_file():
                return str(cand)
        return None

    async def _remote_ref(self, ref: str) -> str:
        """上传参考图到 ComfyUI input/，返回工作流可引用的名字；失败返回空串。

        LoadImage 引用子目录里的图必须写成 `子目录/文件名`，只回传 basename 会
        被 ComfyUI 判成 "Invalid image file"。
        """
        local = self._resolve_local_ref(ref)
        if not local:
            return ref  # 找不到本机实体，按「已在 ComfyUI 内的文件名」原样透传
        try:
            result = await self.client.upload_image(local, subfolder="refs")
            sub = result.get("subfolder") or ""
            name = result.get("name") or Path(local).name
            return f"{sub}/{name}" if sub else name
        except Exception as e:
            logger.warning(f"[SD] 上传参考图失败 {local}: {e}")
            return ""

    async def generate(
        self,
        prompt: str,
        ref_image: Optional[str] = None,
        seed: Optional[int] = None,
        **kwargs,
    ) -> list[dict]:
        """单张图片生成，支持 IP-Adapter + ControlNet"""
        ctrl_type = kwargs.get("controlnet_type") or kwargs.get("ctrl_type") or None
        ctrl_image = kwargs.get("controlnet_image") or ref_image or None
        # ControlNet 工作流基于 SD1.5（CheckpointLoader + SD1.5 controlnet），
        # FLUX 走 GGUF UNET，接不上；FLUX 下忽略 ControlNet 走纯文生图。
        if self._model_type == "flux":
            ctrl_type = ctrl_image = None
        steps, cfg = self._sampling(kwargs.get("steps"), kwargs.get("cfg"), sd_steps=12)

        # 优先级：IP-Adapter > ControlNet > 文生图。两者都是独立工作流（各自带
        # CheckpointLoader），当前 builder 无法叠加，故 IP-Adapter 启用时让位。
        wf = None
        ref = kwargs.get("ipadapter_ref") or ref_image or ctrl_image
        ip_kind = self._ipadapter_kind() if ref else None
        if ip_kind:
            remote_ref = await self._remote_ref(ref)
            if remote_ref:
                wf = self._build_ipadapter(
                    kind=ip_kind,
                    prompt=prompt,
                    negative=_negative_of(kwargs),
                    ref_image=remote_ref,
                    width=kwargs.get("width", 512),
                    height=kwargs.get("height", 512),
                    seed=seed or 42,
                    steps=steps,
                    cfg=cfg,
                )
            else:
                logger.warning("[SD] IP-Adapter 参考图不可用，回退到 ControlNet/文生图")

        if wf is None and ctrl_type and ctrl_image:
            wf = ComfyUIClient.build_controlnet_workflow(
                ckpt_name=self._ckpt,
                prompt=prompt,
                negative_prompt=_negative_of(kwargs),
                controlnet_name=ctrl_type,
                controlnet_image=ctrl_image,
                width=kwargs.get("width", 512),
                height=kwargs.get("height", 512),
                seed=seed or 42,
                steps=steps,
                cfg=cfg,
                controlnet_strength=kwargs.get("controlnet_strength", 0.75),
            )
        if wf is None:
            wf = self._build_txt2img(
                prompt=prompt,
                negative=_negative_of(kwargs),
                width=kwargs.get("width", 512),
                height=kwargs.get("height", 512),
                seed=seed or 42,
                steps=steps,
                cfg=cfg,
            )
        resp = await self.client.queue_prompt(wf)
        prompt_id = resp["prompt_id"]
        result = await self.client.wait_for_completion(prompt_id)
        if not result["success"]:
            raise RuntimeError(f"ComfyUI 生成失败: {result.get('error', '?')}")
        # 提取所有输出图片的文件信息
        images = []
        for node_out in result.get("outputs", {}).values():
            for img in node_out.get("images", []):
                images.append({
                    "filename": img["filename"],
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output"),
                    "prompt_id": prompt_id,
                })
        return images

    async def batch_generate(self, shots: list[dict]) -> list[dict]:
        """批量提交 → 后台轮询收集（不阻塞主流程）"""
        import asyncio, time
        import requests

        # 1. 先去重并上传参考图到 ComfyUI input/
        # 参考图来源有两类：IP-Adapter 的 ipadapter_ref、ControlNet 的 controlnet_image/ref_image；
        # 值可能是本机路径，也可能是 ComfyUI 输出文件名，统一交给 _remote_ref 解析+上传。
        uploaded_refs = {}  # 原样 ref -> filename_in_comfyui
        for shot in shots:
            ref = shot.get("ipadapter_ref") or shot.get("controlnet_image") or shot.get("ref_image") or None
            if not ref or ref in uploaded_refs:
                continue
            remote_name = await self._remote_ref(ref)
            if not remote_name:
                logger.warning(f"[SD] 上传参考图失败 {ref}，该镜头跳过 IP-Adapter/ControlNet")
                shot["ipadapter_ref"] = None
                shot["controlnet_type"] = None
                shot["ref_image"] = None
                continue
            uploaded_refs[ref] = remote_name
            # 同一个文件可能被不同键引用（ipadapter_ref / controlnet_image / ref_image），都登记一份
            for key in ("ipadapter_ref", "controlnet_image", "ref_image"):
                if shot.get(key):
                    uploaded_refs[shot[key]] = remote_name
            logger.info(f"[SD] 上传参考图: {ref} -> {remote_name}")

        # 2. 全部提交，不等待
        submitted = []
        for shot in shots:
            prompt = shot.get("sd_prompt", "")
            seed = max(0, shot.get("seed", 0) or 0)

            ctrl_type = shot.get("controlnet_type") or None
            ctrl_image = shot.get("controlnet_image") or shot.get("ref_image") or None
            # 同 generate()：FLUX 接不上 SD1.5 的 ControlNet / IP-Adapter 工作流，忽略之。
            if self._model_type == "flux":
                ctrl_type = ctrl_image = None
            steps, cfg = self._sampling(shot.get("steps"), shot.get("cfg"), sd_steps=16)

            # 优先级：IP-Adapter > ControlNet > 文生图（同 generate()）
            wf = None
            ip_kind = self._ipadapter_kind()
            ref = shot.get("ipadapter_ref") or ctrl_image
            if ip_kind and ref:
                # 预上传段已传过就直接复用远端名，否则现传
                remote_ref = uploaded_refs.get(ref) or await self._remote_ref(ref)
                if remote_ref:
                    wf = self._build_ipadapter(
                        kind=ip_kind,
                        prompt=prompt,
                        negative=_negative_of(shot),
                        ref_image=remote_ref,
                        width=int(shot.get("width", 768)),
                        height=int(shot.get("height", 768)),
                        seed=seed,
                        steps=steps,
                        cfg=cfg,
                    )
                else:
                    logger.warning("[SD] IP-Adapter 参考图不可用，回退到 ControlNet/文生图")

            if wf is None and ctrl_type and ctrl_image:
                remote_name = uploaded_refs.get(ctrl_image, os.path.basename(ctrl_image))
                wf = ComfyUIClient.build_controlnet_workflow(
                    ckpt_name=self._ckpt,
                    prompt=prompt,
                    negative_prompt=_negative_of(shot),
                    controlnet_name=ctrl_type,
                    controlnet_image=remote_name,
                    width=int(shot.get("width", 768)),
                    height=int(shot.get("height", 768)),
                    seed=seed,
                    steps=steps,
                    cfg=cfg,
                    controlnet_strength=float(shot.get("controlnet_strength", 0.8)),
                )
            if wf is None:
                wf = self._build_txt2img(
                    prompt=prompt,
                    negative=_negative_of(shot),
                    width=int(shot.get("width", 768)),
                    height=int(shot.get("height", 768)),
                    seed=seed,
                    steps=steps,
                    cfg=cfg,
                )
            resp = await self.client.queue_prompt(wf)
            submitted.append({
                "prompt_id": resp["prompt_id"],
                "shot_id": shot.get("shot_id", ""),
                # 保留原始 shot：轮询阶段出错时可摘掉参考图改走文生图重投
                "shot": shot,
            })

        if not submitted:
            return []

        # 2. 后台轮询完成情况
        # 本机（Apple M4）FLUX 单张生成实测约 15-25 分钟，远超普通 SD 的 15-60 秒。
        # 若按旧公式 max(180, n*60)，一集 8 个镜头只有 480s，远不够单张生成即会超时丢图。
        # 故按「单张 ≥20 分钟」给足预算：timeout = max(1200, n * 1200)。
        per_img_sec = int(os.environ.get("AIGC_IMG_TIMEOUT_PER_SEC", "1200"))
        timeout = max(1200, len(submitted) * per_img_sec)
        pending_ids = {s["prompt_id"]: s for s in submitted}
        history_url = f"{self.client.base_url}/history"
        start = time.time()
        while pending_ids and (time.time() - start) < timeout:
            # 用 asyncio.sleep 而非 time.sleep：避免阻塞后端事件循环导致 HTTP 无响应
            await asyncio.sleep(3)
            try:
                hist = (await asyncio.to_thread(_noget, history_url, timeout=5)).json()
            except Exception:
                continue
            for pid in list(pending_ids.keys()):
                entry = hist.get(pid)
                if not entry:
                    continue
                st = entry.get("status", {}) or {}
                if st.get("completed", False):
                    s = pending_ids.pop(pid)
                    logger.info(f"[SD] 完成: shot={s['shot_id']}")
                    continue
                # status_str == "error" 的条目 completed 恒为 False，若不单独处理会
                # 一直挂在 pending 里空转到 timeout（19 镜 ≈ 6.3h）。典型场景：
                # IPAdapterFaceID 对二次元人脸抛 "InsightFace: No face detected."。
                if st.get("status_str") != "error":
                    continue
                s = pending_ids.pop(pid)
                err = _first_error(st)
                shot = s.get("shot") or {}
                if s.get("retried"):
                    logger.error(f"[SD] 放弃: shot={s['shot_id']} 重投后仍失败 ({err})")
                    continue
                # 摘掉参考图（多半是人脸检测/IP-Adapter 工作流的问题），改走纯文生图重投一次
                logger.warning(f"[SD] 失败重投: shot={s['shot_id']} {err} -> 去掉参考图改文生图")
                shot["ipadapter_ref"] = None
                shot["controlnet_type"] = None
                shot["controlnet_image"] = None
                shot["ref_image"] = None
                steps, cfg = self._sampling(shot.get("steps"), shot.get("cfg"), sd_steps=16)
                try:
                    resp = await self.client.queue_prompt(self._build_txt2img(
                        prompt=shot.get("sd_prompt", ""),
                        negative=_negative_of(shot),
                        width=int(shot.get("width", 768)),
                        height=int(shot.get("height", 768)),
                        seed=max(0, shot.get("seed", 0) or 0),
                        steps=steps,
                        cfg=cfg,
                    ))
                except Exception as e:
                    logger.error(f"[SD] 放弃: shot={s['shot_id']} 重投失败 {e}")
                    continue
                s["retried"] = True
                s["prompt_id"] = resp["prompt_id"]
                pending_ids[resp["prompt_id"]] = s

        # 3. 收集结果
        flat = []
        for s in submitted:
            pid = s["prompt_id"]
            try:
                hist = (await asyncio.to_thread(_noget, history_url, timeout=5)).json()
                if pid in hist:
                    outputs = hist[pid]["outputs"]
                    for node_out in outputs.values():
                        for img in node_out.get("images", []):
                            flat.append({
                                "filename": img["filename"],
                                "subfolder": img.get("subfolder", ""),
                                "type": img.get("type", "output"),
                                "prompt_id": pid,
                                "shot_id": s["shot_id"],
                            })
            except Exception as e:
                logger.warning(f"获取结果失败: {e}")

        return flat
