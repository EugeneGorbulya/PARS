"""
Full ML training pipeline: Stage A → Pairwise synthesis → Stage B → Scoring.
Used by the bot /train command.
"""
from __future__ import annotations

import asyncio
import io
import itertools
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable

import numpy as np
import torch
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from core.session import async_session
from models import ModelSnapshot, Profile, PairwiseRating, Rating
from services.ml.config import CLIP_MODEL_TAG
from services.ml.dataset import load_stage_a_bundle, train_flat_ids
from services.ml.dataset_pairwise import load_pairwise_bundle
from services.ml.inference import (
    build_model_from_package,
    load_checkpoint_bytes,
    score_profile_flats as svc_score_flats,
)
from services.ml.model import PreferenceModel, multi_task_loss, pairwise_factor_loss
from services.ml.preprocess import TabularScaler
from services.s3.client import S3Client

FACTORS = ["beauty", "price_quality", "distance_pref"]

# Callback type: async fn that receives a progress string
ProgressCb = Callable[[str], Awaitable[None]]


@dataclass
class StageAResult:
    train_loss: float
    val_mae_beauty: float | None
    val_mae_pq: float | None
    val_mae_dist: float | None
    train_mae_beauty: float | None
    train_mae_pq: float | None
    train_mae_dist: float | None
    best_epoch: int | None
    n_train: int
    n_val: int
    snapshot_id: int


@dataclass
class SynthResult:
    n_duels: int
    n_records: int


@dataclass
class StageBResult:
    train_loss: float
    snapshot_id: int


@dataclass
class PipelineResult:
    stage_a: StageAResult
    synth: SynthResult
    stage_b: StageBResult
    scored_count: int


# ──────────────────────────────────────────────
# Stage A
# ──────────────────────────────────────────────

def _train_loop_a(
    model,
    scaler,
    tr,
    val,
    epochs,
    lr,
    device,
    weight_decay: float = 1e-2,
) -> dict:
    """
    Synchronous Stage A training loop (called via asyncio.to_thread).

    - AdamW + weight_decay → L2-регуляризация (борьба с переобучением).
    - Best-by-val checkpoint → возвращаем веса эпохи с минимальной суммой val_mae,
      а не последней. Без этого 80 эпох full-batch GD легко уезжают в оверфит.
    - В метриках возвращаем и train_mae, чтобы был виден gap.
    """
    tab_n = scaler.transform(tr.tab)
    tc = torch.from_numpy(tr.clip).float().to(device)
    tt = torch.from_numpy(tab_n).float().to(device)
    t_yb = torch.from_numpy(tr.y_beauty).float().to(device)
    t_ypq = torch.from_numpy(tr.y_pq).float().to(device)
    t_yd = torch.from_numpy(tr.y_dist).float().to(device)
    t_mb = torch.from_numpy(tr.mask_beauty).to(device)
    t_mpq = torch.from_numpy(tr.mask_pq).to(device)
    t_md = torch.from_numpy(tr.mask_dist).to(device)

    has_val = val.clip.shape[0] > 0
    if has_val:
        vtn = scaler.transform(val.tab)
        vc = torch.from_numpy(val.clip).float().to(device)
        vt = torch.from_numpy(vtn).float().to(device)
        vyb = torch.from_numpy(val.y_beauty).to(device)
        vypq = torch.from_numpy(val.y_pq).to(device)
        vyd = torch.from_numpy(val.y_dist).to(device)
        vmb = torch.from_numpy(val.mask_beauty).to(device)
        vmpq = torch.from_numpy(val.mask_pq).to(device)
        vmd = torch.from_numpy(val.mask_dist).to(device)

    def mae(pred, y, m):
        d = (pred - y).abs()
        return float((d * m.float()).sum() / m.float().sum().clamp(min=1))

    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    last_loss = 0.0
    best_val_sum = float("inf")
    best_state: dict | None = None
    best_epoch = -1
    best_val_metrics: dict = {}

    for ep in range(epochs):
        model.train()
        opt.zero_grad()
        pb, ppq, pd = model(tc, tt)
        loss = multi_task_loss(pb, ppq, pd, t_yb, t_ypq, t_yd, t_mb, t_mpq, t_md)
        loss.backward()
        opt.step()
        last_loss = float(loss.detach().cpu())

        if has_val:
            model.eval()
            with torch.no_grad():
                vb, vpq, vd = model(vc, vt)
                vmae_b  = mae(vb,  vyb,  vmb)
                vmae_pq = mae(vpq, vypq, vmpq)
                vmae_d  = mae(vd,  vyd,  vmd)
                vsum = vmae_b + vmae_pq + vmae_d
            if vsum < best_val_sum:
                best_val_sum = vsum
                best_epoch = ep
                best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
                best_val_metrics = {
                    "val_mae_beauty": vmae_b,
                    "val_mae_pq":     vmae_pq,
                    "val_mae_dist":   vmae_d,
                }

    if has_val and best_state is not None:
        model.load_state_dict(best_state)

    model.eval()
    with torch.no_grad():
        pb, ppq, pd = model(tc, tt)
        train_mae_b  = mae(pb,  t_yb,  t_mb)
        train_mae_pq = mae(ppq, t_ypq, t_mpq)
        train_mae_d  = mae(pd,  t_yd,  t_md)

    metrics: dict = {
        "train_loss":       last_loss,
        "train_mae_beauty": train_mae_b,
        "train_mae_pq":     train_mae_pq,
        "train_mae_dist":   train_mae_d,
        "weight_decay":     weight_decay,
    }
    if has_val:
        metrics.update(best_val_metrics)
        metrics["best_epoch"] = best_epoch
    return metrics


