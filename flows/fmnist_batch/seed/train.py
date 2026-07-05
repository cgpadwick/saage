#!/usr/bin/env python3
"""Fashion-MNIST baseline — deliberately modest (a small MLP) so there is
real headroom for the hill-climb to find.

Contract (the batch machinery depends on this — keep it when editing):
  - CLI: --epochs N  --seed N  [--max-batches N]  [--device cpu|cuda|auto]
  - at exit, write eval_results.json: {"metric_name": "val_accuracy",
    "value": <float>} and print "VAL_SCORE=<float>" on the last line.
  - validation = the official Fashion-MNIST test split; NEVER train on it.
  - data root: $FMNIST_DATA, else $SAAGE_CACHE/datasets/fmnist, else ./data.

Self-contained data loading (no torchvision): the four official IDX files,
downloaded if absent, parsed with numpy.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import urllib.request
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

MIRROR = "https://raw.githubusercontent.com/zalandoresearch/fashion-mnist/master/data/fashion/"
FILES = ("train-images-idx3-ubyte.gz", "train-labels-idx1-ubyte.gz",
         "t10k-images-idx3-ubyte.gz", "t10k-labels-idx1-ubyte.gz")
MEAN, STD = 0.2860, 0.3530


def data_root() -> Path:
    if os.environ.get("FMNIST_DATA"):
        return Path(os.environ["FMNIST_DATA"])
    cache = os.environ.get("SAAGE_CACHE")
    if cache and (Path(cache) / "datasets" / "fmnist" / FILES[0]).exists():
        return Path(cache) / "datasets" / "fmnist"
    return Path("./data")


def _idx(path: Path, header: int) -> np.ndarray:
    return np.frombuffer(gzip.open(path).read(), np.uint8, offset=header)


def load_split(root: Path, train: bool) -> TensorDataset:
    root.mkdir(parents=True, exist_ok=True)
    prefix = "train" if train else "t10k"
    for name in FILES:
        if name.startswith(prefix) and not (root / name).exists():
            urllib.request.urlretrieve(MIRROR + name, root / name)
    images = _idx(root / f"{prefix}-images-idx3-ubyte.gz", 16).reshape(-1, 1, 28, 28)
    labels = _idx(root / f"{prefix}-labels-idx1-ubyte.gz", 8)
    x = (torch.from_numpy(images.copy()).float() / 255.0 - MEAN) / STD
    return TensorDataset(x, torch.from_numpy(labels.copy()).long())


class MLP(nn.Module):
    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            nn.Flatten(),
            nn.Linear(28 * 28, 256), nn.ReLU(),
            nn.Linear(256, 10),
        )

    def forward(self, x):
        return self.net(x)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-batches", type=int, default=0,
                    help="smoke mode: cap batches per epoch")
    ap.add_argument("--device", default="auto")
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    random.seed(args.seed)
    device = ("cuda" if torch.cuda.is_available() else "cpu") \
        if args.device == "auto" else args.device

    root = data_root()
    train_dl = DataLoader(load_split(root, train=True), batch_size=128,
                          shuffle=True, pin_memory=(device == "cuda"))
    val_dl = DataLoader(load_split(root, train=False), batch_size=512)

    model = MLP().to(device)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = nn.CrossEntropyLoss()

    for epoch in range(args.epochs):
        model.train()
        for b, (x, y) in enumerate(train_dl):
            if args.max_batches and b >= args.max_batches:
                break
            x, y = x.to(device), y.to(device)
            opt.zero_grad()
            loss = loss_fn(model(x), y)
            loss.backward()
            opt.step()

        model.eval()
        correct = total = 0
        with torch.no_grad():
            for x, y in val_dl:
                x, y = x.to(device), y.to(device)
                correct += (model(x).argmax(1) == y).sum().item()
                total += y.numel()
        acc = correct / total
        print(f"epoch {epoch + 1}/{args.epochs} val_acc={acc:.4f}", flush=True)

    Path("eval_results.json").write_text(json.dumps(
        {"metric_name": "val_accuracy", "value": acc}) + "\n")
    print(f"VAL_SCORE={acc:.4f}")


if __name__ == "__main__":
    main()
