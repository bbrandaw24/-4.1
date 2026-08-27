"""纯 NumPy 草莓图像分类训练管线(无 PyTorch 依赖)。

技术路线说明:
    本环境 torch 与 numpy 2 不兼容且 ImageNet 权重下载不可用,故改为
    纯 NumPy + Pillow 实现轻量 MLP 分类器。Riseholme-2021 原始图片仅
    约 60x60 像素、物体居中,MLP 在此规模下足够完成分类任务。

数据要求(与之前契约一致的 ImageFolder 结构):
    <data-dir>/train/<class>/*.png|jpg
    <data-dir>/val/<class>/*.png|jpg
    <data-dir>/test/<class>/*.png|jpg

用法:
    python prepare_riseholme.py --riseholme-dir ../datasets/train/Riseholme-2021-main \
        --output-dir ../datasets/riseholme-classification
    python train_numpy.py --data-dir ../datasets/riseholme-classification \
        --output models/riseholme_mlp_v1.npz --model-version riseholme-2021-mlp-v1

输出:
    --output      权重(.npz,含参数与元信息)
    --labels-out  类别映射 labels.json(推理服务据此加载)
    --report-out  训练报告 training_report.json(真实指标,不虚构)

诚实约束:
    每类训练图不足 50 张(或验证不足 10 张)时中止,不生成伪造权重;
    类别不平衡时启用频率倒数加权交叉熵,并在报告中记录。
"""

import argparse
import json
import os
import random
import time

import numpy as np
from PIL import Image

INPUT_SIZE = 48          # 输入边长(原图 60x60 -> 48x48,保留关键结构且控制计算量)
INPUT_DIM = INPUT_SIZE * INPUT_SIZE * 3
HIDDEN1 = 512
HIDDEN2 = 128
MIN_SAMPLES_PER_CLASS = 50
MIN_VAL_SAMPLES_PER_CLASS = 10


def load_images(data_dir, split, classes):
    """载入一个 split 全部图片 -> (images, labels)。内存约 3500*6912*4B ~ 97MB,可接受。"""
    images, labels = [], []
    for cls_idx, cls in enumerate(classes):
        root = os.path.join(data_dir, split, cls)
        for name in sorted(os.listdir(root)):
            if not name.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            img = Image.open(os.path.join(root, name)).convert("RGB")
            img = img.resize((INPUT_SIZE, INPUT_SIZE), Image.BILINEAR)
            arr = np.asarray(img, dtype=np.float32).reshape(-1) / 255.0
            images.append(arr)
            labels.append(cls_idx)
    return np.stack(images), np.array(labels, dtype=np.int64)


def count_images(data_dir, split, classes):
    counts = {}
    for cls in classes:
        root = os.path.join(data_dir, split, cls)
        if not os.path.isdir(root):
            counts[cls] = 0
            continue
        counts[cls] = len([f for f in os.listdir(root)
                           if f.lower().endswith((".jpg", ".jpeg", ".png"))])
    return counts


def check_dataset(data_dir):
    root = os.path.join(data_dir, "train")
    if not os.path.isdir(root):
        raise SystemExit(f"缺少 train 目录: {root}")
    classes = sorted(os.listdir(root))
    problems = []
    for split, minimum in (("train", MIN_SAMPLES_PER_CLASS), ("val", MIN_VAL_SAMPLES_PER_CLASS)):
        counts = count_images(data_dir, split, classes)
        for cls in classes:
            if counts.get(cls, 0) < minimum:
                problems.append(f"{split}/{cls} 样本不足: {counts.get(cls, 0)} 张 (< {minimum})")
    if problems:
        raise SystemExit(
            "数据集未满足训练条件,已中止(避免生成伪造权重/指标):\n  - "
            + "\n  - ".join(problems)
            + "\n\n请先收集并审核真实标注图片后重试。"
        )
    return classes, {s: count_images(data_dir, s, classes) for s in ("train", "val", "test")}


def xavier_init(rows, cols):
    """He/Xavier 均匀初始化,保证深层梯度稳定。"""
    limit = np.sqrt(6.0 / (rows + cols))
    return np.random.uniform(-limit, limit, size=(rows, cols)).astype(np.float32)


def relu(x):
    return np.maximum(x, 0.0)


