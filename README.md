# PARS — Personalized Apartment Ranking System

Personalized apartment recommendation system: Telegram bot for collecting user
ratings, a multi-task ML model on top of CLIP image embeddings and tabular
features, and an end-to-end training & ranking pipeline. Built for the BPADS
software project course at HSE.

For the full design and evaluation, see
[`.cursor/course_work_this_year/main.tex`](.cursor/course_work_this_year/main.tex).
For evaluation numbers and history,
[`final_metrics.md`](final_metrics.md) and [`eval_history.md`](eval_history.md).

---

## Architecture in one paragraph

A Telegram bot (`aiogram` 3) is the only user-facing surface. It talks to
PostgreSQL directly via async SQLAlchemy and to MinIO/S3 for photos and model
checkpoints. CIAN listings are fetched into the DB by the parser/fetcher
service; photos are downloaded into MinIO and embedded with frozen
CLIP ViT-B/32. The `PreferenceModel` (CLIP + tabular MLP, three cross-coupled
heads) is trained in two stages — pointwise regression and Bradley–Terry
pairwise fine-tuning — and serves the `/top` command via a precomputed
`profile_flat_scores` cache. Redis + an (optional) Celery worker handle
background ML jobs.

---

## Prerequisites

- Python **3.12**
- Docker + Docker Compose (for PostgreSQL, Redis, MinIO, and the bot image)
- A Telegram bot token (`@BotFather`)
- Optional: a 2GIS Public Transport API key (without it, the geo provider
  falls back to a deterministic mock)

---

## Quick start (Docker, all-in-one)

```bash
git clone <repo> && cd PARS
cp .env.example .env
# edit .env: set BOT_TOKEN, optionally DGIS_API_KEY

docker compose up -d db redis minio       # infrastructure
docker compose run --rm migrate           # apply Alembic migrations
docker compose up -d bot                  # start the Telegram bot
docker compose logs -f bot                # tail bot logs
```

The bot will start polling Telegram and is ready to serve `/start`,
`/new_profile`, `/next`, `/duel`, `/train`, `/top`, `/weights`,
`/publish`/`/fork`/`/browse_profiles` etc.

To stop:

```bash
docker compose down                       # keep volumes
docker compose down -v                    # also wipe DB & MinIO data
```

---

## Local development (without containerised bot)

Useful when iterating on bot code: run the infrastructure in Docker, the bot
on the host.

```bash
docker compose up -d db redis minio
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-ml.txt

cp .env.example .env                       # edit BOT_TOKEN, etc.
alembic upgrade head                       # create / migrate schema
python -m bot.main                         # start the bot
```

---

## Ingesting data

Fetch listings, download photos, compute embeddings, and pre-fill travel
times for a profile:

```bash
python -m scripts.ingest.fetch_100_flats
python -m scripts.ingest.download_photos --all --batch 100
python -m scripts.ingest.compute_clip_embeddings
python -m scripts.ingest.enrich_profile_travel --profile-id 1
```

> By default `CIAN_FETCH_ENABLED=false` in `.env`, which disables the
> automatic background fetch that would otherwise top up the rating buffer
> when it runs low. Set it to `true` for a self-replenishing deployment.

---

## ML pipeline

The full pipeline (Stage A regression → Stage B pairwise fine-tuning →
scoring) runs directly from the bot via the `/train` command. Same thing
from the CLI:

```bash
python -m scripts.ml.train_preference_stage_a --profile-id 2 --epochs 80
python -m scripts.ml.train_preference_stage_b --profile-id 2 --epochs 30
python -m scripts.ml.score_profile_flats     --profile-id 2
```

Tune the per-factor weights without retraining:

```bash
# in the bot:  /weights 0.55 0.15 0.30
```

(This issues a single SQL `UPDATE` against `profile_flat_scores` using the
stored per-head predictions.)

---

## Evaluation

```bash
python -m scripts.eval.eval_baselines --profile-id 2          # honest val split
python -m scripts.eval.eval_baselines --profile-id 2 --all-data
python -m scripts.eval.eval_baselines --profile-id 2 --weights 0.55,0.15,0.30
python -m scripts.eval.show_top_flats --profile-id 2 --top 20
```

Regenerate the figures embedded in the course-work report:

```bash
python -m scripts.eval.make_report_plots --profile-id 2 --epochs 80
python -m scripts.eval.make_db_schema
```

---

## Repository layout

```
bot/                Telegram bot (aiogram 3): handlers, FSM states, keyboards
core/               config + session factory
models/             SQLAlchemy models (one file per table)
migrations/         Alembic migrations
services/           ML (model, dataset, pipeline, inference, CLIP),
                    CIAN parser, geo provider, S3 client, image downloader,
                    recommendation, flat_enricher
scripts/
    ops/            db/s3 inspection, system reset, overfit diagnostics
    ingest/         CIAN parsing, photo download, CLIP embeddings, travel times
    ml/             stage A / stage B training, scoring, pairwise synthesis
    eval/           offline evaluation, top-K viewer, report plots, schema diagram
    tests/          ad-hoc tests for the fetcher and geo provider
celery_app.py       optional Celery wrapper around the ML scripts
docker-compose.yml  PostgreSQL + Redis + MinIO + bot + one-shot migrate
Dockerfile          single image: bot + CLI scripts
requirements*.txt   runtime and ML dependencies
```

---

## Optional: Celery worker

The `/train` command runs the ML pipeline in-process via `asyncio`. For
heavier workloads or horizontal scaling, the same training jobs can be
dispatched to a Celery worker:

```bash
# inside a venv with requirements*.txt installed:
celery -A celery_app worker --loglevel=info
celery -A celery_app call pars.train_stage_a --kwargs='{"profile_id": 2}'
```

The worker is **not** part of `docker-compose.yml` by default because the
bot's in-process pipeline is sufficient for a single-user deployment.

---

## Database snapshot

The state used for the reported evaluation (2,241 flats, 37,048 photos,
320 fully-rated apartments on `profile_id = 2`, trained model snapshots in
MinIO) is reproducible from a `pg_dump -F c` dump and the corresponding
MinIO bucket. Restore with:

```bash
pg_restore -h localhost -p 5433 -U postgres -d smartrent dumps/pars.dump
mc cp -r dumps/minio/ s3/smartrent-media/   # or aws s3 cp
```
