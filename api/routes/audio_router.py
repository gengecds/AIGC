"""音频域路由 — 参考音频上传、音色克隆合成等接口

与其它域完全独立，不依赖 pipeline/agents。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from providers.audio_tools import clone_voice
from services.storage_service import StorageService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/audio", tags=["音频"])

# 参考音频允许的 content_type 前缀
_AUDIO_CONTENT_TYPE_PREFIX = "audio/"

# 允许的文件扩展名（content-type 不可靠时的双重校验）
_ALLOWED_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}

# 单文件上限 20MB（VoxCPM2 参考音频 <30s 就够，一般 500KB~3MB）
_MAX_FILE_BYTES = 20 * 1024 * 1024


def _guess_ext(filename: str, content_type: str) -> str:
    """从文件名或 content-type 猜测扩展名，优先文件名后缀。

    返回值带点，例如 ".wav"。拿不到就回 ".bin"，由 StorageService 再判一次。
    """
    # 1) 文件名后缀
    if filename:
        ext = Path(filename).suffix.lower()
        if ext in _ALLOWED_EXTS:
            return ext
    # 2) content-type 兜底
    if content_type:
        ct = content_type.lower()
        mapping = {
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/wave": ".wav",
            "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3",
            "audio/mp4": ".m4a",
            "audio/x-m4a": ".m4a",
            "audio/aac": ".m4a",
            "audio/flac": ".flac",
            "audio/x-flac": ".flac",
            "audio/ogg": ".ogg",
            "audio/vorbis": ".ogg",
        }
        if ct in mapping:
            return mapping[ct]
    # 3) 实在拿不到 → 看 StorageService 里的白名单决定
    return (Path(filename).suffix.lower()) if filename else ".bin"


@router.post("/upload_reference")
async def upload_reference_audio(
    file: UploadFile = File(..., description="角色参考音频（建议 <30s）"),
    character: str = Form("default", description="角色名，用于分目录保存"),
    storage: StorageService = Depends(),
) -> dict:
    """上传角色参考音频，存到 storage/audio_refs/<character>/ 目录。

    校验:
      - content_type 必须是 audio/*，或文件名后缀属于音频白名单
      - 文件大小不得超过 20MB（否则 HTTP 413）

    返回:
        {
          "ref_id": "ref_xxxxxxxx_171xxxxxxxxx",
          "character": "清洗后的安全角色名",
          "ref_path": "磁盘绝对路径",
          "duration_seconds": 0.0   # 后续用 ffprobe/mutagen 补
        }
    """
    # ── 1. 类型校验（content-type + 扩展名双保险）
    filename = file.filename or ""
    content_type = (file.content_type or "").lower()

    ext_ok = bool(filename) and Path(filename).suffix.lower() in _ALLOWED_EXTS
    ct_ok = content_type.startswith(_AUDIO_CONTENT_TYPE_PREFIX)
    if not (ext_ok or ct_ok):
        raise HTTPException(
            status_code=400,
            detail=(
                "不支持的文件类型：仅支持 wav/mp3/m4a/flac/ogg 音频，"
                f"当前 content-type={content_type!r} filename={filename!r}"
            ),
        )

    # ── 2. 大小校验（> 20MB → 413）
    # UploadFile 默认是 SpooledTemporaryFile，我们先按块读出，途中判断大小
    chunks: list[bytes] = []
    total = 0
    # FastAPI UploadFile 读取块大小 64KB 足够
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"参考音频过大：超过 20MB（当前已读 {total/1024/1024:.1f}MB）",
            )
        chunks.append(chunk)
    file_bytes = b"".join(chunks)

    # 空文件也拒绝（更友好）
    if total == 0:
        raise HTTPException(status_code=400, detail="上传文件为空")

    # ── 3. 确定扩展名（文件后缀优先，content-type 兜底）
    ext = _guess_ext(filename, content_type)

    # ── 4. 交给 StorageService 落盘
    ref_id, abs_path = storage.save_uploaded_audio_ref(file_bytes, character, ext)

    safe_char = StorageService.sanitize_character(character)

    logger.info(
        f"[Audio] 上传参考音频成功：ref_id={ref_id} character={safe_char} "
        f"size={total}B path={abs_path}"
    )

    return {
        "ref_id": ref_id,
        "character": safe_char,
        "ref_path": abs_path,
        "duration_seconds": 0.0,  # TODO: 后续接 mutagen / ffprobe 算时长
    }