def softmax(z):
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def augment_batch(x, rng):
    """向量化数据增强:随机水平翻转 / 水平平移 / 亮度扰动。"""
    out = x.copy()
    n = x.shape[0]
    w, h, c = INPUT_SIZE, INPUT_SIZE, 3

    flip = rng.random(n) < 0.5
    if flip.any():
        out[flip] = out[flip].reshape(-1, w, h, c)[:, :, ::-1, :].reshape(-1, w * h * c)

    shifts = rng.integers(-2, 3, size=n)
    for i in np.where(shifts != 0)[0]:
        img = out[i].reshape(w, h, c)
        out[i] = np.roll(img, shifts[i], axis=1).reshape(-1)

    gains = 1.0 + rng.uniform(-0.2, 0.2, size=n).astype(np.float32)
    out = out * gains[:, None]
    return np.clip(out, 0.0, 1.0)


def forward(x, params, dropout=0.0, rng=None):
    """推理/训练共用;dropout=0 时为纯推理。返回 (logits, cache)。"""
    W1, b1, W2, b2, W3, b3 = params
    z1 = x @ W1.T + b1
    a1 = relu(z1)
    if dropout > 0 and rng is not None:
        m1 = (rng.random(a1.shape) > dropout).astype(np.float32) / (1 - dropout)
        a1 = a1 * m1
    z2 = a1 @ W2.T + b2
    a2 = relu(z2)
    if dropout > 0 and rng is not None:
        m2 = (rng.random(a2.shape) > dropout).astype(np.float32) / (1 - dropout)
        a2 = a2 * m2
    z3 = a2 @ W3.T + b3
    return z3, (x, z1, a1, z2, a2, z3)


def compute_loss(logits, labels, class_weight):
    probs = softmax(logits)
    idx = np.arange(labels.shape[0])
    ce = -np.log(np.clip(probs[idx, labels], 1e-12, 1.0))
    return float((ce * class_weight[labels]).mean()), probs


def backward(x, labels, logits, probs, cache, class_weight, params, batch_size):
    W1, b1, W2, b2, W3, b3 = params
    x, z1, a1, z2, a2, z3 = cache

    onehot = np.zeros_like(probs)
    onehot[np.arange(batch_size), labels] = 1.0
    dz3 = (probs - onehot) * class_weight[labels][:, None]

    dW3 = dz3.T @ a2 / batch_size
    db3 = dz3.mean(axis=0)

    da2 = dz3 @ W3
    dz2 = da2 * (z2 > 0)
    dW2 = dz2.T @ a1 / batch_size
    db2 = dz2.mean(axis=0)

    da1 = dz2 @ W2
    dz1 = da1 * (z1 > 0)
    dW1 = dz1.T @ x / batch_size
    db1 = dz1.mean(axis=0)

    return [dW1, db1, dW2, db2, dW3, db3]


def evaluate(X, y, params, class_weight):
    logits, _ = forward(X, params)
    probs = softmax(logits)
    pred = probs.argmax(axis=1)
    acc = float((pred == y).mean())
    n = len(np.unique(y))
    confusion = np.zeros((n, n), dtype=int)
    for t, p in zip(y, pred):
        confusion[t, p] += 1
    f1s = []
    for i in range(n):
        tp = confusion[i, i]
        fn = confusion[i, :].sum() - tp
        fp = confusion[:, i].sum() - tp
        f1s.append(2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0)
    per_class = np.array([confusion[i, i] / confusion[i, :].sum() if confusion[i, :].sum() else 0.0
                          for i in range(n)])
    return acc, sum(f1s) / n, per_class, confusion


