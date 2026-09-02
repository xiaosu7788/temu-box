#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -f .env.docker ]]; then
  echo "缺少 .env.docker，请先执行部署脚本。" >&2
  exit 1
fi

read_env_value() {
  local key="$1"
  local line
  line="$(grep -m1 "^${key}=" .env.docker || true)"
  printf '%s' "${line#*=}"
}

DATABASE_URL_VALUE="$(read_env_value DATABASE_URL)"
POSTGRES_USER_VALUE="$(read_env_value POSTGRES_USER)"
POSTGRES_DB_VALUE="$(read_env_value POSTGRES_DB)"

BACKUP_DIR="$PROJECT_DIR/backups"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

tar -czf "$BACKUP_DIR/temu-box-data-$TIMESTAMP.tar.gz" data

if [[ -z "$DATABASE_URL_VALUE" ]]; then
  docker compose --env-file .env.docker --profile internal-db exec -T db \
    pg_dump -U "${POSTGRES_USER_VALUE:-temubox}" -d "${POSTGRES_DB_VALUE:-temubox}" -Fc \
    > "$BACKUP_DIR/temu-box-db-$TIMESTAMP.dump"
  echo "数据库备份：$BACKUP_DIR/temu-box-db-$TIMESTAMP.dump"
else
  echo "检测到外部 PostgreSQL，数据库请使用外部数据库服务的备份方案。"
fi

echo "文件备份：$BACKUP_DIR/temu-box-data-$TIMESTAMP.tar.gz"
