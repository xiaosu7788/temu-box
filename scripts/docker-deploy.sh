#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
admin_password_created=0

if [[ ! -f .env.docker ]]; then
  cp .env.docker.example .env.docker
fi

if grep -q '^POSTGRES_PASSWORD=CHANGE_ME' .env.docker; then
  sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" .env.docker
fi
if grep -q '^ADMIN_PASSWORD=CHANGE_ME' .env.docker; then
  sed -i "s/^ADMIN_PASSWORD=.*/ADMIN_PASSWORD=$(openssl rand -hex 12)/" .env.docker
  admin_password_created=1
fi
if grep -q '^AUTH_SECRET=CHANGE_ME' .env.docker; then
  sed -i "s/^AUTH_SECRET=.*/AUTH_SECRET=$(openssl rand -hex 32)/" .env.docker
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
if [[ "$admin_password_created" == "1" ]]; then
  echo "管理员用户名：$(grep '^ADMIN_USERNAME=' .env.docker | cut -d= -f2)"
  echo "管理员初始密码：$(grep '^ADMIN_PASSWORD=' .env.docker | cut -d= -f2)"
  echo "请立即记录管理员初始密码。"
fi