async def run_stage_a(
    profile_id: int,
    epochs: int = 80,
    lr: float = 1e-3,
    device: str | None = None,
    progress: ProgressCb | None = None,
) -> StageAResult:
    dev = device or ("cuda" if torch.cuda.is_available() else "cpu")

    if progress:
        await progress("📥 Загружаю данные для Stage A...")

    async with async_session() as session:
        train, val = await load_stage_a_bundle(session, profile_id, val_ratio=0.15)

    n_train = train.clip.shape[0]
    n_val = val.clip.shape[0]
    if n_train == 0:
        raise ValueError("Нет обучающих данных — нужны рейтинги с CLIP-эмбеддингами.")

    if progress:
        await progress(f"🧠 Stage A: обучение {epochs} эпох на {n_train} примерах...")

    scaler = TabularScaler.fit(train.tab)
    model = PreferenceModel(tab_dim=train.tab.shape[1]).to(dev)

    metrics = await asyncio.to_thread(_train_loop_a, model, scaler, train, val, epochs, lr, dev)

    if progress:
        await progress("💾 Сохраняю модель в S3...")

    buf = io.BytesIO()
    pkg = {
        "model_state": model.cpu().state_dict(),
        "scaler": scaler.state_dict(),
        "tab_dim": int(train.tab.shape[1]),
        "clip_tag": CLIP_MODEL_TAG,
        "stage": "a",
    }
    torch.save(pkg, buf)
    ts = int(time.time())
    key = f"models/{profile_id}/stage_a_{ts}.pt"
    s3 = S3Client()
    uri = await s3.upload_bytes(buf.getvalue(), key, content_type="application/octet-stream")

    async with async_session() as session:
        snap = ModelSnapshot(
            profile_id=profile_id,
            backbone=CLIP_MODEL_TAG,
            head_type="PreferenceMLP_stage_a",
            storage_uri=uri,
            metrics=metrics,
        )
        session.add(snap)
        await session.flush()
        await session.execute(
            update(Profile).where(Profile.id == profile_id).values(last_trained_snapshot_id=snap.id)
        )
        await session.commit()
        snap_id = snap.id

    return StageAResult(
        train_loss=metrics["train_loss"],
        val_mae_beauty=metrics.get("val_mae_beauty"),
        val_mae_pq=metrics.get("val_mae_pq"),
        val_mae_dist=metrics.get("val_mae_dist"),
        train_mae_beauty=metrics.get("train_mae_beauty"),
        train_mae_pq=metrics.get("train_mae_pq"),
        train_mae_dist=metrics.get("train_mae_dist"),
        best_epoch=metrics.get("best_epoch"),
        n_train=n_train,
        n_val=n_val,
        snapshot_id=snap_id,
    )


