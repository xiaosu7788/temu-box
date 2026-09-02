# Temu-Box

正式版采用前后端分离架构：

- 前端：Vue 3 + TypeScript + Vite + Element Plus
- 后端：FastAPI + Uvicorn
- Excel：openpyxl
- 数据库：PostgreSQL（本地未配置时自动使用 SQLite）
- 任务：后台线程池，任务状态、日志和统计信息持久化到数据库，原始文件保存到磁盘
- 部署：Nginx + systemd

## 目录

```text
temu-box/
├── frontend/        Vue 前端
├── backend/         FastAPI 后端和测试
├── data/            库存、缓存、头程名单、任务文件
├── deploy/          Nginx 和 systemd 配置
└── scripts/         本地开发脚本
```

## 当前功能

- 管理员初始化、用户注册、管理员审核和 Cookie 登录
- 用户任务记录按账号隔离，普通用户不能读取其他用户的任务结果
- 后台管理：用户管理、订单成本参数和批量报名活动参数
- 使用服务器库存表计算销售订单货值和成本
- 库存表更新及缓存重建
- SKU 批量价格查询，显示价格来源工作表、行号和列号
- 头程减半名单导入、查询和删除
- 后台任务进度、处理日志、历史记录和结果下载
- 价格表头可位于 SKU 上方任意位置
- 无价格表头时从 SKU 左右两侧逐步查找
- 同一 SKU 多处出现时取最低货值
- 批量报名活动价格计算、低于活动价自动过滤和结果下载
- 批量报名活动支持后台默认浮动上限和用户单次任务自定义浮动上限

## Windows 本地运行

在 `backend` 目录创建环境并安装依赖：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

在 `frontend` 目录安装依赖：

```powershell
npm install
```

随后运行：

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\dev.ps1
```

访问 `http://127.0.0.1:5173`，API 文档位于 `http://127.0.0.1:8089/api/docs`。

## Linux 部署

```bash
cd "/var/www/temu-box/backend"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cd ../frontend
npm install
npm run build
```

复制并启用 systemd 服务：

```bash
sudo cp "/var/www/temu-box/deploy/sales-tool-v2.service" /etc/systemd/system/
sudo chown -R www-data:www-data "/var/www/temu-box/data"
sudo systemctl daemon-reload
sudo systemctl enable --now sales-tool-v2
sudo systemctl status sales-tool-v2 --no-pager -l
```

将 `deploy/nginx.conf` 的 `server` 配置放入当前宝塔站点配置，然后使用宝塔实际 Nginx 二进制测试并重载配置。

实时查看 Python 处理日志：

```bash
sudo tail -f /var/log/sales-tool-v2.log
```

## 数据文件

```text
data/inventories/库存统计表.xlsx
data/price_cache.json
data/half_headcost_skus.json
data/tasks/<task_id>/
```

`price_cache.json` 是可重建缓存。更新库存表后缓存会自动失效，原始库存表始终是数据源。

## 后续扩展

## 前端反馈规范

- 成功、警告、错误提示和危险操作确认统一通过 `frontend/src/feedback.ts` 调用。
- 页面组件禁止直接使用 `ElMessage`、`ElMessageBox`、`ElNotification` 或浏览器原生 `alert/confirm/prompt`。
- 表单校验和整页加载失败可以使用页面内错误区域，避免短暂消息消失后用户找不到原因。
- `npm run build` 会先执行 `check:feedback`，发现绕过统一反馈服务的代码时构建失败。

## 数据库配置

数据库是库存、头程名单、任务和活动任务的主数据源。原始 Excel、任务上传文件和生成结果仍保存于 `data/`，`price_cache.json` 和 `half_headcost_skus.json` 仅作为旧版本兼容镜像。

本地不设置 `DATABASE_URL` 时，默认使用 `data/sales_tool.db`；服务器建议使用 PostgreSQL：

```bash
sudo -u postgres psql
CREATE USER sales_tool WITH PASSWORD '替换为强密码';
CREATE DATABASE sales_tool OWNER sales_tool;
\\q
cp .env.example .env
# 编辑 .env，填写 DATABASE_URL，并确认路径使用 /var/www/temu-box
cd backend
.venv/bin/alembic upgrade head
```

