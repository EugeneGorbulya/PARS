#!/bin/bash

# Скрипт для запуска проекта PARS
set -e

echo "🚀 Запуск проекта PARS"
echo "===================="
echo ""

# Цвета для вывода
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Проверка .env файла
if [ ! -f .env ]; then
    echo -e "${RED}❌ Файл .env не найден!${NC}"
    echo "Создайте файл .env на основе примера в README.md"
    exit 1
fi
echo -e "${GREEN}✓${NC} Файл .env найден"

# Проверка Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python3 не найден!${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} Python3 найден: $(python3 --version)"

# Проверка Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker не найден!${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} Docker найден"

if ! command -v docker compose &> /dev/null && ! command -v docker-compose &> /dev/null; then
    echo -e "${RED}❌ docker compose не найден!${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} docker compose найден"

# Установка зависимостей Python
echo ""
echo -e "${YELLOW}📦 Установка зависимостей Python...${NC}"
python3 -m pip install -q -r requirements.txt || {
    echo -e "${YELLOW}⚠️  Предупреждение: некоторые зависимости могут быть не установлены${NC}"
}
echo -e "${GREEN}✓${NC} Зависимости установлены"

# Запуск Docker контейнеров
echo ""
echo -e "${YELLOW}🐳 Запуск Docker контейнеров...${NC}"
docker compose up -d
echo -e "${GREEN}✓${NC} Контейнеры запущены"

# Ожидание готовности PostgreSQL
echo ""
echo -e "${YELLOW}⏳ Ожидание готовности PostgreSQL...${NC}"
for i in {1..30}; do
    if docker compose exec -T db pg_isready -U postgres > /dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} PostgreSQL готов"
        break
    fi
    if [ $i -eq 30 ]; then
        echo -e "${RED}❌ PostgreSQL не запустился за 30 секунд${NC}"
        exit 1
    fi
    sleep 1
done

# Проверка и создание MinIO bucket
echo ""
echo -e "${YELLOW}📦 Проверка MinIO bucket...${NC}"
if ! docker compose exec -T minio mc ls myminio/smartrent-media > /dev/null 2>&1; then
    echo "Создание bucket smartrent-media..."
    docker compose exec -T minio mc alias set myminio http://localhost:9000 minioadmin minioadmin
    docker compose exec -T minio mc mb myminio/smartrent-media || true
    docker compose exec -T minio mc anonymous set download myminio/smartrent-media
    echo -e "${GREEN}✓${NC} Bucket создан"
else
    echo -e "${GREEN}✓${NC} Bucket уже существует"
fi

# Применение миграций
echo ""
echo -e "${YELLOW}🔄 Применение миграций БД...${NC}"
python3 -m alembic upgrade head
echo -e "${GREEN}✓${NC} Миграции применены"

# Проверка BOT_TOKEN
echo ""
if grep -q "BOT_TOKEN=$" .env || grep -q "BOT_TOKEN=your_bot_token" .env; then
    echo -e "${YELLOW}⚠️  BOT_TOKEN не установлен в .env${NC}"
    echo "Бот не запустится без токена!"
else
    echo -e "${GREEN}✓${NC} BOT_TOKEN найден в .env"
fi

echo ""
echo -e "${GREEN}✅ Инфраструктура готова!${NC}"
echo ""
echo "Теперь вы можете запустить:"
echo ""
echo "1. Telegram бот (в первом терминале):"
echo "   python3 bot/main.py"
echo ""
echo "2. Воркер для изображений (во втором терминале, опционально):"
echo "   python3 services/worker_images.py"
echo ""
echo "Проверить статус контейнеров:"
echo "   docker compose ps"
echo ""
echo "Остановить контейнеры:"
echo "   docker compose down"
echo ""

