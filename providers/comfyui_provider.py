"""ComfyUI Provider - 通过本地 ComfyUI API 实现 SD/FLUX 图片生成

注意：视频生成不走本文件 —— 统一由 providers/ltx_mlx_provider.py 的
LTX-2.3 MLX 本地原生引擎负责（曾有两个 ComfyUI 视频 Provider 已删除：
ComfyLTXVideoProvider / ComfyMiniMaxH3VideoProvider）。
"""

import logging
import os
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
                prompt=prompt,
                width=width,
                height=height,
                seed=seed,
                steps=steps,
                cfg=cfg,
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
        )

    async def generate(
        self,
        prompt: str,
        ref_image: Optional[str] = None,
        seed: Optional[int] = None,
        **kwargs,
    ) -> list[dict]:
        """单张图片生成，支持 ControlNet + IP-Adapter"""
        ctrl_type = kwargs.get("controlnet_type") or kwargs.get("ctrl_type") or None
        ctrl_image = kwargs.get("controlnet_image") or ref_image or None

        if ctrl_type and ctrl_image:
            wf = ComfyUIClient.build_controlnet_workflow(
                ckpt_name=self._ckpt,
                prompt=prompt,
                negative_prompt=kwargs.get("negative_prompt", ""),
                controlnet_name=ctrl_type,
                controlnet_image=ctrl_image,
                width=kwargs.get("width", 512),
                height=kwargs.get("height", 512),
                seed=seed or 42,
                steps=kwargs.get("steps", 12),
                cfg=kwargs.get("cfg", 7.5),
                controlnet_strength=kwargs.get("controlnet_strength", 0.75),
            )
        else:
            wf = self._build_txt2img(
                prompt=prompt,
                negative=kwargs.get("negative_prompt", ""),
                width=kwargs.get("width", 512),
                height=kwargs.get("height", 512),
                seed=seed or 42,
                steps=kwargs.get("steps", 12),
                cfg=kwargs.get("cfg", 7.5),
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

        # 1. 先去重并上传 ref_image 到 ComfyUI input/
        uploaded_refs = {}  # local_path -> filename_in_comfyui
        for shot in shots:
            ctrl_image = shot.get("controlnet_image") or shot.get("ref_image") or None
            if ctrl_image and ctrl_image not in uploaded_refs:
                try:
                    result = await self.client.upload_image(ctrl_image, subfolder="refs")
                    remote_name = result.get("name", os.path.basename(ctrl_image))
                    uploaded_refs[ctrl_image] = remote_name
                    logger.info(f"[SD] 上传参考图: {ctrl_image} -> {remote_name}")
                except Exception as e:
                    logger.warning(f"[SD] 上传参考图失败 {ctrl_image}: {e}")
                    # 上传失败就跳过 ControlNet
                    shot["controlnet_type"] = None
                    shot["ref_image"] = None

        # 2. 全部提交，不等待
        submitted = []
        for shot in shots:
            prompt = shot.get("sd_prompt", "")
            seed = max(0, shot.get("seed", 0) or 0)

            ctrl_type = shot.get("controlnet_type") or None
            ctrl_image = shot.get("controlnet_image") or shot.get("ref_image") or None

            if ctrl_type and ctrl_image:
                remote_name = uploaded_refs.get(ctrl_image, os.path.basename(ctrl_image))
                wf = ComfyUIClient.build_controlnet_workflow(
                    ckpt_name=self._ckpt,
                    prompt=prompt,
                    negative_prompt=shot.get("negative_prompt", ""),
                    controlnet_name=ctrl_type,
                    controlnet_image=remote_name,
                    width=int(shot.get("width", 768)),
                    height=int(shot.get("height", 768)),
                    seed=seed,
                    steps=int(shot.get("steps", 16)),
                    cfg=float(shot.get("cfg", 7.5)),
                    controlnet_strength=float(shot.get("controlnet_strength", 0.8)),
                )
            else:
                wf = self._build_txt2img(
                    prompt=prompt,
                    negative=shot.get("negative_prompt", ""),
                    width=int(shot.get("width", 768)),
                    height=int(shot.get("height", 768)),
                    seed=seed,
                    steps=int(shot.get("steps", 16)),
                    cfg=float(shot.get("cfg", 7.5)),
                )
            resp = await self.client.queue_prompt(wf)
            submitted.append({
                "prompt_id": resp["prompt_id"],
                "shot_id": shot.get("shot_id", ""),
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
                if pid in hist and hist[pid].get("status", {}).get("completed", False):
                    s = pending_ids.pop(pid)
                    logger.info(f"[SD] 完成: shot={s['shot_id']}")

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