# ──────────────────────────────────────────────
# Pairwise synthesis
# ──────────────────────────────────────────────

async def synthesize_pairwise(
    profile_id: int,
    n_duels: int,
    progress: ProgressCb | None = None,
) -> SynthResult:
    if progress:
        await progress("🔄 Подготовка данных для дообучения...")

    async with async_session() as session:
        pr = (await session.execute(select(Profile).where(Profile.id == profile_id))).scalar_one()
        user_id = pr.user_id

        rows = (await session.execute(
            select(Rating.flat_id, Rating.beauty, Rating.price_quality, Rating.distance_pref)
            .where(Rating.profile_id == profile_id)
            .where(Rating.skipped.is_(False))
            .where(
                (Rating.beauty.is_not(None))
                | (Rating.price_quality.is_not(None))
                | (Rating.distance_pref.is_not(None))
            )
        )).all()

        scores: dict[int, dict] = {
            int(r.flat_id): {"beauty": r.beauty, "price_quality": r.price_quality, "distance_pref": r.distance_pref}
            for r in rows
        }
        # Только train-часть: пары, содержащие val-квартиры, утекают их лейблы
        # в Stage B и делают eval_baselines нечестным.
        train_ids = await train_flat_ids(session, profile_id)
        flat_ids = [fid for fid in scores.keys() if fid in train_ids]
        all_pairs = list(itertools.combinations(flat_ids, 2))
        selected = random.sample(all_pairs, min(n_duels, len(all_pairs)))

        records = []
        for a, b in selected:
            if a > b:
                a, b = b, a
            for factor in FACTORS:
                sa, sb = scores[a].get(factor), scores[b].get(factor)
                if sa is None or sb is None or sa == sb:
                    continue
                records.append((a, b, factor, a if sa > sb else b))

        batch_size = 500
        for i in range(0, len(records), batch_size):
            for a_id, b_id, factor, preferred_id in records[i:i+batch_size]:
                stmt = (
                    insert(PairwiseRating)
                    .values(user_id=user_id, profile_id=profile_id,
                            flat_a_id=a_id, flat_b_id=b_id,
                            factor=factor, preferred_flat_id=preferred_id)
                    .on_conflict_do_nothing(constraint="uix_pairwise")
                )
                await session.execute(stmt)
            await session.commit()

    return SynthResult(n_duels=len(selected), n_records=len(records))


# ──────────────────────────────────────────────
# Stage B
# ──────────────────────────────────────────────

def _train_loop_b(
    model,
    scaler,
    bundle,
    epochs,
    lr,
    device,
    weight_decay: float = 1e-2,
) -> float:
    """Synchronous Stage B fine-tuning loop. AdamW + weight_decay."""
    ta = scaler.transform(bundle.tab_a)
    tb = scaler.transform(bundle.tab_b)
    ca = torch.from_numpy(bundle.clip_a).float().to(device)
    cb = torch.from_numpy(bundle.clip_b).float().to(device)
    tta = torch.from_numpy(ta).float().to(device)
    ttb = torch.from_numpy(tb).float().to(device)
    fi = torch.from_numpy(bundle.factor_idx).long().to(device)
    pref = torch.from_numpy(bundle.prefer_a).bool().to(device)

    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    model.train()
    last = 0.0
    for _ in range(epochs):
        opt.zero_grad()
        b_a, pq_a, d_a = model(ca, tta)
        b_b, pq_b, d_b = model(cb, ttb)
        loss = pairwise_factor_loss(b_a, pq_a, d_a, b_b, pq_b, d_b, fi, pref)
        loss.backward()
        opt.step()
        last = float(loss.detach().cpu())
    return last


