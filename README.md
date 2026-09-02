# Temu-Box

Temu-Box 是用于 Temu 订单成本计算、库存管理和批量报名活动处理的 Web 应用。

## 技术架构

- 前端：Vue 3、TypeScript、Vite、Element Plus
- 后端：FastAPI、SQLAlchemy、Alembic、openpyxl
- 数据库：PostgreSQL
- 部署：Docker Compose、Nginx

## 功能

- 用户注册、管理员审核和权限隔离
- 多区域成本参数与活动价格配置
- 订单成本计算和结果下载
- 库存上传、缓存重建、SKU 查询和明细管理
- 头程减半名单管理
- 批量报名活动、默认及自定义 SKC 识别规则
- 用户任务记录和管理员任务管理

## 项目结构

```text
temu-box/
├── backend/                 FastAPI 后端、迁移与测试
├── frontend/                Vue 前端
├── docker/                  容器镜像与 Nginx 配置
├── scripts/                 开发和部署脚本
├── data/                    Excel、任务文件及生成结果
├── docker-compose.yml       生产编排配置
├── .env.docker.example      生产环境变量模板
└── DEPLOYMENT.md            完整部署手册
```

## 部署

全新服务器部署、宝塔域名配置、版本更新、日志、备份和故障排查请阅读 [DEPLOYMENT.md](DEPLOYMENT.md)。

快速开始：

```bash
git clone https://github.com/xiaosu7788/temu-box.git
cd temu-box
chmod +x scripts/docker-deploy.sh
./scripts/docker-deploy.sh
```

默认访问地址：`http://服务器IP:8089`

## Windows 本地开发

安装后端依赖：

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

安装前端依赖：

```powershell
cd ..\frontend
npm install
cd ..
```

运行开发环境：

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\dev.ps1
```

- 前端：`http://127.0.0.1:5173`
- API：`http://127.0.0.1:8089`
- API 文档：`http://127.0.0.1:8089/api/docs`

## 数据说明

PostgreSQL 保存用户、区域配置、库存明细和任务记录。`data/` 保存库存 Excel、用户上传文件及处理结果。生产备份必须同时包含 PostgreSQL 和 `data/`。

## 验证

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q

cd ..\frontend
npm run build
```
