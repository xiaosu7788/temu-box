#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -f .env.docker ]]; then
  cp .env.docker.example .env.docker
fi

if grep -q '^POSTGRES_PASSWORD=CHANGE_ME' .env.docker; then
  password="$(openssl rand -hex 24)"
  sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${password}/" .env.docker
  echo "已生成 .env.docker 和随机数据库密码。"
fi

mkdir -p data/inventories data/tasks data/activities data/logs
if grep -q '^DATABASE_URL=.' .env.docker; then
  docker compose --env-file .env.docker --profile internal-db stop db >/dev/null 2>&1 || true
  docker compose --env-file .env.docker up -d --build backend frontend
else
  docker compose --env-file .env.docker --profile internal-db up -d --build --wait db
  docker compose --env-file .env.docker up -d --build backend frontend
fi
docker compose --env-file .env.docker --profile internal-db ps
echo "访问地址：http://127.0.0.1:$(grep '^WEB_PORT=' .env.docker | cut -d= -f2)"
