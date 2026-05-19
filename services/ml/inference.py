"""Load checkpoint from S3 / bytes, run inference, write profile_flat_score."""
from __future__ import annotations

import io
from typing import Any, Dict, List, Tuple

import numpy as np
import torch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Flat, ModelSnapshot, Profile, ProfileFlatScore
from services.ml.candidates import flat_ids_for_profile_filters
from services.ml.dataset import _mean_clip_by_flat, _min_travel_by_flat, _tabular_row
from services.ml.model import PreferenceModel
from services.ml.preprocess import TabularScaler
from services.s3.client import S3Client


def load_checkpoint_bytes(data: bytes) -> Dict[str, Any]:
    buf = io.BytesIO(data)
    try:
        return torch.load(buf, map_location="cpu", weights_only=False)
    except TypeError:
        buf.seek(0)
        return torch.load(buf, map_location="cpu")


def build_model_from_package(pkg: Dict[str, Any]) -> Tuple[PreferenceModel, TabularScaler]:
    tab_dim = int(pkg.get("tab_dim", 6))
    scaler = TabularScaler.from_state_dict(pkg["scaler"])
    model = PreferenceModel(tab_dim=tab_dim)
    model.load_state_dict(pkg["model_state"])
    model.eval()
    return model, scaler


def predict_batch(
    model: PreferenceModel,
    scaler: TabularScaler,
    clip: np.ndarray,
    tab: np.ndarray,
    device: str,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    tab_n = scaler.transform(tab)
    x_c = torch.from_numpy(clip).float().to(device)
    x_t = torch.from_numpy(tab_n).float().to(device)
    with torch.no_grad():
        b, pq, d = model(x_c, x_t)
    return b.cpu().numpy(), pq.cpu().numpy(), d.cpu().numpy()


async def _batch_features(
    session: AsyncSession,
    profile_id: int,
    flat_ids: List[int],
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], List[int]]:
    if not flat_ids:
        return None, None, []
    clip_map = await _mean_clip_by_flat(session, flat_ids)
    travel_map = await _min_travel_by_flat(session, profile_id, flat_ids)
    fr = await session.execute(select(Flat).where(Flat.id.in_(flat_ids)))
    flats = {f.id: f for f in fr.scalars().all()}
    clip_rows = []
    tab_rows = []
    ordered_ids: List[int] = []
    for fid in flat_ids:
        f = flats.get(fid)
        if f is None:
            continue
        v = clip_map.get(fid)
        if v is None:
            continue
        clip_rows.append(v.astype(np.float32))
        tab_rows.append(_tabular_row(f, travel_map.get(fid, 999.0)))
        ordered_ids.append(fid)
    if not clip_rows:
        return None, None, []
    return np.stack(clip_rows, axis=0), np.stack(tab_rows, axis=0), ordered_ids


async def score_profile_flats(
    session: AsyncSession,
    s3: S3Client,
    profile_id: int,
    *,
    device: str | None = None,
    batch_size: int = 256,
    snapshot_id: int | None = None,
) -> int:
    dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
    if snapshot_id is not None:
        snap_r = await session.execute(select(ModelSnapshot).where(ModelSnapshot.id == snapshot_id))
    else:
        snap_r = await session.execute(
            select(ModelSnapshot)
            .where(ModelSnapshot.profile_id == profile_id)
            .order_by(ModelSnapshot.created_at.desc())
            .limit(1)
        )
    snap = snap_r.scalar_one_or_none()
    if not snap:
        return 0
    raw = await s3.download_bytes(s3_uri=snap.storage_uri)
    pkg = load_checkpoint_bytes(raw)
    model, scaler = build_model_from_package(pkg)

    pr = (await session.execute(select(Profile).where(Profile.id == profile_id))).scalar_one_or_none()
    if not pr:
        return 0
    wb = float(pr.weight_beauty)
    wpq = float(pr.weight_price_quality)
    wd = float(pr.weight_distance)

    flat_ids = await flat_ids_for_profile_filters(session, profile_id)
    written = 0
    for i in range(0, len(flat_ids), batch_size):
        chunk = flat_ids[i : i + batch_size]
        clip, tab, ordered_ids = await _batch_features(session, profile_id, chunk)
        if clip is None or not ordered_ids:
            continue
        bh, pqh, dh = predict_batch(model, scaler, clip, tab, dev)
        score_vec = wb * bh + wpq * pqh + wd * dh
        for j, fid in enumerate(ordered_ids):
            row = ProfileFlatScore(
                profile_id=profile_id,
                flat_id=fid,
                score=float(score_vec[j]),
                beauty_hat=float(bh[j]),
                price_quality_hat=float(pqh[j]),
                distance_hat=float(dh[j]),
            )
            await session.merge(row)
            written += 1
    await session.commit()
    return written
