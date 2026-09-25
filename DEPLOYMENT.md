# Temu-Box 部署手册

本文只适用于 Temu-Box。生产环境统一使用 Docker Compose，应用端口为 `8089`。

## 1. 服务器要求

- Linux x86_64 或 ARM64
- 至少 2 GB 内存，建议 4 GB
- 至少 10 GB 可用磁盘
- Docker Engine 24+ 和 Docker Compose v2
- 已放行 TCP `8089`，或通过宝塔反向代理域名

安装 Docker（Ubuntu）：

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin git openssl
sudo systemctl enable --now docker
docker --version
docker compose version
```

## 2. 获取项目

选择一个全新的空目录：

```bash
sudo mkdir -p /opt/temu-box
sudo chown -R "$USER":"$USER" /opt/temu-box
git clone https://github.com/xiaosu7788/temu-box.git /opt/temu-box
cd /opt/temu-box
```

后续命令均在 `/opt/temu-box` 执行。

## 3. 选择数据库

### 方案 A：Docker PostgreSQL（推荐）

无需提前创建数据库。一键部署脚本会启动 PostgreSQL 容器，并使用 Docker 数据卷保存数据库。

### 方案 B：已有 PostgreSQL

先创建独立数据库和用户，然后在 `.env.docker` 中设置：

```ini
DATABASE_URL=postgresql+psycopg://temubox:数据库密码@host.docker.internal:5432/temubox
```

数据库必须允许 Docker 容器连接。密码含有 `@`、`:`、`/`、`#` 等字符时，必须进行 URL 编码。

## 4. 首次部署

推荐执行一键脚本：

```bash
chmod +x scripts/docker-deploy.sh
./scripts/docker-deploy.sh
```

脚本会：

1. 从模板创建 `.env.docker`。
2. 生成数据库密码、管理员密码和登录密钥。
3. 创建持久化目录。
4. 构建前后端镜像。
5. 执行 Alembic 数据库迁移。
6. 启动 PostgreSQL、FastAPI 和前端 Nginx。

> 该脚本在服务器本地构建镜像。若已配置 GHCR 预构建镜像（见第 12 节），可把第 4 步替换为 `docker compose --env-file .env.docker pull backend frontend`，并在启动时去掉 `--build`。

首次运行会在终端显示管理员用户名和随机密码，请立即记录。

也可以手动配置：

```bash
cp .env.docker.example .env.docker
nano .env.docker
mkdir -p data/inventories data/tasks data/activities data/logs
docker compose --env-file .env.docker --profile internal-db up -d --build
```

手动配置时必须替换所有 `CHANGE_ME` 值。

## 5. 验证部署

```bash
docker compose --env-file .env.docker --profile internal-db ps
curl -i http://127.0.0.1:8089/api/health
```

健康接口正常返回示例：

```json
{"status":"ok","version":"2.0.0","database":{"status":"ok","dialect":"postgresql"}}
```

浏览器访问：

```text
http://服务器IP:8089
```

## 6. 宝塔绑定域名

1. 在宝塔创建纯静态站点并绑定域名。
2. 添加反向代理，目标 URL 填写 `http://127.0.0.1:8089`。
3. 申请并启用 SSL 证书。
4. 修改 `.env.docker`：

```ini
COOKIE_SECURE=true
```

5. 重建并启动服务：

```bash
docker compose --env-file .env.docker up -d --build
```

宝塔只负责公网域名和 HTTPS；Temu-Box 的前端及 API 都由 Docker Compose 管理。

## 7. 更新版本

更新前建议先备份：

```bash
cd /opt/temu-box
./scripts/backup.sh
```

### 方式 A：拉取预构建镜像（推荐）

前后端镜像由 GitHub Actions 构建并推送到 GHCR，服务器只拉取，不在本地编译：

```bash
./scripts/docker-update-pull.sh
```

脚本依次执行：备份 → `git pull --ff-only origin main` → 拉取前后端镜像 → 启动（不带 `--build`）→ 状态与健康检查。

手动等价命令：

```bash
git pull --ff-only origin main
docker compose --env-file .env.docker pull backend frontend
docker compose --env-file .env.docker --profile internal-db up -d
docker compose --env-file .env.docker --profile internal-db ps
curl -i http://127.0.0.1:8089/api/health
```

关键点：**方式 A 不能带 `--build`**，否则会退化成服务器本地构建，并覆盖刚拉取的镜像。

### 方式 B：服务器本地构建

不使用预构建镜像时使用（例如镜像尚未发布、服务器无法访问 GHCR）：

```bash
git pull --ff-only origin main
docker compose --env-file .env.docker up -d --build
docker compose --env-file .env.docker --profile internal-db ps
curl -i http://127.0.0.1:8089/api/health
```

`--build` 会在构建期间临时增加 CPU 和内存占用，完成后构建进程会退出。不要删除 `.env.docker`、`data/` 或数据库卷。

两种方式都必须让前后端在同一次操作中一起更新：新旧版本混用会出现字段不一致，例如批量活动预览弹窗显示空白。

