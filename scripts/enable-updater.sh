#!/usr/bin/env bash
set -euo pipefail

# 启用「一键热更新」：生成访问令牌、构建并启动 compose ops profile 里的运维边车。
# 启用后，后台「系统更新」页面即可直接完成 备份 → 拉代码 → 拉镜像 → 重启 → 健康检查，不用再登录服务器。
# 幂等：重复执行只补齐缺失配置，已有的 UPDATER_TOKEN 会被保留（后端与边车必须用同一个令牌）。
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -f .env.docker ]]; then
  echo "缺少 .env.docker，请先执行 ./scripts/docker-deploy.sh 完成首次部署。" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "无法访问 Docker 守护进程，请用 root 或已加入 docker 组的账号执行。" >&2
  exit 1
fi

set_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" .env.docker; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env.docker
  else
    # 老文件可能没有结尾换行，先补齐再追加，避免粘到上一行
    [[ -n "$(tail -c 1 .env.docker)" ]] && printf '\n' >> .env.docker
    printf '%s=%s\n' "$key" "$value" >> .env.docker
  fi
}

echo "== 1/5 生成访问令牌 =="
token="$(grep '^UPDATER_TOKEN=' .env.docker | cut -d= -f2- || true)"
if [[ -z "$token" ]]; then
  if command -v openssl >/dev/null 2>&1; then
    token="$(openssl rand -hex 32)"
  else
    token="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  fi
  echo "已生成新的 UPDATER_TOKEN（32 字节随机）"
else
  echo "沿用 .env.docker 中已有的 UPDATER_TOKEN"
fi
set_env "UPDATER_TOKEN" "$token"
# 项目目录按容器内的同一绝对路径挂载，updater 里的 compose 才能解析到相同位置
set_env "TEMUBOX_PROJECT_DIR" "$PROJECT_DIR"
if ! grep -q '^UPDATER_BRANCH=' .env.docker; then
  set_env "UPDATER_BRANCH" "$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
fi

echo "== 2/5 构建边车镜像（只构建 updater，不动前后端）=="
docker compose --env-file .env.docker --profile ops build updater

echo "== 3/5 启动边车 =="
docker compose --env-file .env.docker --profile ops up -d updater

echo "== 4/5 重启后端以载入令牌 =="
docker compose --env-file .env.docker up -d backend

echo "== 5/5 检查状态 =="
health=""
for _ in $(seq 1 20); do
  cid="$(docker compose --env-file .env.docker --profile ops ps -q updater 2>/dev/null || true)"
  if [[ -n "$cid" ]]; then
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null || true)"
    [[ "$health" == "healthy" ]] && break
  fi
  sleep 3
done
echo "边车健康状态：${health:-unknown}"
docker compose --env-file .env.docker --profile ops ps updater
echo "后端 → 边车连通性："
docker compose --env-file .env.docker exec -T backend python -c \
  "import urllib.request; print(urllib.request.urlopen('http://updater:8899/health', timeout=5).read().decode())"

echo
echo "完成：登录后台 → 侧边栏「系统更新」，即可一键更新到最新版本，不用再登录服务器。"
echo "若出现「运维边车不可用」，多半是边车未启动，重跑本脚本或执行："
echo "  docker compose --env-file .env.docker --profile ops up -d updater && docker compose --env-file .env.docker up -d backend"
