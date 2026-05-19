"""
Load OpenCLIP once and encode RGB images to L2-normalized float32 vectors (512-d for ViT-B-32).
"""
from __future__ import annotations

import io
from typing import List

import numpy as np
import torch
from PIL import Image

from services.ml.config import CLIP_IMAGE_DIM, CLIP_MODEL_ARCH, CLIP_PRETRAINED, CLIP_MODEL_TAG


class ClipImageEmbedder:
    _instance: "ClipImageEmbedder | None" = None

    def __init__(self, device: str | None = None):
        import open_clip

        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        model, _, preprocess = open_clip.create_model_and_transforms(
            CLIP_MODEL_ARCH,
            pretrained=CLIP_PRETRAINED,
            device=self.device,
        )
        self.model = model.eval()
        self.preprocess = preprocess
        self.dim = CLIP_IMAGE_DIM
        self.model_tag = CLIP_MODEL_TAG

    @classmethod
    def get(cls, device: str | None = None) -> "ClipImageEmbedder":
        if cls._instance is None:
            cls._instance = ClipImageEmbedder(device=device)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def encode_jpeg_bytes_batch(self, images_jpeg: List[bytes]) -> np.ndarray:
        tensors = []
        for raw in images_jpeg:
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            tensors.append(self.preprocess(img))
        batch = torch.stack(tensors, dim=0).to(self.device)
        with torch.no_grad():
            emb = self.model.encode_image(batch)
            emb = emb / emb.norm(dim=-1, keepdim=True)
        return emb.float().cpu().numpy()
