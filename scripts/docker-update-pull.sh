#!/usr/bin/env bash
set -euo pipefail

# 镜像预构建更新流程：前后端镜像由 GitHub Actions 构建并推送到 GHCR，服务器只拉取。
# 与 docker-deploy.sh（服务器本地构建）互为替代，日常更新用本脚本。
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -f .env.docker ]]; then
  echo "缺少 .env.docker，请先执行 ./scripts/docker-deploy.sh 完成首次部署。" >&2
  exit 1
fi

echo "== 1/5 备份数据 =="
./scripts/backup.sh

echo "== 2/5 拉取代码 =="
git pull --ff-only origin main

echo "== 3/5 拉取镜像 =="
if ! docker compose --env-file .env.docker pull backend frontend; then
  echo "镜像拉取失败：请确认 GHCR 包已设为 public；若为私有，先执行 docker login ghcr.io。" >&2
  exit 1
fi

# 店铺管理模块（可选）：配了 BRIDGE_TOKEN 才拉起 bridge，失败不阻断主流程
if grep -q '^BRIDGE_TOKEN=.' .env.docker 2>/dev/null; then
  echo "  → 同时更新 bridge 镜像"
  docker compose --env-file .env.docker --profile stores pull bridge \
    || echo "  ⚠ bridge 镜像拉取失败，保留现有镜像继续"
fi

echo "== 4/6 启动服务（不重新构建）=="
# --force-recreate 是必须的：镜像 tag 是 :latest，pull 之后 compose 认为配置没变，
# 不带这个参数容器会原地不动，新代码根本没生效（历史踩过）
if grep -q '^DATABASE_URL=.' .env.docker; then
  docker compose --env-file .env.docker up -d --force-recreate backend frontend
else
  docker compose --env-file .env.docker --profile internal-db up -d --force-recreate backend frontend
fi

# bridge 已经在跑的话，把它一起拉起（新镜像会在这一步重建容器）
if grep -q '^BRIDGE_TOKEN=.' .env.docker 2>/dev/null; then
  docker compose --env-file .env.docker --profile stores up -d --force-recreate bridge \
    || echo "  ⚠ bridge 启动失败，店铺管理模块暂不可用"
fi

echo "== 5/6 检查状态 =="
docker compose --env-file .env.docker --profile internal-db ps
web_port="$(grep '^WEB_PORT=' .env.docker | cut -d= -f2)"
web_port="${web_port:-8089}"
curl -i "http://127.0.0.1:${web_port}/api/health"

echo "== 6/6 清理旧镜像与构建缓存 =="
# 每次 pull + --force-recreate 都会把旧的 :latest 打成悬空镜像，
# 不清理会一直堆（服务器上曾累积 89 个 / 8.4GB）。
# 只清 24 小时前的，避免误删正在用于回滚的镜像。
freed="$(docker image prune -f --filter 'until=24h' 2>/dev/null | sed -n 's/^Total reclaimed space: //p')"
echo "  悬空镜像：${freed:-无}"
cached="$(docker builder prune -f 2>/dev/null | sed -n 's/^Total:\s*//p')"
echo "  构建缓存：${cached:-无}"

# 旧备份也顺带按保留份数清理（backup.sh 内也有同样逻辑，
# 这里兜住「用旧版 backup.sh 备份过」的历史数据）
keep="${BACKUP_KEEP:-15}"
for pattern in "temu-box-data-*.tar.gz" "temu-box-db-*.dump"; do
  [[ -d backups ]] || continue
  # shellcheck disable=SC2012
  ls -t backups/$pattern 2>/dev/null | tail -n +$((keep + 1)) | xargs -r rm -f
done
echo "  备份：保留最近 ${keep} 份"
