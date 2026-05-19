"""
Multi-task preference model: CLIP image embedding + tabular MLP → three scalar heads (1–5 via sigmoid).
"""
from __future__ import annotations

from typing import Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F

from services.ml.config import CLIP_IMAGE_DIM


class PreferenceModel(nn.Module):
    def __init__(
        self,
        tab_dim: int = 6,
        hidden: int = 128,
        clip_dim: int = CLIP_IMAGE_DIM,
        price_feat_idx: int = 0,
        travel_feat_idx: int | None = None,
    ):
        super().__init__()
        self.tab_dim = tab_dim
        self.price_feat_idx = price_feat_idx
        # По умолчанию травел-фича — последняя в табличке (TABULAR_FEATURE_KEYS).
        self.travel_feat_idx = (
            travel_feat_idx if travel_feat_idx is not None else (tab_dim - 1)
        )
        self.clip_in = nn.Linear(clip_dim, hidden)
        self.tab_in = nn.Sequential(
            nn.Linear(tab_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
        )
        fused = hidden * 2
        self.fuse = nn.Sequential(nn.Linear(fused, hidden), nn.ReLU())
        self.head_b = nn.Linear(hidden, 1)
        # Кросс-голова pq: общий h + предсказанный beauty (сырой logit) + log_price.
        # beauty в pq передаётся через detach: pq не должна тянуть градиент в beauty,
        # иначе beauty начнёт «подгоняться» под удобство pq-головы.
        self.head_pq = nn.Linear(hidden + 2, 1)
        # Кросс-голова distance: общий h + log_travel.
        self.head_d = nn.Linear(hidden + 1, 1)

    def forward(self, clip: torch.Tensor, tab: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        a = torch.relu(self.clip_in(clip))
        b = self.tab_in(tab)
        h = self.fuse(torch.cat([a, b], dim=-1))

        b_raw = self.head_b(h).squeeze(-1)

        log_price = tab[:, self.price_feat_idx : self.price_feat_idx + 1]
        log_travel = tab[:, self.travel_feat_idx : self.travel_feat_idx + 1]

        pq_input = torch.cat([h, b_raw.detach().unsqueeze(-1), log_price], dim=-1)
        pq_raw = self.head_pq(pq_input).squeeze(-1)

        d_input = torch.cat([h, log_travel], dim=-1)
        d_raw = self.head_d(d_input).squeeze(-1)

        beauty = 1.0 + 4.0 * torch.sigmoid(b_raw)
        pq = 1.0 + 4.0 * torch.sigmoid(pq_raw)
        dist = 1.0 + 4.0 * torch.sigmoid(d_raw)
        return beauty, pq, dist


def masked_mse(
    pred: torch.Tensor,
    target: torch.Tensor,
    mask: torch.Tensor,
) -> torch.Tensor:
    if mask.sum() == 0:
        return pred.sum() * 0.0
    diff = (pred - target) ** 2
    return (diff * mask.float()).sum() / mask.float().sum()


def multi_task_loss(
    pred_b: torch.Tensor,
    pred_pq: torch.Tensor,
    pred_d: torch.Tensor,
    y_b: torch.Tensor,
    y_pq: torch.Tensor,
    y_d: torch.Tensor,
    m_b: torch.Tensor,
    m_pq: torch.Tensor,
    m_d: torch.Tensor,
    weights: Tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> torch.Tensor:
    wb, wp, wd = weights
    lb = masked_mse(pred_b, y_b, m_b)
    lpq = masked_mse(pred_pq, y_pq, m_pq)
    ld = masked_mse(pred_d, y_d, m_d)
    return wb * lb + wp * lpq + wd * ld


def pairwise_logistic_loss(
    pred_w: torch.Tensor,
    pred_l: torch.Tensor,
) -> torch.Tensor:
    """Preferred flat should score higher: softplus(-(s_w - s_l))."""
    margin = pred_w - pred_l
    return F.softplus(-margin).mean()


def pairwise_factor_loss(
    pred_b_a: torch.Tensor,
    pred_pq_a: torch.Tensor,
    pred_d_a: torch.Tensor,
    pred_b_b: torch.Tensor,
    pred_pq_b: torch.Tensor,
    pred_d_b: torch.Tensor,
    factor_idx: torch.Tensor,
    prefer_a: torch.Tensor,
) -> torch.Tensor:
    """For each pair pick head by factor_idx; preferred side should score higher."""
    pa = torch.stack([pred_b_a, pred_pq_a, pred_d_a], dim=-1)
    pb = torch.stack([pred_b_b, pred_pq_b, pred_d_b], dim=-1)
    idx = factor_idx.long().unsqueeze(1)
    s_a = pa.gather(1, idx).squeeze(1)
    s_b = pb.gather(1, idx).squeeze(1)
    pa_mask = prefer_a.float()
    s_w = pa_mask * s_a + (1.0 - pa_mask) * s_b
    s_l = pa_mask * s_b + (1.0 - pa_mask) * s_a
    return pairwise_logistic_loss(s_w, s_l)
