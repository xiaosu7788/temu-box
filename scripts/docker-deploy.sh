#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -f .env.docker ]] || grep -q 'CHANGE_ME' .env.docker; then
  cp .env.docker.example .env.docker
  password="$(openssl rand -hex 24)"
  sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${password}/" .env.docker
  echo "已生成 .env.docker 和随机数据库密码。"
fi

mkdir -p data/inventories data/tasks data/activities data/logs
docker compose --env-file .env.docker up -d --build
docker compose --env-file .env.docker ps
echo "访问地址：http://127.0.0.1:$(grep '^WEB_PORT=' .env.docker | cut -d= -f2)"
