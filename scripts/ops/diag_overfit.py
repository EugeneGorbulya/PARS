"""
Диагностика переобучения Stage A:
  1) Показывает metrics последних снапшотов профиля из БД.
  2) Скачивает последний Stage A снапшот из S3, инферит на train/val
     (тот же сплит по created_at + 15%, что и в dataset.py) и считает
     train_mae vs val_mae по каждой голове.

Запуск:
    python3 -m scripts.ops.diag_overfit --profile-id 2
    python3 -m scripts.ops.diag_overfit --profile-id 2 --last 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import List

import numpy as np
import torch
from sqlalchemy import select

sys.path.insert(0, os.getcwd())

from core.session import async_session
from models import ModelSnapshot
from services.ml.dataset import load_stage_a_bundle
from services.ml.inference import build_model_from_package, load_checkpoint_bytes
from services.s3.client import S3Client


def _mae(pred: np.ndarray, y: np.ndarray, mask: np.ndarray) -> float:
    if mask.sum() == 0:
        return float("nan")
    diff = np.abs(pred - y)
    return float((diff * mask.astype(np.float32)).sum() / mask.sum())


def _baseline_mae_mean(y_train: np.ndarray, m_train: np.ndarray, y_val: np.ndarray, m_val: np.ndarray) -> float:
    """MAE при предсказании train-средним по голове."""
    if m_train.sum() == 0 or m_val.sum() == 0:
        return float("nan")
    mu = float((y_train * m_train.astype(np.float32)).sum() / m_train.sum())
    return _mae(np.full_like(y_val, mu, dtype=np.float32), y_val, m_val)


async def run(profile_id: int, last_n: int) -> None:
    async with async_session() as session:
        snaps = (
            await session.execute(
                select(ModelSnapshot)
                .where(ModelSnapshot.profile_id == profile_id)
                .order_by(ModelSnapshot.created_at.desc())
                .limit(last_n)
            )
        ).scalars().all()

    if not snaps:
        print(f"Профиль #{profile_id}: снапшотов нет.")
        return

    print(f"\n=== Последние {len(snaps)} снапшотов профиля #{profile_id} ===\n")
    print(f"{'id':>5} {'head_type':<28} {'created_at':<20} metrics")
    print("─" * 110)
    for s in snaps:
        m = s.metrics or {}
        m_str = json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()}, ensure_ascii=False)
        print(f"{s.id:>5} {s.head_type:<28} {str(s.created_at)[:19]:<20} {m_str}")

    last_a = next((s for s in snaps if "stage_a" in s.head_type), None)
    if last_a is None:
        print("\n⚠️  Среди последних снапшотов нет Stage A — пересчёт train/val MAE пропускаю.")
        return

    print(f"\n=== Пересчёт MAE на train/val для Stage A id={last_a.id} ===\n")

    s3 = S3Client()
    raw = await s3.download_bytes(s3_uri=last_a.storage_uri)
    pkg = load_checkpoint_bytes(raw)
    model, scaler = build_model_from_package(pkg)
    model.eval()

    async with async_session() as session:
        train, val = await load_stage_a_bundle(session, profile_id, val_ratio=0.15)

    if train.clip.shape[0] == 0:
        print("Нет данных для пересчёта.")
        return

    def predict(bundle) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if bundle.clip.shape[0] == 0:
            empty = np.zeros((0,), np.float32)
            return empty, empty, empty
        tab_n = scaler.transform(bundle.tab)
        with torch.no_grad():
            b, pq, d = model(torch.from_numpy(bundle.clip).float(), torch.from_numpy(tab_n).float())
        return b.numpy(), pq.numpy(), d.numpy()

    tb, tpq, td = predict(train)
    vb, vpq, vd = predict(val)

    rows = []
    for name, pred_t, pred_v, y_t, y_v, m_t, m_v in [
        ("beauty",      tb,  vb,  train.y_beauty, val.y_beauty, train.mask_beauty, val.mask_beauty),
        ("price_q",     tpq, vpq, train.y_pq,     val.y_pq,     train.mask_pq,     val.mask_pq),
        ("distance",    td,  vd,  train.y_dist,   val.y_dist,   train.mask_dist,   val.mask_dist),
    ]:
        train_mae = _mae(pred_t, y_t, m_t)
        val_mae   = _mae(pred_v, y_v, m_v)
        base_mae  = _baseline_mae_mean(y_t, m_t, y_v, m_v)
        gap = val_mae - train_mae if not (np.isnan(train_mae) or np.isnan(val_mae)) else float("nan")
        rows.append((name, int(m_t.sum()), int(m_v.sum()), train_mae, val_mae, gap, base_mae))

    print(f"{'head':<10} {'n_train':>8} {'n_val':>6} {'train_mae':>10} {'val_mae':>9} {'gap':>7} {'baseline':>9}")
    print("─" * 75)
    for name, nt, nv, tr, va, gp, ba in rows:
        print(
            f"{name:<10} {nt:>8} {nv:>6} "
            f"{tr:>10.3f} {va:>9.3f} {gp:>+7.3f} {ba:>9.3f}"
        )
    print()
    print("Колонки:")
    print("  train_mae — MAE Stage A на тех же примерах, на которых модель училась")
    print("  val_mae   — MAE на отложенных 15% (по created_at)")
    print("  gap       — val_mae - train_mae (большой положительный gap = переобучение)")
    print("  baseline  — MAE при предсказании train-средним (если val_mae не сильно ниже —")
    print("              модель ничего не выучила сверх среднего)")
    print()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile-id", type=int, required=True)
    p.add_argument("--last", type=int, default=8, help="Сколько последних снапшотов вывести")
    args = p.parse_args()
    asyncio.run(run(args.profile_id, args.last))


if __name__ == "__main__":
    main()
