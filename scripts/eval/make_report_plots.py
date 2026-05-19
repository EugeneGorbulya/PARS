"""
Generates the three figures used in the LaTeX report:
 - graphics/training_curves.png : Stage A loss + per-head val MAE per epoch
 - graphics/rating_histogram.png: histogram of ratings (beauty / pq / distance)

Re-runs Stage A training from scratch on the same chronological 85/15 split
used by `services.ml.pipeline`, capturing per-epoch metrics. Snapshot writes
to the DB / S3 are disabled (we only need the curve).

Usage:
    python -m scripts.eval.make_report_plots --profile-id 2 \\
        --out /Users/.../course_work_this_year/graphics
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import torch
from sqlalchemy import select

from core.session import async_session
from models import Rating
from services.ml.dataset import load_stage_a_bundle
from services.ml.model import PreferenceModel, multi_task_loss
from services.ml.preprocess import TabularScaler


def _mae(pred: torch.Tensor, y: torch.Tensor, m: torch.Tensor) -> float:
    d = (pred - y).abs()
    return float((d * m.float()).sum() / m.float().sum().clamp(min=1))


async def stage_a_history(profile_id: int, epochs: int = 80, lr: float = 1e-3, seed: int = 0):
    """Re-trains Stage A capturing per-epoch train loss + per-head val MAE."""
    torch.manual_seed(seed)
    np.random.seed(seed)

    async with async_session() as s:
        tr, val = await load_stage_a_bundle(s, profile_id)

    scaler = TabularScaler.fit(tr.tab)
    tab_n = scaler.transform(tr.tab)
    val_tab_n = scaler.transform(val.tab)

    device = torch.device("cpu")
    tc = torch.from_numpy(tr.clip).float().to(device)
    tt = torch.from_numpy(tab_n).float().to(device)
    t_yb = torch.from_numpy(tr.y_beauty).float().to(device)
    t_ypq = torch.from_numpy(tr.y_pq).float().to(device)
    t_yd = torch.from_numpy(tr.y_dist).float().to(device)
    t_mb = torch.from_numpy(tr.mask_beauty).to(device)
    t_mpq = torch.from_numpy(tr.mask_pq).to(device)
    t_md = torch.from_numpy(tr.mask_dist).to(device)

    vc = torch.from_numpy(val.clip).float().to(device)
    vt = torch.from_numpy(val_tab_n).float().to(device)
    vyb = torch.from_numpy(val.y_beauty).to(device)
    vypq = torch.from_numpy(val.y_pq).to(device)
    vyd = torch.from_numpy(val.y_dist).to(device)
    vmb = torch.from_numpy(val.mask_beauty).to(device)
    vmpq = torch.from_numpy(val.mask_pq).to(device)
    vmd = torch.from_numpy(val.mask_dist).to(device)

    model = PreferenceModel().to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-2)

    hist = {"epoch": [], "loss": [], "mae_b": [], "mae_pq": [], "mae_d": []}
    best_sum, best_epoch = float("inf"), -1
    for ep in range(epochs):
        model.train()
        opt.zero_grad()
        pb, ppq, pd = model(tc, tt)
        loss = multi_task_loss(pb, ppq, pd, t_yb, t_ypq, t_yd, t_mb, t_mpq, t_md)
        loss.backward()
        opt.step()

        model.eval()
        with torch.no_grad():
            vb, vpq, vd = model(vc, vt)
            mb = _mae(vb, vyb, vmb)
            mpq = _mae(vpq, vypq, vmpq)
            md = _mae(vd, vyd, vmd)
        hist["epoch"].append(ep + 1)
        hist["loss"].append(float(loss.detach().cpu()))
        hist["mae_b"].append(mb)
        hist["mae_pq"].append(mpq)
        hist["mae_d"].append(md)
        s = mb + mpq + md
        if s < best_sum:
            best_sum, best_epoch = s, ep + 1

    return hist, best_epoch, tr, val


def plot_training_curves(hist, best_epoch, out_path: Path):
    fig, ax = plt.subplots(1, 2, figsize=(11, 4))

    ax[0].plot(hist["epoch"], hist["loss"], color="#2b7bba", lw=2)
    ax[0].set_xlabel("Epoch")
    ax[0].set_ylabel("Training loss (masked MSE)")
    ax[0].set_title("Stage A — training loss")
    ax[0].grid(alpha=0.3)
    ax[0].axvline(best_epoch, color="#888", ls="--", lw=1,
                  label=f"best-by-val epoch = {best_epoch}")
    ax[0].legend(loc="upper right", fontsize=9)

    ax[1].plot(hist["epoch"], hist["mae_b"],  label="beauty",        color="#c0392b", lw=2)
    ax[1].plot(hist["epoch"], hist["mae_pq"], label="price-quality", color="#27ae60", lw=2)
    ax[1].plot(hist["epoch"], hist["mae_d"],  label="distance",      color="#2980b9", lw=2)
    ax[1].axvline(best_epoch, color="#888", ls="--", lw=1)
    ax[1].set_xlabel("Epoch")
    ax[1].set_ylabel("Validation MAE")
    ax[1].set_title("Stage A — per-head validation MAE")
    ax[1].grid(alpha=0.3)
    ax[1].legend(loc="upper right", fontsize=9)

    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(f"saved {out_path}")


async def collect_ratings(profile_id: int):
    async with async_session() as s:
        rows = (await s.execute(
            select(Rating.beauty, Rating.price_quality, Rating.distance_pref)
            .where(Rating.profile_id == profile_id)
            .where(Rating.skipped.is_(False))
        )).all()
    b  = [int(r[0]) for r in rows if r[0] is not None]
    pq = [int(r[1]) for r in rows if r[1] is not None]
    d  = [int(r[2]) for r in rows if r[2] is not None]
    return b, pq, d


def plot_rating_histogram(b, pq, d, out_path: Path):
    fig, ax = plt.subplots(figsize=(8, 4.2))
    bins = np.arange(0.5, 6.0, 1.0)
    x = np.arange(1, 6)
    width = 0.27

    def counts(arr):
        h, _ = np.histogram(arr, bins=bins)
        return h

    cb, cpq, cd = counts(b), counts(pq), counts(d)
    ax.bar(x - width, cb,  width=width, label=f"beauty (n={len(b)})",        color="#c0392b", edgecolor="white")
    ax.bar(x,         cpq, width=width, label=f"price-quality (n={len(pq)})", color="#27ae60", edgecolor="white")
    ax.bar(x + width, cd,  width=width, label=f"distance (n={len(d)})",      color="#2980b9", edgecolor="white")

    ax.set_xticks(x)
    ax.set_xlabel("Rating value (1–5)")
    ax.set_ylabel("Number of ratings")
    ax.set_title("Distribution of user ratings per factor")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(f"saved {out_path}")


async def main_async(args):
    out = Path(args.out)
    hist, best_epoch, tr, val = await stage_a_history(args.profile_id, epochs=args.epochs)
    plot_training_curves(hist, best_epoch, out / "training_curves.png")

    b, pq, d = await collect_ratings(args.profile_id)
    plot_rating_histogram(b, pq, d, out / "rating_histogram.png")

    print(f"n_train={tr.clip.shape[0]}, n_val={val.clip.shape[0]}, best_epoch={best_epoch}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile-id", type=int, required=True)
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--out", type=str,
                    default=".cursor/course_work_this_year/graphics")
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
