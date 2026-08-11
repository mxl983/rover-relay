#!/usr/bin/env python3
"""Simple CNN for cat posture classification (Project 5–style starter).

Classes: belly, rest, sit, stand, unclear
At inference, optional max-softmax threshold can still force "unclear" when unsure.

Training uses stratified K-fold CV, then retrains on all data for the saved weights.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from torch.utils.data import DataLoader, Subset
from torchvision import datasets
from torchvision.transforms import v2

ROOT = Path(__file__).resolve().parent
CLASS_NAMES = ("belly", "rest", "sit", "stand", "unclear")  # index 0..4
IMG_SIZE = 128


def pick_device() -> str:
    if hasattr(torch, "accelerator") and torch.accelerator.is_available():
        return torch.accelerator.current_accelerator().type
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


device = pick_device()


# Channel widths for conv stack: 16, 32, 64, 128, ...
CONV_CHANNELS = (16, 32, 64, 128, 256, 512, 512)
MAX_CONV_LAYERS = int(np.log2(IMG_SIZE))  # keep spatial size >= 1 after pools


def max_conv_for_size(img_size: int) -> int:
    """Largest num_conv that keeps spatial size >= 1 after 2× pools."""
    return int(np.log2(img_size))


class PostureNetwork(nn.Module):
    """Configurable conv-depth CNN → posture logits (log-softmax).

    Each conv is followed by MaxPool2d(2), so spatial size halves per layer.
    With img_size=128, num_conv=3 → feature map 64×16×16 (same as the starter).
    """

    def __init__(
        self,
        num_classes: int | None = None,
        num_conv: int = 3,
        img_size: int = IMG_SIZE,
    ):
        super().__init__()
        if num_classes is None:
            num_classes = len(CLASS_NAMES)
        max_conv = max_conv_for_size(img_size)
        if not 1 <= num_conv <= max_conv:
            raise ValueError(
                f"num_conv must be in 1..{max_conv} for img_size={img_size}"
            )
        if img_size % (2**num_conv) != 0:
            raise ValueError(
                f"img_size={img_size} must be divisible by 2^{num_conv}={2**num_conv}"
            )
        self.num_conv = num_conv
        self.num_classes = num_classes
        self.img_size = img_size
        channels = CONV_CHANNELS[:num_conv]

        layers: list[nn.Module] = []
        in_c = 3
        for out_c in channels:
            layers.append(nn.Conv2d(in_c, out_c, kernel_size=3, padding=1))
            in_c = out_c
        self.convs = nn.ModuleList(layers)

        spatial = img_size // (2**num_conv)
        self.drop = nn.Dropout(p=0.5)
        self.fc1 = nn.Linear(in_c * spatial * spatial, 128)
        self.fc2 = nn.Linear(128, num_classes)

        self.loss_fn = nn.NLLLoss()
        self.optimizer = torch.optim.Adam(self.parameters(), lr=1e-3)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        for conv in self.convs:
            x = F.relu(F.max_pool2d(conv(x), 2))
        x = torch.flatten(x, 1)
        x = self.drop(F.relu(self.fc1(x)))
        x = self.fc2(x)
        return F.log_softmax(x, dim=1)


def predict_label(
    model: PostureNetwork,
    x: torch.Tensor,
    *,
    class_names: tuple[str, ...] = CLASS_NAMES,
    unclear_thresh: float | None = 0.55,
) -> str:
    """Return predicted class name (includes trained 'unclear').

    If unclear_thresh is set and max softmax is below it, force 'unclear'.
    """
    model.eval()
    with torch.no_grad():
        log_prob = model(x.to(device))
        prob = log_prob.exp()
        conf, idx = prob.max(dim=1)
        if unclear_thresh is not None and float(conf[0]) < unclear_thresh:
            return "unclear"
        return class_names[int(idx[0])]


def train_network(dataloader, model, loss_fn, optimizer, *, quiet: bool = False) -> None:
    size = len(dataloader.dataset)
    model.train()
    for batch, (X, y) in enumerate(dataloader):
        X, y = X.to(device), y.to(device)
        pred = model(X)
        loss = loss_fn(pred, y)
        loss.backward()
        optimizer.step()
        optimizer.zero_grad()
        if not quiet and batch % 20 == 0:
            print(f"loss: {loss.item():>7f}  [{(batch + 1) * len(X):>5d}/{size:>5d}]")


def test_network(dataloader, model, loss_fn, set_name: str = "Test", *, quiet: bool = False):
    size = len(dataloader.dataset)
    num_batches = len(dataloader)
    model.eval()
    total_loss, correct = 0.0, 0.0
    with torch.no_grad():
        for X, y in dataloader:
            X, y = X.to(device), y.to(device)
            pred = model(X)
            total_loss += loss_fn(pred, y).item()
            correct += (pred.argmax(1) == y).type(torch.float).sum().item()
    avg_loss = total_loss / max(num_batches, 1)
    accuracy = correct / max(size, 1)
    if not quiet:
        print(f"{set_name}: Accuracy: {(100 * accuracy):>0.1f}%, Avg loss: {avg_loss:>8f}")
    return avg_loss, accuracy


class PostureImageFolder(datasets.ImageFolder):
    """ImageFolder using CLASS_NAMES folders (belly/rest/sit/stand/unclear)."""

    def find_classes(self, directory: str | Path):
        classes = [c for c in CLASS_NAMES if (Path(directory) / c).is_dir()]
        missing = [c for c in CLASS_NAMES if c not in classes]
        if missing:
            raise FileNotFoundError(
                f"Missing posture class folders under {directory}: {missing}"
            )
        class_to_idx = {c: i for i, c in enumerate(classes)}
        return classes, class_to_idx


def build_transforms(img_size: int = IMG_SIZE) -> v2.Compose:
    """Deterministic resize + tensorize. Augmentation is offline only."""
    return v2.Compose(
        [
            v2.Resize((img_size, img_size)),
            v2.ToImage(),
            v2.ToDtype(torch.float32, scale=True),
        ]
    )


def load_dataset(
    data_dir: Path | str, *, img_size: int = IMG_SIZE
) -> PostureImageFolder:
    return PostureImageFolder(
        root=str(data_dir), transform=build_transforms(img_size)
    )


def stratified_kfold(
    labels: list[int], n_folds: int, seed: int
) -> list[tuple[list[int], list[int]]]:
    """Return list of (train_idx, val_idx) with class balance per fold."""
    rng = np.random.default_rng(seed)
    by_class: dict[int, list[int]] = {}
    for i, y in enumerate(labels):
        by_class.setdefault(int(y), []).append(i)
    for idxs in by_class.values():
        rng.shuffle(idxs)

    folds: list[tuple[list[int], list[int]]] = []
    for k in range(n_folds):
        val: list[int] = []
        train: list[int] = []
        for idxs in by_class.values():
            for j, idx in enumerate(idxs):
                if j % n_folds == k:
                    val.append(idx)
                else:
                    train.append(idx)
        rng.shuffle(train)
        rng.shuffle(val)
        folds.append((train, val))
    return folds


def parse_aug_stem(path: str | Path) -> tuple[str, int, bool] | None:
    """Parse `{class}_{src:04d}_orig` / `{class}_{src:04d}_aug_{k:03d}` names."""
    parts = Path(path).stem.split("_")
    if len(parts) < 3:
        return None
    cls, src_s, kind = parts[0], parts[1], parts[2]
    try:
        src_i = int(src_s)
    except ValueError:
        return None
    if kind == "orig":
        return cls, src_i, False
    if kind == "aug":
        return cls, src_i, True
    return None


def original_indices(dataset: PostureImageFolder) -> list[int]:
    """Indices of original (non-aug) images; falls back to all if naming missing."""
    orig: list[int] = []
    for i, (path, _) in enumerate(dataset.samples):
        parsed = parse_aug_stem(path)
        if parsed is None or not parsed[2]:
            orig.append(i)
    return orig


def stratified_kfold_aug_train_orig_val(
    dataset: PostureImageFolder, n_folds: int, seed: int
) -> list[tuple[list[int], list[int]]]:
    """Fair CV for offline-aug datasets.

    Fold by original source image. Train gets that source's orig + augs;
    val gets originals only (never augs, never augs of val sources).
    Requires names from scripts/build_posture_augmented.py.
    """
    groups: dict[tuple[str, int], dict] = {}
    n_parsed = 0
    for idx, (path, y) in enumerate(dataset.samples):
        parsed = parse_aug_stem(path)
        if parsed is None:
            continue
        n_parsed += 1
        cls, src_i, is_aug = parsed
        key = (cls, src_i)
        g = groups.setdefault(key, {"orig": [], "all": [], "label": int(y)})
        g["all"].append(idx)
        if not is_aug:
            g["orig"].append(idx)

    if n_parsed < len(dataset.samples):
        raise SystemExit(
            "Augmented dataset filenames are missing source ids. Rebuild with:\n"
            "  python scripts/build_posture_augmented.py --target 2000"
        )

    keys = [k for k, g in groups.items() if g["orig"]]
    if not keys:
        raise SystemExit("No original (*_orig*) images found for validation splits")

    labels = [groups[k]["label"] for k in keys]
    group_folds = stratified_kfold(labels, n_folds, seed)
    rng = np.random.default_rng(seed)

    folds: list[tuple[list[int], list[int]]] = []
    for train_g, val_g in group_folds:
        train_idx: list[int] = []
        val_idx: list[int] = []
        for gi in train_g:
            train_idx.extend(groups[keys[gi]]["all"])
        for gi in val_g:
            val_idx.extend(groups[keys[gi]]["orig"])
        rng.shuffle(train_idx)
        rng.shuffle(val_idx)
        folds.append((train_idx, val_idx))
    return folds


def run_fold(
    data_dir: Path | str,
    train_idx: list[int],
    val_idx: list[int],
    *,
    epochs: int,
    batch_size: int,
    num_conv: int = 3,
    img_size: int = IMG_SIZE,
    quiet_epochs: bool = False,
    train_eval_idx: list[int] | None = None,
) -> float:
    """Train one fold; return final val accuracy.

    train_eval_idx: optional subset for reported train accuracy (e.g. originals only).
    """
    dataset = load_dataset(data_dir, img_size=img_size)
    eval_train_idx = train_eval_idx if train_eval_idx is not None else train_idx
    train_loader = DataLoader(
        Subset(dataset, train_idx), batch_size=batch_size, shuffle=True
    )
    train_eval_loader = DataLoader(
        Subset(dataset, eval_train_idx), batch_size=batch_size, shuffle=False
    )
    val_loader = DataLoader(
        Subset(dataset, val_idx), batch_size=batch_size, shuffle=False
    )
    model = PostureNetwork(
        num_classes=len(CLASS_NAMES), num_conv=num_conv, img_size=img_size
    ).to(device)
    for t in range(epochs):
        train_network(
            train_loader, model, model.loss_fn, model.optimizer, quiet=True
        )
        if not quiet_epochs and (t == 0 or t == epochs - 1 or (t + 1) % 5 == 0):
            _, train_acc = test_network(
                train_eval_loader, model, model.loss_fn, set_name="Train", quiet=True
            )
            _, val_acc = test_network(
                val_loader, model, model.loss_fn, set_name="Val", quiet=True
            )
            print(
                f"  epoch {t + 1:>2d}/{epochs}: "
                f"train {100 * train_acc:5.1f}%  val {100 * val_acc:5.1f}%"
            )
    _, val_acc = test_network(
        val_loader, model, model.loss_fn, set_name="Val", quiet=True
    )
    return val_acc


def retrain_full(
    data_dir: Path | str,
    *,
    epochs: int,
    batch_size: int,
    num_conv: int = 3,
    img_size: int = IMG_SIZE,
    eval_originals_only: bool = False,
) -> PostureNetwork:
    """Train on all labeled images for the saved deployment weights."""
    dataset = load_dataset(data_dir, img_size=img_size)
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)
    eval_idx = original_indices(dataset) if eval_originals_only else list(range(len(dataset)))
    eval_loader = DataLoader(
        Subset(dataset, eval_idx), batch_size=batch_size, shuffle=False
    )
    model = PostureNetwork(
        num_classes=len(CLASS_NAMES), num_conv=num_conv, img_size=img_size
    ).to(device)
    for t in range(epochs):
        train_network(loader, model, model.loss_fn, model.optimizer, quiet=True)
        if t == 0 or t == epochs - 1 or (t + 1) % 5 == 0:
            _, acc = test_network(
                eval_loader, model, model.loss_fn, set_name="Full", quiet=True
            )
            tag = "orig" if eval_originals_only else "all"
            print(f"  epoch {t + 1:>2d}/{epochs}: train({tag}) {100 * acc:5.1f}%")
    return model


AUGMENTED_DATA = ROOT / "datasets" / "my_cat_dataset_part2_augmented"


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Train posture CNN with stratified K-fold CV")
    p.add_argument(
        "--data",
        type=Path,
        default=ROOT / "datasets" / "my_cat_dataset_part2",
    )
    p.add_argument(
        "--use-augmented-data",
        action="store_true",
        help=(
            f"Use offline-balanced dataset at {AUGMENTED_DATA.name} "
            "(from scripts/build_posture_augmented.py). Overrides --data. "
            "Train uses orig+aug of train sources; val/test uses originals only."
        ),
    )
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--epochs", type=int, default=25)
    p.add_argument("--batch", type=int, default=32)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--num-conv",
        type=int,
        default=3,
        help=f"Number of conv+maxpool layers (1..{MAX_CONV_LAYERS})",
    )
    p.add_argument(
        "--img-size",
        type=int,
        default=IMG_SIZE,
        help=f"CNN input size (default {IMG_SIZE})",
    )
    return p.parse_args(argv[1:])


def main(argv: list[str]) -> None:
    args = parse_args(argv)
    data_dir = AUGMENTED_DATA if args.use_augmented_data else args.data
    if args.use_augmented_data and not data_dir.is_dir():
        raise SystemExit(
            f"Missing {data_dir}. Build it first:\n"
            "  python scripts/build_posture_augmented.py --target 2000"
        )
    max_c = max_conv_for_size(args.img_size)
    if not 1 <= args.num_conv <= max_c:
        raise SystemExit(
            f"num_conv={args.num_conv} invalid for img-size={args.img_size} "
            f"(need 1..{max_c})"
        )
    if args.img_size % (2**args.num_conv) != 0:
        raise SystemExit(
            f"img-size={args.img_size} must be divisible by "
            f"2^{args.num_conv}={2**args.num_conv}"
        )
    print(f"Using {device} device")

    dataset = load_dataset(data_dir, img_size=args.img_size)
    print(f"Data: {data_dir}")
    print(f"Loaded {len(dataset)} images | classes: {dataset.classes}")
    if args.use_augmented_data:
        folds = stratified_kfold_aug_train_orig_val(
            dataset, args.folds, args.seed
        )
        n_orig = len(original_indices(dataset))
        print(
            f"Stratified {args.folds}-fold CV on {n_orig} originals | "
            f"train=orig+aug of train sources, val=originals only | "
            f"{args.epochs} epochs/fold | num_conv={args.num_conv} | "
            f"img_size={args.img_size}"
        )
    else:
        labels = [y for _, y in dataset.samples]
        folds = stratified_kfold(labels, args.folds, args.seed)
        print(
            f"Stratified {args.folds}-fold CV | {args.epochs} epochs/fold | "
            f"num_conv={args.num_conv} | img_size={args.img_size}"
        )

    fold_accs: list[float] = []
    for k, (train_idx, val_idx) in enumerate(folds):
        train_orig_idx = [
            i
            for i in train_idx
            if (p := parse_aug_stem(dataset.samples[i][0])) is None or not p[2]
        ]
        print(
            f"\nFold {k + 1}/{args.folds}  "
            f"train={len(train_idx)} (orig={len(train_orig_idx)}) "
            f"val={len(val_idx)} originals"
        )
        acc = run_fold(
            data_dir,
            train_idx,
            val_idx,
            epochs=args.epochs,
            batch_size=args.batch,
            num_conv=args.num_conv,
            img_size=args.img_size,
            train_eval_idx=train_orig_idx if args.use_augmented_data else None,
        )
        fold_accs.append(acc)
        print(f"Fold {k + 1} val accuracy (originals): {100 * acc:.1f}%")

    mean = float(np.mean(fold_accs))
    std = float(np.std(fold_accs))
    print("\n========== CV summary ==========")
    for k, acc in enumerate(fold_accs):
        print(f"  fold {k + 1}: {100 * acc:5.1f}%")
    print(f"  mean      {100 * mean:5.1f}%  ± {100 * std:.1f}%")
    if args.use_augmented_data:
        print("  (validation = original images only)")
    print("================================")

    print(f"\nRetraining on all {len(dataset)} images for saved weights ...")
    model = retrain_full(
        data_dir,
        epochs=args.epochs,
        batch_size=args.batch,
        num_conv=args.num_conv,
        img_size=args.img_size,
        eval_originals_only=args.use_augmented_data,
    )
    # Deployed checkpoint (experiments never write here)
    out = ROOT / "models" / "part2_posture_cnn.pth"
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "state_dict": model.state_dict(),
            "num_conv": args.num_conv,
            "img_size": args.img_size,
            "num_classes": len(CLASS_NAMES),
            "class_names": list(CLASS_NAMES),
            "data": str(data_dir),
            "use_augmented_data": args.use_augmented_data,
        },
        out,
    )
    print(
        f"Saved model to {out} "
        f"(num_conv={args.num_conv}, img_size={args.img_size}, classes={CLASS_NAMES})"
    )


if __name__ == "__main__":
    main(sys.argv)
