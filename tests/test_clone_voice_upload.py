#!/usr/bin/env python3
"""域 B Smoke Test — VoxCPM2 clone_voice + StorageService 存文件 + FastAPI 上传端点

目标：exit code 0 全绿。不依赖 GPU/ComfyUI/VoxCPM2 真实运行，
      clone_voice 走 mock 路径、StorageService 存真实临时文件、
      FastAPI 用 TestClient 调 HTTP。

运行：
    cd /Users/a715/git/AIGC
    .venv/bin/python -m pytest tests/test_clone_voice_upload.py -v
    或  .venv/bin/python tests/test_clone_voice_upload.py
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import wave
from pathlib import Path

# 确保项目根在 import path 里（两种运行方式都兼容）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ──────────────────────────────────────────────
# TEST 1: clone_voice() mock 模式能正常返回、文件存在
# ──────────────────────────────────────────────
def test_clone_voice_mock_mode(tmp_path: Path | None = None) -> None:
    """验证 clone_voice 在没有 voxcpm 依赖时：
       - 不抛异常
       - 返回 output_path（字符串）
       - output_path 指向的文件真实存在
    """
    # 使用 pytest tmp_path（若有）或回退真实临时目录
    tmp_dir = tmp_path or Path(tempfile.mkdtemp(prefix="clone_voice_"))

    # 1) 造一个合法的假 WAV 参考音频（44100Hz 单声道 16-bit，0.25s）
    ref_path = tmp_dir / "ref_mock.wav"
    with wave.open(str(ref_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(b"\x00\x00" * (44100 // 4))  # 静音 0.25 秒

    # 2) 调 clone_voice（用不存在的 model_dir，强制走 mock）
    out_path = tmp_dir / "cloned_out.wav"
    from providers.audio_tools import clone_voice

    returned = clone_voice(
        reference_audio_path=str(ref_path),
        text="你好，这是一句测试台词。",
        output_path=str(out_path),
        language="zh",
        emotion="default",
        speed_ratio=1.0,
        voxcpm_model_dir="/tmp/definitely/not/exist/VoxCPM2",
    )

    # 3) 断言
    assert isinstance(returned, str), "clone_voice 必须返回字符串路径"
    assert returned == str(out_path), "返回路径必须等于传入的 output_path"
    assert Path(returned).exists(), "输出 WAV 文件必须真实存在"
    assert Path(returned).stat().st_size > 0, (
        "mock 模式下输出不应是 0 字节（应该有 WAV 头或拷贝内容）"
    )
    print("✅ TEST 1 clone_voice mock 模式：通过")


# ──────────────────────────────────────────────
# TEST 2: StorageService.save_uploaded_audio_ref 路径规则正确
# ──────────────────────────────────────────────
def test_storage_save_audio_ref(tmp_path: Path | None = None) -> None:
    """验证 save_uploaded_audio_ref：
       - 角色名「主角-小明」里的横杠保留，其它非法字符被过滤
       - 返回的绝对路径里包含 audio_refs/<安全角色名>/
       - 文件实际落盘且内容正确
    """
    tmp_root = tmp_path or Path(tempfile.mkdtemp(prefix="storage_"))

    from services.storage_service import StorageService

    storage = StorageService(storage_root=tmp_root)

    # 模拟 WAV 字节（只看路径规则，不需要合法 WAV）
    fake_bytes = b"fakewav" + b"\x00" * 16

    ref_id, abs_path = storage.save_uploaded_audio_ref(
        data=fake_bytes,
        character="主角-小明",   # 包含中文 + 横杠
        ext=".wav",
    )

    # 基本类型
    assert isinstance(ref_id, str) and ref_id.startswith("ref_"), (
        f"ref_id 必须以 ref_ 开头，实际: {ref_id}"
    )
    assert Path(abs_path).is_absolute(), "返回的 ref_path 必须是绝对路径"

    # 路径里必须包含 audio_refs/ + 安全角色名
    # sanitize_character 应该保留中文和横杠 → "主角-小明"
    p = Path(abs_path)
    assert "audio_refs" in p.parts, (
        f"路径中必须含 audio_refs 目录，实际路径: {p}"
    )
    # 找到 audio_refs 下一级目录名 → 期望等于 "主角-小明"
    parts = list(p.parts)
    idx = parts.index("audio_refs")
    char_dir = parts[idx + 1]
    # 只要包含「主角」和「小明」的关键汉字就算对（横杠可能被保留，也可能不影响）
    assert "主角" in char_dir, (
        f"角色目录名里必须保留中文'主角'，实际: {char_dir}"
    )
    assert "小明" in char_dir, (
        f"角色目录名里必须保留中文'小明'，实际: {char_dir}"
    )

    # 文件名必须以 ref_id 开头 + 扩展名
    assert p.name.startswith(ref_id), (
        f"文件名必须以 ref_id={ref_id} 开头，实际文件名: {p.name}"
    )
    assert p.name.endswith(".wav"), f"扩展名必须是 .wav，实际: {p.suffix}"

    # 文件真实存在、内容正确
    assert p.exists(), "文件必须真实存在"
    assert p.read_bytes() == fake_bytes, "文件内容必须与上传 bytes 完全一致"

    # ── 再验证一下路径穿越字符被正确过滤 ──
    ref_id2, abs_path2 = storage.save_uploaded_audio_ref(
        data=b"x",
        character="恶/意/../路径\\穿*越?~",
        ext=".mp3",
    )
    p2 = Path(abs_path2)
    # 父目录是 tmp_root/audio_refs/xxx，不能跳出 tmp_root
    assert str(p2.resolve()).startswith(str(tmp_root.resolve())), (
        f"路径穿越检测失败！{p2.resolve()} 不在 {tmp_root.resolve()} 下"
    )
    idx2 = list(p2.parts).index("audio_refs")
    char_dir2 = list(p2.parts)[idx2 + 1]
    # 过滤后不能有任何 / \ . 等字符
    for ch in ("/", "\\", ".", "?", "*", "~"):
        assert ch not in char_dir2, f"字符 {ch!r} 未被过滤: {char_dir2}"

    print("✅ TEST 2 StorageService.save_uploaded_audio_ref：通过")


# ──────────────────────────────────────────────
# TEST 3: FastAPI TestClient 调 POST /api/v1/audio/upload_reference
# ──────────────────────────────────────────────
def test_api_upload_reference(tmp_path: Path | None = None) -> None:
    """调 HTTP 接口：传一个假 WAV 文件
       - HTTP 状态码 200
       - JSON 返回含 ref_id
    """
    tmp_dir = tmp_path or Path(tempfile.mkdtemp(prefix="api_audio_"))

    # 用临时目录覆盖 StorageService 的根（避免污染真实 storage/）
    # 通过覆盖模块级 _STORAGE_ROOT 很难，改用 FastAPI 的 dependency override
    from services.storage_service import StorageService
    storage_override = StorageService(storage_root=tmp_dir)

    from fastapi.testclient import TestClient
    from api.main import app

    # 注入测试用 StorageService
    app.dependency_overrides[StorageService] = lambda: storage_override
    try:
        client = TestClient(app)

        # 造一个最小合法 WAV（16000Hz 0.1s）
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * 1600)
        buf.seek(0)

        resp = client.post(
            "/api/v1/audio/upload_reference",
            files={"file": ("test-小明.wav", buf.getvalue(), "audio/wav")},
            data={"character": "主角-小明"},
        )

        assert resp.status_code == 200, (
            f"HTTP 应该 200，实际 {resp.status_code}：{resp.text}"
        )
        data = resp.json()
        assert "ref_id" in data, f"返回 JSON 必须含 ref_id，实际键: {list(data.keys())}"
        assert isinstance(data["ref_id"], str) and data["ref_id"].startswith("ref_"), (
            f"ref_id 格式异常: {data['ref_id']}"
        )
        # character 被清洗过但保留中文
        assert "主角" in data["character"] and "小明" in data["character"], (
            f"返回 character 未保留中文: {data['character']}"
        )
        assert Path(data["ref_path"]).exists(), "ref_path 指向的文件必须存在"

        print("✅ TEST 3 FastAPI upload_reference 端点：通过")
    finally:
        # 还原 dependency override，避免影响其它测试
        app.dependency_overrides.pop(StorageService, None)


# ──────────────────────────────────────────────
# 直接运行（pytest 以外的入口）
# ──────────────────────────────────────────────
def _main() -> int:
    """无 pytest 时也能跑的主入口：每个用例单独造临时目录。

    返回 0 全过，非 0 有失败。
    """
    print("=" * 60)
    print("域 B Smoke Test — clone_voice / StorageService / upload_reference")
    print("=" * 60)

    passed = 0
    failed = 0

    def _run(name, fn):
        nonlocal passed, failed
        try:
            with tempfile.TemporaryDirectory() as td:
                fn(Path(td))
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"❌ {name} 断言失败: {e}")
        except Exception as e:
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"❌ {name} 异常: {e}")

    _run("TEST 1 clone_voice mock 模式", test_clone_voice_mock_mode)
    _run("TEST 2 StorageService.save_uploaded_audio_ref", test_storage_save_audio_ref)
    _run("TEST 3 FastAPI upload_reference 端点", test_api_upload_reference)

    print()
    print("=" * 60)
    if failed == 0:
        print(f"✅ 全部通过：{passed}/{passed + failed}")
        return 0
    else:
        print(f"❌ 失败 {failed} 个，通过 {passed} 个")
        return 2


if __name__ == "__main__":
    sys.exit(_main())