首次启动会自动建表，并迁移已有的 `price_cache.json`、`half_headcost_skus.json` 和 `data/tasks/*/task.json`。后续启动不会覆盖数据库中的新数据。
`sales-tool-v2.service` 每次启动前会自动执行 `alembic upgrade head`，用于以后平滑升级数据库结构。

管理员账号通过环境变量初始化：`ADMIN_USERNAME` 和 `ADMIN_PASSWORD`。Docker 一键脚本首次生成管理员密码时会在终端输出，请及时记录；普通用户注册后默认是待审核状态，管理员登录后台后在“用户管理”中批准。

```bash
sudo chown root:www-data /var/www/temu-box/.env
sudo chmod 640 /var/www/temu-box/.env
sudo systemctl daemon-reload
sudo systemctl restart sales-tool-v2
curl http://127.0.0.1:8089/api/health
```

生产环境备份至少包括 PostgreSQL 数据库和 `data/` 文件目录：

```bash
sudo -u postgres pg_dump -Fc sales_tool > sales_tool_$(date +%F).dump
tar -czf temu-box-data-$(date +%F).tar.gz data
```

## Docker 一键部署

Docker 部署不使用旧项目目录。以下命令会在全新的 `/var/www/temu-box` 目录部署，旧的 `/var/www/成本计算工具` 不参与运行。

如果服务器还没有 Docker：

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
docker --version
docker compose version
```

拉取新版项目：

```bash
sudo mkdir -p /var/www
cd /var/www
sudo git clone https://github.com/xiaosu7788/temu-box.git temu-box
cd /var/www/temu-box
```

执行一键部署：

```bash
chmod +x scripts/docker-deploy.sh
./scripts/docker-deploy.sh
```

脚本会自动创建 `.env.docker`、生成 PostgreSQL 和管理员随机密码、创建数据目录并启动 PostgreSQL、FastAPI 和前端 Nginx。默认对外端口为 `8089`：

```text
http://服务器IP:8089
```

默认使用 Docker 自带的 PostgreSQL，不需要在宝塔另外创建数据库。如果宝塔创建的是 PostgreSQL，也可以在 `.env.docker` 中配置外部数据库：

```ini
DATABASE_URL=postgresql+psycopg://用户名:密码@host.docker.internal:5432/数据库名
DB_CONNECT_TIMEOUT=10
DB_STATEMENT_TIMEOUT_MS=30000
```

此时一键脚本不会启动内置 PostgreSQL 容器，而是让后端连接宝塔数据库。宝塔数据库必须允许 Docker 容器访问，且服务器上 PostgreSQL 端口通常为 `5432`。连接超时默认 10 秒，单条 SQL 最长执行 30 秒，避免数据库异常时前端页面长期停留在加载状态。可以先检查：

```bash
sudo ss -ltnp | grep 5432
sudo -u postgres psql -c '\\l'
```

如果宝塔创建的是 MySQL 或 MariaDB，不能直接连接本项目，应该删除 `.env.docker` 中的 `DATABASE_URL` 配置，使用 Docker 自带 PostgreSQL。数据库密码建议使用字母、数字和下划线，避免 URL 特殊字符未编码。

库存表放在宿主机的 `data/inventories/库存统计表.xlsx`，任务文件和结果也会保存在宿主机 `data/` 中；PostgreSQL 数据保存在 Docker volume `temu-box_postgres_data`。

查看状态和日志：

```bash
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker logs -f backend
docker compose --env-file .env.docker logs -f db
```

更新版本：

```bash
cd /var/www/temu-box
git pull origin main
docker compose --env-file .env.docker up -d --build
```

如果用宝塔绑定域名，在宝塔站点中将 `/` 反向代理到 `http://127.0.0.1:8089`，再由宝塔负责 SSL。使用 HTTPS 域名时，将 `.env.docker` 的 `COOKIE_SECURE` 改为 `true` 后重建后端。Docker 部署时不要同时启动占用 `8089` 的 `sales-tool-v2.service`，也不要把域名代理到旧项目的 `8088`。

停止或重启容器：

```bash
docker compose --env-file .env.docker restart
docker compose --env-file .env.docker down
```

不要使用 `docker compose down -v`，否则会删除 PostgreSQL 数据卷。
