"""Riseholme-2021 -> ImageFolder 数据准备脚本。

将 Riseholme-2021(https://github.com/ctyeong/Riseholme-2021)整理为
train_numpy.py 可直接消费的标准目录:

    <output>/train|val|test/{Ripe,Unripe,Occluded,Anomalous}/*.png

划分规则(如实记录,不虚构):
- Normal 三类(Ripe/Unripe/Occluded)使用官方 Splits/Split{--split}-RUO-{train,val,test}.txt;
- Anomalous 官方设定为全部测试(one-class),但本项目为 4 类多分类,
  因此按 70/15/15 随机划分(固定种子),并在输出元信息中说明。
"""

import argparse
import json
import os
import random
import shutil

NORMAL_CLASSES = ["Ripe", "Unripe", "Occluded"]
ANOMALOUS_CLASSES = ["Anomalous"]


def parse_txt(path):
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def main():
    parser = argparse.ArgumentParser(description="整理 Riseholme-2021 为 ImageFolder 结构")
    parser.add_argument("--riseholme-dir", required=True, help="Riseholme-2021-main 根目录")
    parser.add_argument("--output-dir", required=True, help="输出目录(ImageFolder)")
    parser.add_argument("--split", type=int, default=1, choices=[1, 2, 3], help="使用官方第几个划分")
    parser.add_argument("--scenario", default="RUO", choices=["R", "U", "RU", "RUO"])
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    data_root = os.path.join(args.riseholme_dir, "Data")
    normal_root = os.path.join(data_root, "Normal")
    split_root = os.path.join(args.riseholme_dir, "Splits", f"Split{args.split}")
    os.makedirs(args.output_dir, exist_ok=True)

    stats = {}
    copied = 0

    # 1) Normal 三类:官方划分
    for cls in NORMAL_CLASSES:
        src_dir = os.path.join(normal_root, cls)
        counts = {}
        for phase in ("train", "val", "test"):
            txt = os.path.join(split_root, f"Split{args.split}-{args.scenario}-{phase.capitalize()}.txt")
            if not os.path.exists(txt):
                raise SystemExit(f"缺少划分文件: {txt}")
            names = [os.path.basename(p) for p in parse_txt(txt) if p.startswith(cls + "/")]
            dst_dir = os.path.join(args.output_dir, phase, cls)
            os.makedirs(dst_dir, exist_ok=True)
            for name in names:
                src = os.path.join(src_dir, name)
                if not os.path.exists(src):
                    raise SystemExit(f"图片缺失: {src}")
                shutil.copy2(src, os.path.join(dst_dir, name))
                copied += 1
            counts[phase] = len(names)
        stats[cls] = counts

    # 2) Anomalous:官方仅测试,本项目多分类需训练样本,按 70/15/15 自划分
    src_dir = os.path.join(data_root, "Anomalous")
    names = sorted(os.listdir(src_dir))
    random.shuffle(names)
    n = len(names)
    n_train = int(n * 0.70)
    n_val = int(n * 0.15)
    phases = {"train": names[:n_train], "val": names[n_train:n_train + n_val], "test": names[n_train + n_val:]}
    counts = {}
    for phase, files in phases.items():
        dst_dir = os.path.join(args.output_dir, phase, "Anomalous")
        os.makedirs(dst_dir, exist_ok=True)
        for name in files:
            shutil.copy2(os.path.join(src_dir, name), os.path.join(dst_dir, name))
            copied += 1
        counts[phase] = len(files)
    stats["Anomalous"] = counts

    meta = {
        "dataset": "Riseholme-2021",
        "source": "https://github.com/ctyeong/Riseholme-2021",
        "official_split": f"Split{args.split}",
        "scenario": args.scenario,
        "normal_splits": "official RUO split files",
        "anomalous_splits": "manual 70/15/15 (seed=%d), original dataset uses all Anomalous for test (one-class)" % args.seed,
        "classes": ["Ripe", "Unripe", "Occluded", "Anomalous"],
        "counts": stats,
        "total_images": copied,
        "image_format": "png",
    }
    with open(os.path.join(args.output_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"已复制 {copied} 张图片 -> {args.output_dir}")


if __name__ == "__main__":
    main()
