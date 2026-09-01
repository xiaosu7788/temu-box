# temu-box

正式版采用前后端分离架构：

- 前端：Vue 3 + TypeScript + Vite + Element Plus
- 后端：FastAPI + Uvicorn
- Excel：openpyxl
- 任务：后台线程池，任务状态和日志持久化到磁盘
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

- 使用服务器库存表计算销售订单货值和成本
- 库存表更新及缓存重建
- SKU 批量价格查询，显示价格来源工作表、行号和列号
- 头程减半名单导入、查询和删除
- 后台任务进度、处理日志、历史记录和结果下载
- 价格表头可位于 SKU 上方任意位置
- 无价格表头时从 SKU 左右两侧逐步查找
- 同一 SKU 多处出现时取最低货值
- 批量报名活动价格计算、低于活动价自动过滤和结果下载

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
cd "/var/www/成本计算工具/temu-box/backend"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cd ../frontend
npm install
npm run build
```

复制并启用 systemd 服务：

```bash
sudo cp "/var/www/成本计算工具/temu-box/deploy/sales-tool-v2.service" /etc/systemd/system/
sudo chown -R www-data:www-data "/var/www/成本计算工具/temu-box/data"
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

当前任务持久化采用目录和 JSON，适合单机部署。增加账号、权限、多人协作和复杂报表时，再将用户、任务和操作日志迁移到 PostgreSQL；任务执行可迁移到 Redis + Celery/RQ，前端 API 不需要重写。
