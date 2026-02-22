# PARS
PARS: Personalized Apartment Ranking System (Система персонализированного ранжирования квартир)

## 🚀 Запуск проекта

### Первый запуск

Выполните команды последовательно:

```bash
# 1. Установка зависимостей
python3 -m pip install -r requirements.txt

# Если возникнут проблемы с cianparser, установите его отдельно:
python3 -m pip install cianparser

# 2. Настройка .env файла (см. раздел "Настройка окружения" ниже)

# 3. Запуск инфраструктуры (PostgreSQL, Redis, MinIO)
docker compose up -d

# 4. Создание bucket в MinIO (только при первом запуске)
docker compose exec minio mc alias set myminio http://localhost:9000 minioadmin minioadmin
docker compose exec minio mc mb myminio/smartrent-media
docker compose exec minio mc anonymous set download myminio/smartrent-media

# 5. Применение миграций БД (только при первом запуске или после изменений схемы)
python3 -m alembic upgrade head

# 6. Запуск Telegram бота (в первом терминале)
python3 bot/main.py

# 7. Запуск воркера для изображений (во втором терминале, опционально)
python3 services/worker_images.py
```

### Обычный запуск (после первого раза)

```bash
# 1. Запуск инфраструктуры
docker compose up -d

# 2. Запуск Telegram бота (в первом терминале)
python3 bot/main.py

# 3. Запуск воркера для изображений (во втором терминале, опционально)
python3 services/worker_images.py
```

**Примечание:** Миграции применяются автоматически при изменении схемы БД. Bucket создается один раз и сохраняется в volume.

## Быстрый старт

### 1. Установка зависимостей

```bash
python3 -m pip install -r requirements.txt
```

### 2. Настройка окружения

Создайте файл `.env` в корне проекта:

```bash
# Database
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_HOST=localhost
POSTGRES_PORT=5433
POSTGRES_DB=smartrent
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/smartrent

# Redis
REDIS_URL=redis://localhost:6379/0

# S3 / MinIO
S3_ENDPOINT=http://localhost:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET=smartrent-media

# Telegram Bot
BOT_TOKEN=your_bot_token_here

# Geo Provider (mock or google or auto or here)
GEO_PROVIDER=mock
GOOGLE_MAPS_API_KEY=your_google_key
HERE_API_KEY=your_here_key
```

### 3. Запуск инфраструктуры (Docker)

Запустите PostgreSQL, Redis и MinIO:

```bash
docker compose up -d
```

Проверьте статус контейнеров:

```bash
docker compose ps
```

**Создание bucket в MinIO:**

После первого запуска создайте bucket для хранения медиа:

```bash
docker compose exec minio mc alias set myminio http://localhost:9000 minioadmin minioadmin
docker compose exec minio mc mb myminio/smartrent-media
docker compose exec minio mc anonymous set download myminio/smartrent-media
```

**Доступ к MinIO Console:**
- URL: http://localhost:9001
- Логин: `minioadmin`
- Пароль: `minioadmin`

### 4. Применение миграций БД

```bash
python3 -m alembic upgrade head
```

### 5. Запуск Telegram бота

```bash
python3 bot/main.py
```

Бот начнет принимать сообщения через polling.

### 6. Запуск воркера для загрузки изображений (опционально)

В отдельном терминале:

```bash
python3 services/worker_images.py
```

Воркер будет периодически загружать изображения квартир в S3.

## Полезные скрипты

### Тестирование геосервиса

```bash
python3 scripts/test_geo.py
```

### Проверка подключения к S3

```bash
python3 scripts/check_s3.py
```

### Тестирование парсера Циан

```bash
python3 scripts/test_fetcher.py
```

### Очистка всех данных

```bash
python3 scripts/clean_all.py
```

### Сброс системы

```bash
python3 scripts/reset_system.py
```

## Структура проекта

```
PARS/
├── bot/                    # Telegram бот
│   ├── handlers/          # Обработчики сообщений
│   ├── keyboards/         # Клавиатуры
│   └── main.py            # Точка входа бота
├── core/                   # Ядро приложения
│   ├── config.py          # Конфигурация
│   └── session.py        # Сессии БД
├── models/                 # SQLAlchemy модели
├── services/               # Бизнес-логика
│   ├── cian_parser/      # Парсер Циан
│   ├── geo/               # Геосервисы
│   ├── image_downloader/  # Загрузка изображений
│   ├── recommendation/    # Рекомендации
│   └── s3/                # S3 клиент
├── scripts/                # Вспомогательные скрипты
├── migrations/             # Миграции БД (Alembic)
├── docker-compose.yml      # Docker конфигурация
└── requirements.txt        # Зависимости Python
```

## Остановка сервисов

Остановить все Docker контейнеры:

```bash
docker compose down
```

Остановить и удалить данные:

```bash
docker compose down -v
```

## Переменные окружения

| Переменная | Описание | По умолчанию |
|-----------|----------|--------------|
| `POSTGRES_HOST` | Хост PostgreSQL | `localhost` |
| `POSTGRES_PORT` | Порт PostgreSQL | `5432` |
| `POSTGRES_DB` | Имя базы данных | `smartrent` |
| `POSTGRES_USER` | Пользователь БД | `postgres` |
| `POSTGRES_PASSWORD` | Пароль БД | `postgres` |
| `REDIS_URL` | URL Redis | `redis://localhost:6379/0` |
| `S3_ENDPOINT` | Endpoint MinIO/S3 | `http://localhost:9000` |
| `S3_ACCESS_KEY` | Access key для S3 | `minioadmin` |
| `S3_SECRET_KEY` | Secret key для S3 | `minioadmin` |
| `S3_BUCKET` | Имя bucket | `smartrent-media` |
| `BOT_TOKEN` | Токен Telegram бота | - |
| `GEO_PROVIDER` | Провайдер геосервиса (`mock` или `google`) | `mock` |
| `GOOGLE_MAPS_API_KEY` | API ключ Google Maps | - |

## Геосервисы

Проект поддерживает два провайдера для расчета времени в пути:

- **mock** (по умолчанию) - возвращает случайные значения для тестирования
- **google** - использует Google Maps Directions API с поддержкой общественного транспорта

Для использования Google Maps установите:
```bash
GEO_PROVIDER=google
GOOGLE_MAPS_API_KEY=your_api_key_here
```

Подробнее см. `services/geo/provider.py`
