"""
Fine-tune preference model on pairwise_ratings (logistic margin on selected head).
Loads latest Stage-A snapshot from S3 unless --from-uri is set.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import os
import sys
import time

sys.path.insert(0, os.getcwd())

import torch
from sqlalchemy import select, update

from core.session import async_session
from models import ModelSnapshot, Profile
from services.ml.config import CLIP_MODEL_TAG
from services.ml.dataset_pairwise import load_pairwise_bundle
from services.ml.inference import build_model_from_package, load_checkpoint_bytes
from services.ml.model import PreferenceModel, pairwise_factor_loss
from services.ml.preprocess import TabularScaler
from services.s3.client import S3Client


async def _load_latest_pkg(session, profile_id: int) -> bytes:
    r = await session.execute(
        select(ModelSnapshot)
        .where(ModelSnapshot.profile_id == profile_id)
        .order_by(ModelSnapshot.created_at.desc())
        .limit(1)
    )
    snap = r.scalar_one_or_none()
    if not snap:
        raise SystemExit("No model_snapshots for profile; run train_preference_stage_a first.")
    s3 = S3Client()
    return await s3.download_bytes(s3_uri=snap.storage_uri)


def _train_pairwise(
    model: PreferenceModel,
    scaler: TabularScaler,
    bundle,
    epochs: int,
    lr: float,
    device: str,
) -> float:
    if bundle.clip_a.shape[0] == 0:
        raise SystemExit("No pairwise rows with CLIP vectors for this profile.")
    ta = scaler.transform(bundle.tab_a)
    tb = scaler.transform(bundle.tab_b)
    ca = torch.from_numpy(bundle.clip_a).float().to(device)
    cb = torch.from_numpy(bundle.clip_b).float().to(device)
    tta = torch.from_numpy(ta).float().to(device)
    ttb = torch.from_numpy(tb).float().to(device)
    fi = torch.from_numpy(bundle.factor_idx).long().to(device)
    pref = torch.from_numpy(bundle.prefer_a).bool().to(device)

    opt = torch.optim.Adam(model.parameters(), lr=lr)
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


async def async_main(profile_id: int, epochs: int, lr: float, device: str | None, from_uri: str | None):
    dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
    s3 = S3Client()
    async with async_session() as session:
        if from_uri:
            raw = await s3.download_bytes(s3_uri=from_uri)
        else:
            raw = await _load_latest_pkg(session, profile_id)
        bundle = await load_pairwise_bundle(session, profile_id)

    pkg = load_checkpoint_bytes(raw)
    model, scaler = build_model_from_package(pkg)
    model = model.to(dev)
    loss = _train_pairwise(model, scaler, bundle, epochs, lr, dev)

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
        snap = ModelSnapshot(
            profile_id=profile_id,
            backbone=CLIP_MODEL_TAG,
            head_type="PreferenceMLP_stage_b",
            storage_uri=uri,
            metrics={"pairwise_train_loss": loss},
        )
        session.add(snap)
        await session.flush()
        await session.execute(
            update(Profile)
            .where(Profile.id == profile_id)
            .values(last_trained_snapshot_id=snap.id)
        )
        await session.commit()
        print("Saved snapshot", snap.id, uri)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile-id", type=int, required=True)
    p.add_argument("--epochs", type=int, default=40)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--device", type=str, default=None)
    p.add_argument("--from-uri", type=str, default=None, help="S3 URI of checkpoint to warm-start from")
    args = p.parse_args()
    asyncio.run(async_main(args.profile_id, args.epochs, args.lr, args.device, args.from_uri))


if __name__ == "__main__":
    main()
