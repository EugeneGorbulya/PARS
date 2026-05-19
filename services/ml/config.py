"""Shared ML hyperparameters and names."""

CLIP_MODEL_ARCH = "ViT-B-32"
CLIP_PRETRAINED = "openai"
CLIP_MODEL_TAG = f"{CLIP_MODEL_ARCH}-{CLIP_PRETRAINED}"

# ViT-B-32 image embedding dimension (OpenAI / LAION checkpoints)
CLIP_IMAGE_DIM = 512

# Tabular numeric features (order fixed for model + preprocess.json)
TABULAR_FEATURE_KEYS = (
    "log_price",
    "area_sqm",
    "rooms",
    "floor",
    "floors_total",
    "log_travel_min",
)

SENTINEL_TRAVEL_MIN = 999.0

# Доля последних по времени оценок, уходящих в validation.
# Используется одновременно в dataset.py (Stage A split),
# pipeline.synthesize_pairwise / scripts.ml.synthesize_pairwise (отсечение val
# из пар синтеза, чтобы Stage B не видел val-квартиры),
# и scripts.eval.eval_baselines (тот же сплит при оценке).
VAL_RATIO = 0.15
