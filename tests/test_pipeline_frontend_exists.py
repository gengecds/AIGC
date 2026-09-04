#!/usr/bin/env python3
"""PipelineView.vue 静态存在性 Smoke Test（域 D 独立模块）

目的：不启动 vite / 浏览器，纯靠文件内容 + 正则断言验证
前端 5 步向导组件「结构完整」。
验证 4 个关键字（按任务要求）：
  1. EventSource       → 证明组件确实使用 SSE 连接订阅后端进度
  2. step === 0        → 证明有 Step 0（剧本审查）的分支渲染
  3. step === 4        → 证明有 Step 4（最终发布）的分支渲染
  4. <video controls   → 证明 Step 3 卡片里包含 HTML5 视频预览标签

额外（非阻塞）断言：
  - 文件存在、可读、> 10KB（合理大小，不是空壳）
  - 含 <template> / <script setup / <style scoped 三段式 SFC 结构
"""

import os
import re
import sys

# ── 路径定位：项目根 + PipelineView.vue 实际位置 ────────────────
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TEST_DIR)

# 按项目实际结构：frontend/src/views/PipelineView.vue
TARGET_VUE = os.path.join(
    PROJECT_ROOT, "frontend", "src", "views", "PipelineView.vue"
)

# ── 必需的 4 个断言规则：(显示名, 正则 pattern, 失败提示) ────────
REQUIRED_CHECKS = [
    (
        "EventSource (SSE 订阅)",
        re.compile(r"\bnew\s+EventSource\b"),
        "组件未实例化 EventSource，无法订阅后端 SSE 进度",
    ),
    (
        "Step 0 分支 (step === 0)",
        re.compile(r"step\s*===\s*0"),
        "缺少 Step 0 (剧本审查) 的 v-if 渲染分支",
    ),
    (
        "Step 4 分支 (step === 4)",
        re.compile(r"step\s*===\s*4"),
        "缺少 Step 4 (最终发布) 的 v-if 渲染分支",
    ),
    (
        "<video controls 标签",
        re.compile(r"<video\s[^>]*controls", re.IGNORECASE),
        "Step 3 缺少 <video controls> 视频预览标签",
    ),
]

# ── 增强（非阻塞）断言 ──────────────────────────────────────────
EXTRA_CHECKS = [
    (
        "SFC <template> 块",
        re.compile(r"<template[^>]*>"),
        "缺少 <template> 起始标签",
    ),
    (
        "SFC <script setup 块",
        re.compile(r"<script[^>]*setup[^>]*>"),
        "缺少 <script setup> 起始标签（Vue 3 组合式 API）",
    ),
    (
        "SFC <style scoped 块",
        re.compile(r"<style[^>]*scoped[^>]*>"),
        "缺少 <style scoped> 起始标签（样式隔离）",
    ),
    (
        "5 步名称：剧本/分镜/角色/视频/最终",
        re.compile(r"剧本.*分镜.*角色.*视频.*最终", re.DOTALL),
        "5 步卡片标签可能不完整（剧本审查/分镜审查/角色图/视频预览/最终发布）",
    ),
]


def main() -> int:
    print("=" * 60)
    print("PipelineView.vue 静态存在性 Smoke Test")
    print("=" * 60)

    # ── 1. 文件存在 + 可读 ─────────────────────────────────────
    if not os.path.isfile(TARGET_VUE):
        print(f"❌ 目标文件不存在: {TARGET_VUE}")
        return 1
    print(f"✅ 目标文件存在: {TARGET_VUE}")

    try:
        with open(TARGET_VUE, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        print(f"❌ 文件无法以 UTF-8 读取: {TARGET_VUE}")
        return 1
    except Exception as e:
        print(f"❌ 读取文件异常: {type(e).__name__}: {e}")
        return 1

    # ── 2. 文件大小合理性（非空壳） ─────────────────────────────
    size_kb = len(content.encode("utf-8")) / 1024.0
    print(f"✅ 文件内容读取成功，大小 {size_kb:.1f} KB")
    if size_kb < 5:
        print(f"⚠️  文件过小 (< 5 KB)，可能是空壳组件（非阻塞）")

    # ── 3. 必需断言（失败则 exit 1） ────────────────────────────
    print("\n── 必需断言（4 项）──")
    all_required_ok = True
    for name, pattern, fail_msg in REQUIRED_CHECKS:
        if pattern.search(content):
            print(f"✅  {name}")
        else:
            print(f"❌  {name} —— {fail_msg}")
            all_required_ok = False

    if not all_required_ok:
        print("\n❌ 必需断言未全部通过，测试失败")
        return 1

    # ── 4. 增强断言（仅提示，不影响 exit code） ─────────────────
    print("\n── 增强断言（非阻塞）──")
    for name, pattern, fail_msg in EXTRA_CHECKS:
        if pattern.search(content):
            print(f"✅  {name}")
        else:
            print(f"ℹ️  {name} 缺失（不影响 exit code）—— {fail_msg}")

    # ── 5. 关键行数统计（调试信息） ─────────────────────────────
    template_lines = content.count("\n") + 1
    print(f"\n📊 组件总行数: {template_lines} 行")

    print("\n🏁 所有必需 Smoke Test 通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
