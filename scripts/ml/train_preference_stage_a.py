"""
Запуск Stage A для одного профиля из CLI. Делегирует в services.ml.pipeline.run_stage_a,
чтобы вся логика тренировки (weight_decay, best-by-val, snapshot upload) жила в одном
месте и не расходилась между ботом и скриптами.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.getcwd())

from services.ml.pipeline import run_stage_a


async def async_main(profile_id: int, epochs: int, lr: float, device: str | None) -> None:
    res = await run_stage_a(
        profile_id=profile_id,
        epochs=epochs,
        lr=lr,
        device=device,
        progress=None,
    )
    print(
        f"Saved snapshot {res.snapshot_id}  "
        f"train_loss={res.train_loss:.4f}  best_epoch={res.best_epoch}  "
        f"n_train={res.n_train}  n_val={res.n_val}"
    )
    print(
        f"  beauty:   train={res.train_mae_beauty:.3f}  val={res.val_mae_beauty:.3f}"
        if res.val_mae_beauty is not None else "  beauty:   (no val)"
    )
    print(
        f"  pq:       train={res.train_mae_pq:.3f}  val={res.val_mae_pq:.3f}"
        if res.val_mae_pq is not None else "  pq:       (no val)"
    )
    print(
        f"  distance: train={res.train_mae_dist:.3f}  val={res.val_mae_dist:.3f}"
        if res.val_mae_dist is not None else "  distance: (no val)"
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile-id", type=int, required=True)
    p.add_argument("--epochs", type=int, default=80)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--device", type=str, default=None)
    args = p.parse_args()
    asyncio.run(async_main(args.profile_id, args.epochs, args.lr, args.device))


if __name__ == "__main__":
    main()
