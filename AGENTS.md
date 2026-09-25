# AGENTS.md

本文件定义 Temu-Box 项目的局部事实、命令与架构约束，供本仓库内的开发工作使用。
跨项目通用规则见 `~/.pi/agent/AGENTS.md`；部署细节见 `DEPLOYMENT.md`。

## 项目概况

Temu-Box 是用于 Temu 订单成本计算、库存管理和批量报名活动处理的 Web 应用。

- 前端：Vue 3 + TypeScript + Vite + Element Plus（`frontend/`）
- 后端：FastAPI + SQLAlchemy 2 + Alembic + openpyxl（`backend/`）
- 数据库：默认 SQLite（`data/temubox.db`），生产用 PostgreSQL（Docker Compose）
- 部署：Docker Compose + Nginx，详见 `DEPLOYMENT.md`；首次部署 `scripts/docker-deploy.sh`（服务器本地构建），日常更新 `scripts/docker-update-pull.sh`（拉取 GHCR 预构建镜像，由 `.github/workflows/docker-publish.yml` 构建）

## 目录与关键文件

```text
temu-box/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI 唯一入口（路由、校验、审计都在这里）
│   │   ├── config.py        # 全部环境变量与路径配置（API_HOST/PORT、TEMUBOX_DATA_DIR、DATABASE_URL…）
│   │   ├── database.py      # SQLAlchemy 模型、CRUD、DEFAULT_SETTINGS、迁移兼容逻辑
│   │   ├── schemas.py       # Pydantic 请求/响应模型
│   │   └── services/        # 领域逻辑：orders、inventory、activity、categories、regions、
│   │                        #   settings、tasks、taskpool、auth、audit、cleanup、monitoring…
│   ├── migrations/          # Alembic 迁移
│   └── tests/               # pytest（conftest.py 强制用临时数据目录）
├── frontend/
│   └── src/
│       ├── api.ts           # 所有后端调用的 axios 封装（http / uploadHttp）
│       ├── types.ts         # 前后端共享的 TS 类型
│       ├── regionState.ts   # 全局区域/品类选择状态
│       ├── router.ts        # 路由（含 /admin 后台）
│       └── views/           # 页面；components/ 通用组件
├── scripts/                 # dev.ps1、dev-backend-local.ps1、dev-frontend-local.ps1、backup.sh、docker-deploy.sh、docker-update-pull.sh
├── docker/                  # 镜像与 nginx.conf
├── .github/workflows/       # docker-publish.yml：构建前后端镜像并推送到 GHCR
├── data/                    # 运行时数据（不入库）：inventories/、tasks/、activities/、logs/、*.db、*.json
├── docker-compose.yml
├── DEPLOYMENT.md
└── .env / .env.docker       # 敏感配置，禁止入库/读取后外泄
```

## 常用命令

```powershell
# 后端测试（必须先在 backend/ 下运行；conftest 自动用临时数据目录，不碰真实库）
cd backend
.\.venv\Scripts\python.exe -m pytest -q

# 前端类型检查 + 构建（build 会先跑 check:feedback）
cd frontend
npm run build

# 仅类型检查
npx vue-tsc -b --force

# 本地开发（见 scripts/dev*.ps1；后端 8089，前端 5173）
```

## 配置与数据

- 环境变量在 `backend/app/config.py` 集中读取：`TEMUBOX_DATA_DIR`（默认 `data/`）、`DATABASE_URL`（默认
  `sqlite:///data/temubox.db`）、`AUTH_SECRET`、`ADMIN_USERNAME/ADMIN_PASSWORD`、`API_PORT`(8089)。
- 关键路径：`data/inventories/库存统计表.xlsx`、`data/price_cache.json`、`data/half_headcost_skus.json`、
  `data/tasks/`、`data/activities/`、`data/logs/`。
- 应用设置存 `app_settings` 表：`activity`（成本参数 + `default_skc_rules`）、`order`、`system`、
  `activity_skc_rules:<品类代码>`（品类级 SKC 识别规则，如 `activity_skc_rules:A`）。
