"""存储服务：统一管理 storage/ 下的文件落盘路径规则。

目前提供：
- save_uploaded_audio_ref(): 参考音频上传落盘（角色音色克隆时的参考文件）
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Tuple

# 复用 audio_tools 里定义的 ref_id 生成规则（一处定义全局一致）
from providers.audio_tools import _gen_ref_id

logger = logging.getLogger(__name__)

# storage 根目录：项目根/storage（相对项目根定位，避免 cwd 影响）
_PROJECT_ROOT = Path(__file__).parent.parent
_STORAGE_ROOT = _PROJECT_ROOT / "storage"

# 参考音频允许的扩展名（白名单，防路径/类型穿越）
_ALLOWED_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}

# 角色名字符过滤：只保留「中文 \u4e00-\u9fa5」「英文数字 \w」「横杠 -」
# 其它字符（/ \ . .. ~ * ? 等）一律替换为空，防止路径穿越
_CHARACTER_SAFE_RE = re.compile(r"[^\w\u4e00-\u9fa5-]")


class StorageService:
    """文件存储服务（FastAPI 可 Depends 注入，默认无参数构造即可）。

    路径规则统一在这里维护，避免各 router 自己拼路径。
    未来如果切到对象存储（S3/OSS），只需替换这一个类的实现。
    """

    def __init__(self, storage_root: str | Path | None = None):
        """初始化。

        Args:
            storage_root: 可选，自定义 storage 根目录（测试里常用临时目录覆盖）。
                          不传时默认为 <项目根>/storage。
        """
        self.storage_root = Path(storage_root) if storage_root else _STORAGE_ROOT

    # ───────── 工具方法 ──────────────────────────────────────

    @staticmethod
    def sanitize_character(character: str) -> str:
        """把角色名清理为安全目录名（保留中文/英数/下划线/横杠，其它删除）。

        - "/" "\\" ".." "~" 等危险字符一律去掉
        - 连续横杠/下划线合并
        - 首尾的 "-" "_" 去掉（避免产生 /_-xxx 这样的奇怪目录）
        """
        if not character:
            return "default"
        cleaned = _CHARACTER_SAFE_RE.sub("", character.strip())
        # 连续的 - _ 合并为一个
        cleaned = re.sub(r"[-_]{2,}", lambda m: m.group(0)[0], cleaned)
        cleaned = cleaned.strip("-_")
        return cleaned or "default"

    @staticmethod
    def normalize_extension(ext: str) -> str:
        """规范化扩展名：小写、补个点。

        例如 "WAV" → ".wav"、"mp3" → ".mp3"、".FLAC" → ".flac"。
        不在白名单内则回退 ".bin"（保留文件，前端能看到类型非法）。
        """
        if not ext:
            return ".bin"
        ext = ext.lower()
        if not ext.startswith("."):
            ext = "." + ext
        if ext not in _ALLOWED_AUDIO_EXTS:
            logger.warning(f"[Storage] 未知音频扩展名: {ext}，将以 .bin 保存")
            return ".bin"
        return ext

    # ───────── 参考音频上传 ──────────────────────────────────

    def save_uploaded_audio_ref(
        self,
        data: bytes,
        character: str,
        ext: str,
    ) -> Tuple[str, str]:
        """保存上传的「角色参考音频」。

        目录规则：
            {storage_root}/audio_refs/{sanitize_character(character)}/{ref_id}{ext}

        Args:
            data: 上传的文件字节
            character: 角色名（会做安全清洗）
            ext: 扩展名（会做规范化 + 白名单）

        Returns:
            (ref_id, absolute_file_path) — 两个都是字符串。
            - ref_id: 形如 ref_xxxxxxxx_171xxxxxxxxx，短且全局唯一
            - absolute_file_path: 磁盘上的绝对路径（写入完成后返回）
        """
        # 1) 清洗输入
        safe_char = self.sanitize_character(character)
        safe_ext = self.normalize_extension(ext)

        # 2) 生成 ref_id + 目标路径
        ref_id = _gen_ref_id()
        target_dir = self.storage_root / "audio_refs" / safe_char
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{ref_id}{safe_ext}"

        # 3) 原子落盘：先写 .tmp 再 rename，避免中途崩溃产生半残文件
        tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
        try:
            tmp_path.write_bytes(data)
            tmp_path.replace(target_path)
        except Exception as e:
            # 清理残余 tmp
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            logger.error(f"[Storage] 参考音频写入失败: {target_path}，err={e}")
            raise

        logger.info(
            f"[Storage] 参考音频已保存: char={safe_char} ref={ref_id} "
            f"size={len(data)}B → {target_path}"
        )
        return ref_id, str(target_path.resolve())