async def run_stage_b(
    profile_id: int,
    epochs: int = 30,
    lr: float = 1e-4,
    device: str | None = None,
    progress: ProgressCb | None = None,
) -> StageBResult:
    dev = device or ("cuda" if torch.cuda.is_available() else "cpu")

    if progress:
        await progress(f"🔬 Stage B: загружаю пары и модель...")

    s3 = S3Client()
    async with async_session() as session:
        snap_r = await session.execute(
            select(ModelSnapshot)
            .where(ModelSnapshot.profile_id == profile_id)
            .order_by(ModelSnapshot.created_at.desc())
            .limit(1)
        )
        snap = snap_r.scalar_one_or_none()
        if not snap:
            raise ValueError("Нет сохранённого снапшота Stage A. Сначала обучите Stage A.")

        raw = await s3.download_bytes(s3_uri=snap.storage_uri)
        bundle = await load_pairwise_bundle(session, profile_id)

    if bundle.clip_a.shape[0] == 0:
        raise ValueError("Нет пар для Stage B. Сначала запустите синтез дуэлей.")

    if progress:
        await progress(f"⚡️ Stage B: дообучение {epochs} эпох на {bundle.clip_a.shape[0]} парах...")

    pkg = load_checkpoint_bytes(raw)
    model, scaler = build_model_from_package(pkg)
    model = model.to(dev)

    loss = await asyncio.to_thread(_train_loop_b, model, scaler, bundle, epochs, lr, dev)

    if progress:
        await progress("💾 Сохраняю модель Stage B в S3...")

    buf = io.BytesIO()
    out = {
        "model_state": model.cpu().state_dict(),
        "scaler": pkg["scaler"],
        "tab_dim": int(pkg.get("tab_dim", 6)),
        "clip_tag": pkg.get("clip_tag", CLIP_MODEL_TAG),
        "stage": "b",
        "pairwise_train_loss": loss,
    }
    torch.save(out, buf)
    ts = int(time.time())
    key = f"models/{profile_id}/stage_b_{ts}.pt"
    uri = await s3.upload_bytes(buf.getvalue(), key, content_type="application/octet-stream")

    async with async_session() as session:
        snap_b = ModelSnapshot(
            profile_id=profile_id,
            backbone=CLIP_MODEL_TAG,
            head_type="PreferenceMLP_stage_b",
            storage_uri=uri,
            metrics={"pairwise_train_loss": loss},
        )
        session.add(snap_b)
        await session.flush()
        await session.execute(
            update(Profile).where(Profile.id == profile_id).values(last_trained_snapshot_id=snap_b.id)
        )
        await session.commit()
        snap_b_id = snap_b.id

    return StageBResult(train_loss=loss, snapshot_id=snap_b_id)


# ──────────────────────────────────────────────
# Full pipeline
# ──────────────────────────────────────────────

async def run_full_pipeline(
    profile_id: int,
    stage_a_epochs: int = 80,
    stage_a_lr: float = 1e-3,
    n_synth_duels: int = 250,
    stage_b_epochs: int = 30,
    stage_b_lr: float = 1e-4,
    device: str | None = None,
    progress: ProgressCb | None = None,
) -> PipelineResult:
    stage_a = await run_stage_a(profile_id, stage_a_epochs, stage_a_lr, device, progress)
    synth = await synthesize_pairwise(profile_id, n_synth_duels, progress)

    stage_b = await run_stage_b(profile_id, stage_b_epochs, stage_b_lr, device, progress)

    if progress:
        await progress("📊 Скоринг всех квартир...")

    s3 = S3Client()
    async with async_session() as session:
        scored = await svc_score_flats(session, s3, profile_id, device=device)

    return PipelineResult(stage_a=stage_a, synth=synth, stage_b=stage_b, scored_count=scored)