- `.env`、`data/*.db`、`data/*.xlsx`、`data/pending_inventory_catalog*.json` 等均已被 `.gitignore` 排除，
  提交时不得包含。

## 核心业务逻辑（改动前必读）

### SKC 识别规则（`backend/app/services/activity.py`）

- 规则结构：`set_keywords`（套装标识）、`set_mappings`（固定映射）、`single_rules`（多条单品规则，按顺序
  依次尝试、先匹配先用，最多 20 条）。
- 旧格式兼容：`single_mode/single_delimiter/single_marker` 单值字段自动升级为一条 `single_rules`；
  归一化输出仍回显旧字段（`single_mode` 等），前端已改用 `single_rules`。
- 优先级：固定映射 → 套装标识（件数必须在 `allowed_pieces` 档位内）→ 单品规则。
- 重要语义：**套装标识命中但件数不在档位时，视为"不是合法套装"，继续尝试后续规则**（不要 `return None`，
  否则 `1pc-10` 会被误判为无法识别）。
- 校验集中在 `normalize_parse_config`：空列表、超限、方式非法、分隔符/文字为空、重复规则均报 `ValueError`，
  由 `main.py` 转成 400。
- 识别依据（`method`）用于预览/排障，多规则命中时会标注"第 N 条规则"。

### 活动价格计算（`activity.py`）

- 单品底价 = 货值 + 头程(`headcost`) + 操作费(`operation_fee`) + 匹配利润（按 `single_tiers` 货值区间取
  max，取不到则 0）。
- 套装底价 = `set_prices` 对应件数的活动价。
- ID 利润规则（`id_profit_rules`）：按 SPU/SKC/SKU 优先级匹配，命中则把 `profit` 加进底价。
- 上浮：`_uplifted_price(base, reference, uplift_limit)` 把申报价上浮到 `(base, base + min(uplift_limit,
  reference - base)]` 的随机值（两位小数）；申报价 ≤ 底价时该行不变或整行删除。
- **预览必须与实际写入一致**：`preview_activity_workbook` 与 `process_activity_workbook` 共用同一套规则与
  价格逻辑；预览返回 `final_price_low/high` 区间与 `action`（上浮/不变/将被删除/无法识别）。

### 其他领域

- 订单成本（`services/orders.py`）：货值 + 头程 + 操作费 + 续件费 + 尾程 - 运费补贴；头程减半名单命中时头程
  按 50%。
- 库存（`services/inventory.py`）：Excel 上传 → 解析 → 缓存（`price_cache.json`）→ SKU 查询；品类映射
  （A 映射式 / 其他自动式）。
- 任务（`services/taskpool.py` + `services/tasks.py` / `activity_tasks.py`）：提交入队 → 后台线程执行 →
  结果落 `data/`，任务记录在库。

## 工作约定

- 默认语言：中文；注释、文档、提交信息用中文，专有名词与 API 名保留原文。
- 文件 UTF-8 无 BOM、LF 行尾（Windows 上 git 会提示 CRLF 转换属正常，提交用 `-c core.autocrlf=false`
  或不转）。
- **绝不在真实数据库（`data/temubox.db` 或服务器库）上做写入类验证**。`pytest` 已隔离到临时目录；手写
  验证脚本涉及写库时，必须把 `TEMUBOX_DATA_DIR` 指向 scratch 临时目录，或只做 GET/只读。
- 涉及"恢复用户配置/数据"时：先向用户确认正确值，不要凭旧快照或日志猜测覆盖（历史教训：曾两次破坏品类 A
  的 SKC 规则配置）。
- 提交前跑后端 `pytest`；涉及前端必跑 `npm run build`。推送网络不稳定时允许带重试，推送后可用 GitHub API
  核对远程 HEAD。
- 不要提交运行时文件：`.env`、`data/*`、`dist/`、`node_modules/`、`.venv/`。
