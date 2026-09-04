"""只读：打印每个风格绑定的模型清单 + 本地安装状态。"""
import sys
import yaml

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from download_models import build_installed_index, clean_name

cfg = yaml.safe_load(open("/Users/a715/git/AIGC/config/config.yaml")) or {}
styles = cfg.get("styles", {})
idx = build_installed_index()


def stat(name):
    if not name or name == "-":
        return "-"
    c = clean_name(name)
    for t, bucket in idx.items():
        if c in bucket:
            return "本地(需对齐)" if bucket[c].name != name else "✓已装"
    return "❌缺失"


print(f'{"风格":<8} | {"底模 image_ckpt":<30} | {"视频 video_model":<26} | 底模状态')
print("-" * 100)
for sname, scfg in styles.items():
    if not isinstance(scfg, dict):
        continue
    ck = scfg.get("image_ckpt") or "-"
    vd = scfg.get("video_model") or "-"
    print(f"{sname:<8} | {ck:<30} | {vd:<26} | {stat(ck)}")

print("\n--- 风格 LoRA / 推荐模型 ---")
for sname, scfg in styles.items():
    if not isinstance(scfg, dict):
        continue
    recs = scfg.get("recommended_models", [])
    if recs:
        missing = [m for m in recs if stat(m) == "❌缺失"]
        print(f"{sname}: {[stat(m) + ' ' + m if stat(m)!= '✓已装' else '' for m in recs]}")
        if missing:
            print(f"   → 待手动/下载: {missing}")
