"""
Optional Celery app for background ML jobs.

Run worker from repo root:
  celery -A celery_app worker --loglevel=info

Trigger training (after installing requirements-ml.txt + celery):
  celery -A celery_app call pars.train_stage_a --kwargs='{"profile_id": 1}'
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from celery import Celery

ROOT = Path(__file__).resolve().parent
BROKER = os.environ.get("CELERY_BROKER_URL") or os.environ.get("REDIS_URL", "redis://localhost:6379/1")

app = Celery("pars", broker=BROKER, backend=BROKER)


def _run_module(module: str, profile_id: int) -> int:
    return subprocess.call(
        [sys.executable, "-m", module, "--profile-id", str(profile_id)],
        cwd=str(ROOT),
    )


@app.task(name="pars.train_stage_a")
def train_stage_a(profile_id: int) -> int:
    return _run_module("scripts.ml.train_preference_stage_a", profile_id)


@app.task(name="pars.train_stage_b")
def train_stage_b(profile_id: int) -> int:
    return _run_module("scripts.ml.train_preference_stage_b", profile_id)


@app.task(name="pars.score_profile")
def score_profile(profile_id: int) -> int:
    return _run_module("scripts.ml.score_profile_flats", profile_id)