def main():
    parser = argparse.ArgumentParser(description="纯 NumPy MLP 草莓图像分类训练")
    parser.add_argument("--data-dir", default="../datasets/riseholme-classification")
    parser.add_argument("--model-version", default="riseholme-2021-mlp-v1")
    parser.add_argument("--output", default="models/riseholme_mlp_v1.npz")
    parser.add_argument("--labels-out", default="models/labels.json")
    parser.add_argument("--report-out", default="models/training_report.json")
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    random.seed(args.seed)

    classes, class_counts = check_dataset(args.data_dir)
    print("类别(自动发现):", classes)
    print("类别样本数:", json.dumps(class_counts, ensure_ascii=False, indent=2))

    X_train, y_train = load_images(args.data_dir, "train", classes)
    X_val, y_val = load_images(args.data_dir, "val", classes)
    X_test, y_test = load_images(args.data_dir, "test", classes)
    print(f"载入完成 train={X_train.shape} val={X_val.shape} test={X_test.shape}")

    # 频率倒数加权(Unripe 占比 68%,避免模型偏向大类)
    freqs = np.array([class_counts["train"][c] for c in classes], dtype=np.float32)
    class_weight = (freqs.sum() / freqs)
    class_weight = class_weight / class_weight.sum() * len(classes)

    params = [xavier_init(HIDDEN1, INPUT_DIM).astype(np.float32),
              np.zeros(HIDDEN1, dtype=np.float32),
              xavier_init(HIDDEN2, HIDDEN1).astype(np.float32),
              np.zeros(HIDDEN2, dtype=np.float32),
              xavier_init(len(classes), HIDDEN2).astype(np.float32),
              np.zeros(len(classes), dtype=np.float32)]

    # Adam 状态
    m = [np.zeros_like(p) for p in params]
    v = [np.zeros_like(p) for p in params]
    beta1, beta2, eps = 0.9, 0.999, 1e-8
    t_step = 0

    n_batches = int(np.ceil(X_train.shape[0] / args.batch_size))
    best_acc, best_state = 0.0, None
    start = time.time()
    for epoch in range(1, args.epochs + 1):
        perm = rng.permutation(X_train.shape[0])
        epoch_loss, seen = 0.0, 0
        for b in range(n_batches):
            idx = perm[b * args.batch_size:(b + 1) * args.batch_size]
            xb, yb = X_train[idx], y_train[idx]
            xb = augment_batch(xb, rng)
            t_step += 1

            logits, cache = forward(xb, params, dropout=0.3, rng=rng)
            loss, probs = compute_loss(logits, yb, class_weight)
            grads = backward(xb, yb, logits, probs, cache, class_weight, params, len(yb))

            for i in range(len(params)):
                m[i] = beta1 * m[i] + (1 - beta1) * grads[i]
                v[i] = beta2 * v[i] + (1 - beta2) * grads[i] ** 2
                m_hat = m[i] / (1 - beta1 ** t_step)
                v_hat = v[i] / (1 - beta2 ** t_step)
                params[i] -= args.lr * m_hat / (np.sqrt(v_hat) + eps)

            epoch_loss += loss * len(yb)
            seen += len(yb)

        val_acc, _, _, _ = evaluate(X_val, y_val, params, class_weight)
        print(f"epoch {epoch:02d}/{args.epochs} loss={epoch_loss / seen:.4f} val_acc={val_acc:.4f}")
        if val_acc > best_acc:
            best_acc = val_acc
            best_state = [p.copy() for p in params]

    if best_state is None:
        raise SystemExit("训练失败:未得到有效验证结果")

    acc, macro_f1, per_class, confusion = evaluate(X_test, y_test, best_state, class_weight)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    np.savez_compressed(args.output, W1=best_state[0], b1=best_state[1], W2=best_state[2],
                        b2=best_state[3], W3=best_state[4], b3=best_state[5],
                        model_version=args.model_version, classes=np.array(classes))

    for path in (args.labels_out, args.report_out):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(args.labels_out, "w", encoding="utf-8") as f:
        json.dump({"model_version": args.model_version, "classes": classes}, f,
                  ensure_ascii=False, indent=2)

    report = {
        "model_version": args.model_version,
        "classes": classes,
        "architecture": f"MLP({INPUT_DIM}-{HIDDEN1}-{HIDDEN2}-{len(classes)}), ReLU+Dropout0.3",
        "backend": "pure numpy (no torch)",
        "input_size": INPUT_SIZE,
        "dataset": os.path.abspath(args.data_dir),
        "seed": args.seed,
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "learning_rate": args.lr,
        "optimizer": "Adam",
        "data_augmentation": "hflip/shift+/-2px/brightness+/-20%",
        "class_weight": {c: round(float(w), 4) for c, w in zip(classes, class_weight)},
        "class_counts": class_counts,
        "test_accuracy": round(acc, 4),
        "test_macro_f1": round(macro_f1, 4),
        "test_per_class_accuracy": {c: round(float(v), 4) for c, v in zip(classes, per_class)},
        "confusion_matrix": confusion.tolist(),
        "training_seconds": round(time.time() - start, 1),
    }
    with open(args.report_out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n最佳验证准确率: {best_acc:.4f}")
    print(f"测试集准确率: {acc:.4f} | 宏平均 F1: {macro_f1:.4f}")
    print("每类准确率:", {c: round(float(v), 4) for c, v in zip(classes, per_class)})
    print("混淆矩阵:\n", confusion)
    print(f"权重已保存: {args.output}")
    print(f"训练报告已保存: {args.report_out}")


if __name__ == "__main__":
    main()