## 8. 日志与状态

```bash
# 所有容器状态
docker compose --env-file .env.docker --profile internal-db ps

# 后端实时日志
docker compose --env-file .env.docker logs -f --tail=200 backend

# 前端日志
docker compose --env-file .env.docker logs -f --tail=100 frontend

# 内置 PostgreSQL 日志
docker compose --env-file .env.docker --profile internal-db logs -f --tail=100 db

# 资源占用
docker stats --no-stream
```

## 9. 数据位置

- PostgreSQL：Docker 卷 `temu-box_postgres_data`，或 `.env.docker` 指定的外部 PostgreSQL
- 库存文件：`data/inventories/库存统计表.xlsx`
- 订单任务：`data/tasks/`
- 活动任务：`data/activities/`
- 应用日志目录：`data/logs/`

管理员可以在后台页面上传库存表，不需要直接操作服务器文件。

## 10. 备份

执行仓库内脚本：

```bash
./scripts/backup.sh
```

备份文件保存在 `backups/`，包括 PostgreSQL 导出和 `data/` 压缩包。使用外部 PostgreSQL 时，应同时使用数据库服务提供的备份方案。

不要只备份 Docker 镜像；镜像不包含业务数据。

## 11. 常用操作

```bash
# 重启
docker compose --env-file .env.docker restart

# 停止
docker compose --env-file .env.docker down

# 重新启动
docker compose --env-file .env.docker up -d

# 查看数据库迁移版本
docker compose --env-file .env.docker exec backend alembic current

# 查看磁盘占用
docker system df
```

禁止执行以下命令，除非确认要永久删除内置 PostgreSQL：

```bash
docker compose down -v
```

## 12. 镜像预构建（GitHub Actions + GHCR）

前后端镜像由 GitHub Actions 在推送 `main` 时构建并推送到 GitHub Container Registry，服务器只拉取，不在本地编译。

- 工作流：`.github/workflows/docker-publish.yml`
- 触发：推送到 `main` 且改动涉及 `backend/`、`frontend/`、`docker/`、`docker-compose.yml`；也可在仓库 Actions 页面手动触发
- 镜像：
  - `ghcr.io/xiaosu7788/temu-box-backend:latest`
  - `ghcr.io/xiaosu7788/temu-box-frontend:latest`
- 标签：`latest`（仅默认分支）和 `sha-<提交短哈希>`，回滚时使用 `sha-` 标签
- 架构：`linux/arm64`，与 ARM64 服务器一致；换 x86 服务器需修改工作流中的 `platforms`

### 一次性设置：把镜像包设为公开

首次推送后，打开仓库页面 → 右侧 `Packages` → 选择 `temu-box-backend` → `Package settings` → `Change visibility` → `Public`；`temu-box-frontend` 同样操作。

设为公开后服务器无需登录即可拉取。若保持私有，在服务器执行：

```bash
echo <GitHub PAT> | docker login ghcr.io -u xiaosu7788 --password-stdin
```

PAT 需要 `read:packages` 权限。

### 指定镜像标签与回滚

镜像名可在 `.env.docker` 中覆盖：

```ini
TEMUBOX_BACKEND_IMAGE=ghcr.io/xiaosu7788/temu-box-backend:sha-1a2b3c4
TEMUBOX_FRONTEND_IMAGE=ghcr.io/xiaosu7788/temu-box-frontend:sha-1a2b3c4
```

回滚时把两个变量都改成目标 `sha-` 标签，再按第 7 节方式 A 更新。

### 构建加速（可选）

默认用 QEMU 在 x86 runner 上模拟构建 arm64，前端 `npm run build` 较慢。若仓库可用 GitHub 原生 ARM runner，把工作流中的 `runs-on: ubuntu-latest` 改为 `runs-on: ubuntu-24.04-arm`，并删除「准备 QEMU」步骤。

### 相关排查命令

```bash
# 服务器实际使用的镜像与标签
docker compose --env-file .env.docker images

# 确认镜像架构（应为 arm64）
docker image inspect ghcr.io/xiaosu7788/temu-box-backend:latest --format '{{.Architecture}}'

# 手动拉取镜像
docker compose --env-file .env.docker pull backend frontend
```

## 13. 故障排查

### 后端容器不健康

```bash
docker compose --env-file .env.docker ps -a
docker compose --env-file .env.docker logs backend --tail=200
docker compose --env-file .env.docker run --rm backend alembic current
```

### 域名返回 502

```bash
curl -i http://127.0.0.1:8089/api/health
sudo ss -ltnp | grep 8089
docker compose --env-file .env.docker ps
```

如果本机健康接口正常而域名 502，检查宝塔反向代理目标是否为 `http://127.0.0.1:8089`。

### 更新后页面仍是旧版本

```bash
# 镜像预构建模式
docker compose --env-file .env.docker pull frontend
docker compose --env-file .env.docker up -d frontend

# 服务器本地构建模式
docker compose --env-file .env.docker up -d --build frontend
docker compose --env-file .env.docker restart frontend
```

随后强制刷新浏览器页面。
