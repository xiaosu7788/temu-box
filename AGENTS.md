# AGENTS.md

本文件定义 Temu-Box 项目的局部事实、命令与架构约束，供本仓库内的开发工作使用。
跨项目通用规则见 `~/.pi/agent/AGENTS.md`；部署细节见 `DEPLOYMENT.md`。

## 项目概况

Temu-Box 是用于 Temu 订单成本计算、库存管理和批量报名活动处理的 Web 应用。

- 前端：Vue 3 + TypeScript + Vite + Element Plus（`frontend/`）
- 后端：FastAPI + SQLAlchemy 2 + Alembic + openpyxl（`backend/`）
- 数据库：默认 SQLite（`data/temubox.db`），生产用 PostgreSQL（Docker Compose）
- 部署：Docker Compose + Nginx，详见 `DEPLOYMENT.md`；首次部署 `scripts/docker-deploy.sh`（服务器本地构建），日常更新 `scripts/docker-update-pull.sh`（拉取 GHCR 预构建镜像，由 `.github/workflows/docker-publish.yml` 构建）；可选的后台一键热更新用 `scripts/enable-updater.sh` 启用（见 `DEPLOYMENT.md` 第 7 节方式 C）

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
├── scripts/                 # dev.ps1、dev-backend-local.ps1、dev-frontend-local.ps1、backup.sh、docker-deploy.sh、docker-update-pull.sh、enable-updater.sh
├── docker/                  # 镜像、nginx.conf、updater/（后台「一键热更新」运维边车，可选）
├── .github/workflows/       # docker-publish.yml：构建前后端镜像并推送到 GHCR
├── data/                    # 运行时数据（不入库）：inventories/、tasks/、activities/、logs/、*.db、*.json
├── docker-compose.yml
├── DEPLOYMENT.md
├── VERSION                  # 版本号（唯一来源，发版时改这里）
├── CHANGELOG.md             # 更新日志：每个版本一节，分「新增 / 优化 / 修复」
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
- **识别失败的 SKC 默认删除所在行**（用户可在预览中取消，改为只删勾选项或全部保留）：预览返回 `unrecognized_skc_total` /
  `unrecognized_skcs_truncated` / `unrecognized_skcs`（每项 `skc`/`count`/`rows`/`rows_truncated`，`rows` 是
  示例行号，供前端提示用户），该聚合基于全表，不受 `result_filter` 与分页影响；提交任务时前端传
  `drop_unrecognized_skcs`（JSON 数组）或 `drop_all_unrecognized`（默认 true；显式 false 表示保留），
  随任务写入 `task_overrides`，服务重启后按原选择执行。
- 删除只作用于 `parse_skc` 返回 None 的行，且按 SKC 原文精确匹配（识别结果是 SKC 文本的纯函数，不会误删能
  识别的行）；申报价无法解析但 SKC 能识别的行仍走 `skipped_rows`，不受勾选影响。删除数量见
  `dropped_unrecognized_rows`，生效的勾选见 `drop_unrecognized_skcs`。
- 防护上限：`MAX_UNRECOGNIZED_SKCS`(200)、`MAX_UNRECOGNIZED_ROWS_PER_SKC`(20)、`MAX_DROP_SKC_LIST`(500)、
  `MAX_DROP_SKC_LENGTH`(200)；列表被截断时前端提示改用「删除全部无法识别的 SKC 所在行」。

### 其他领域

- 订单成本（`services/orders.py`）：货值 + 头程 + 操作费 + 续件费 + 尾程 - 运费补贴；头程减半名单命中时头程
  按 50%。**克重计算头程名单命中时，头程改用「克重 × 0.017」**（`services/weight_headcost.py`），不再按成本
  参数里的头程取值；两份名单同时命中时先取克重头程、再按减半规则 × 50%。
- 库存（`services/inventory.py`）：Excel 上传 → 解析 → 缓存（`price_cache.json`）→ SKU 查询；品类映射
  （A 映射式 / 其他自动式）。
- 任务（`services/taskpool.py` + `services/tasks.py` / `activity_tasks.py`）：提交入队 → 后台线程执行 →
  结果落 `data/`，任务记录在库。

### 店铺管理模块（可选，`bridge/` + `/api/store/*`）

- **准入**：`users.store_access`（管理员在「用户管理」逐个开通；管理员恒为 true）。未开通时前端隐藏入口、
  `/api/store/*` 一律 403；关闭权限会**同时强制退出**该用户的店铺会话（释放被占用的 Temu 账号）。
- **模型**：一用户一店铺（`temu_sessions`，`user_id` 主键），换店铺必须先退出；同一 Temu 账号全系统
  只允许一个活跃会话（`account_hash`，占用态含 `cooldown`）。
- **上游**：所有 Temu 请求经内网 `bridge/`（Node，`temu-ops` 的 fork），后端只用标准库 `urllib`
  调用（`services/temu_bridge.py`）。bridge 未配置时接口返回 503，其余功能不受影响。
- **bridge 补丁（勿随手覆盖上游代码）**：多账号会话池、按账号读写会话文件（修「取最新 session」串号）、
  `X-Bridge-Token` 鉴权、登录闸门（3s 间隔 / 5 次每分钟 / 54001 冷却 30 分钟）、**卡片按 toolId 翻页**
  （`toolCard({toolId, maxPages})` + `/kefu/cards`，卡片会被聊天记录挤出最近 50 条）。清单见
  `bridge/README.md`；**改动后必须跑 `node bridge/test/smoke.js`**（串号相关没有编译期保护）。
  上游的 `waitForResult` / `submitAndWait` **不要同步**：bridge 是单线程 + 每账号一把串行锁，
  一次等 15 分钟会把该账号所有请求堵死；回执轮询已在后端做（`refresh_reply` + 重试调度）。
- **凭证**：密码默认不入库；勾选「记住密码」时用 `services/credential_cipher.py`（HMAC-CTR +
  Encrypt-then-MAC，零依赖）以 `CREDENTIAL_KEY`（base64 32 字节）加密存 `temu_credentials`。
  该密钥留空时功能自动关闭，且**不要写进备份**。
- **会话失效不自动重登**：`40001` / 被浏览器顶号 → 状态置 `invalid`，要求用户手动重登
  （自动重登会烧风控配额，并刷新 54001 的冷却计时）。
- **写操作**（下架 / 发布到站点 / 价格刷新）对用户全部开放，靠三层防护：服务端首次 dryRun 预览 →
  前端二次确认 + 商品身份确认 → `temu_kefu_actions` 审计与机器人回执。
- **商品查询**（`services/store_goods.py`）：数据源是**「上新生命周期管理」**
  `POST /api/kiana/mms/robin/searchForSemiSupplier`（bridge `GET /goods/lifecycle`），
  商品级 100 字段 + `skcList[]`，**展平成 SKC 行**喂前端。
  **必须用服务端筛选 + 单页**（分页字段是 `pageNum`，不是 `page`）：`secondarySelectStatusList`
  （9 待选品 / 10 待处理 / 12 已加入站点 / 13 待价格校验）、`productNameKeyword`、
  `productSpuIdList` / `productSkcIdList` / `productSkuIdList`、`skcExtCodeList` / `skuExtCodeList`、
  `leafCatIdList`、`siteIdList`、`timeType` + `timeBegin/End`（1 创建 / 4 价格确认 / 6 加入站点 / 7 下架）、
  `priceReviewStatusList` / `sampleQcOrderStatusList` / `supplierTodoTypeList` / `punishStatusList` /
  `limitStatusList` / `removeStatus` / `reSampleTimesBegin|End`、`skipCheck: true`。
  搜索框支持批量粘贴（空格 / 逗号 / 换行 / 顿号分隔），前端用 `search_type`
  （auto / name / spu / skc / sku / extcode）决定进哪个参数；auto 时纯数字按 SKC、其它按商品名。
  ⚠️ **平台限流：连续约 8~10 次请求/分钟会返回 40001（登录态失效）**，所以**绝不能拉全量再本地过滤**
  （曾经的错误做法：全量 3 页 + 补销量 2 页 ≈ 每次刷新 5 个请求，几轮就把整个账号的会话打死，
  商品列表和客服卡片一起超时）。bridge 的 `withLock` 已加 **350ms 最小请求间隔**
  （`BRIDGE_MIN_GAP_MS`）；lifecycle 失败时自动回退商品列表接口（`skc/pageQuery`，
  状态按 `skcSiteStatus` 推导），不会把整页打成 500。详情仍走 `queryForImage`，**主图 = carousel[0]**。
- **客服只读**（`services/store_kefu.py`）：工具定义缓存 30 分钟（`toolId` 由服务端下发，禁止硬编码）；
  工具卡片**不缓存**（用户可能刚在 Temu 后台重开会话）；卡片缺失（bridge `EMPTY`）翻译成 404 + 引导用户
  先在 Temu 后台点开一次「联系官方客服」；会话消息服务端上限 50 条，已剥离 HTML。本模块不发任何消息。
- **客服写操作**（`services/store_kefu_actions.py` + `temu_kefu_actions` 表）：创建预览时**一次性**完成
  `queryProduct + preIntercept + 组装 content` 并落库；确认时只发送已存的 `request_body`，并通过
  `product`/`intercept` 字段把结果透传给 bridge 的 `kefu.submit`，跳过其内部的重复查询与拦截
  （`preIntercept` 有状态，重复调用会得到「请勿重复咨询」）。`confirm` 幂等：已 `sent/replied` 直接返回，
  绝不重复发消息。全局熔断开关是 `system.kefu_writes_enabled`（关闭 = 全员只读）。
- **商品创建**（`services/product_create.py` + bridge `/product/*`）：上游 `temu-ops` v1.1.0 能力，
  `bridge/lib/` 里的 `product.js` / `material.js` / `template_engine.js` 从上游直接复制（零依赖），
  **不要改这三份文件**，改了就失去与上游同步的依据。
  - 表单由**类目模板驱动**：`GET /product/template?catId=` 拿属性定义，`POST /product/template/describe`
    按当前已选值返回应显示字段（条件显示 / 值依赖 / 值级限制全部由上游 `TemplateEngine` 计算）。
    **前端绝不要自己推导依赖**，选完值就重新 describe 一次。
  - `selected` 的 key：下拉 `refPid`、纯数值 `num_<refPid>`、下拉+数值 `input_<refPid>`。
  - 属性请求体（`productPropertyReqs`）在 bridge 里由 `eng.buildPropertyReqs(selected)` 生成，
    前端只传 `selected` + `colorVid` + `sizeVid`。
  - 创建流程：`draft` → `draft/save` → `product/add`，**一个商品至少 3 个上游请求**；
    `dry_run` 默认 `true` 只走到 `draft/save`。
  - 硬约束（上游实测，别绕过）：类目路径 10 级全传（缺级报 `9th Level Category ID cannot be empty`）、
    价格单位是**分**、重量**毫克**、体积**毫米**、单品件数必须 1 且带 `pieceUnitCode`、
    素材图必须 1:1 ≥800×800（不能拿轮播图 3:4 顶替）、轮播图 3:4 ≥1340×1785。
  - **类目选择**（上游 v1.1.2）：全部走 SDK，**不要自己解析上游返回**（`categoryPaths` 元素是含
    `cat1NodeVO..cat10NodeVO` 的**对象**，不是二维数组，自己解析必成空）。
    - `searchCategories(keyword)`：**类目搜索框用的就是它**，多条模糊（如「情趣」返回 59 条）。
      底层 `POST /anniston-agent-seller/category/search`，⚠️ **参数名必须是 `searchText`**
      （`keyword` / `query` / `searchValue` 全部返回空——上游 1.1.1 曾因此误判「接口无效」），
      `querySource` 固定 `mms_create_good`。
    - `suggestCategory(name)`：按商品名推荐，1 条精确（对应官网「商品名失焦」）。
      ⚠️ 1.1.2 把旧的 `searchCategory` 改名成 `suggestCategory`，旧名已不存在。
    - `categoryHints()`：新建页顶部「常用推荐」5 条。
    - `categoryChildren(parentCatId)` 逐级树（`parentCatId=0` 取一级，`isLeaf` 才是可选类目）；
      `categoryPath(catIds)` 反查中文层级名。
      前端组件 `TemuCategoryPicker.vue`（搜索默认走 `searchCategories` + 常用推荐兜底）——
      **注意与项目内的业务品类 `CategoryPicker.vue` 是两个东西**，后者是 A/B/C 品类选择器，
      曾被误覆盖过一次（`git checkout` 恢复）。
  - **配置驱动创建**（v1.1.5~v1.1.7，**建品成功率的关键**）：
    `createProduct` 内部并行拉 模板 + `categoryConfig` + 环境，先跑 `validateByConfig`，
    不通过就直接返回 `stage='validate'` + `errors[]`（不发任何请求）。
    ⚠️ **`6000002 货品类目属性更新` 是误导性报错**，真实原因按此顺序查：
    ① 必填属性漏了数值型（克重类）② 英文名 <20 字 ③ 图片规格 ④ 单品/多件字段差异。
    v1.1.7 修了 `requiredProps()` 漏掉数值型属性的 Bug——**旧版 engine 会让「面料克重」这类必填项
    永远填不上，必然报 6000002**，升级时务必同步 `template_engine.js`。
    英文名下限定为 `EN_NAME_MIN = 20`（半托管硬性），`normalize_form` 已前置拦截。
    素材图可留空：`autoMaterialImg` 会从首图自动裁 1:1。
  - **表单字段清单**（v1.1.3 补齐，`buildBody` 支持的就这些，超出的传了也不生效）：
    颜色 `colorVid`（两级：`colorGroups()` 色系→颜色）、尺码 `sizeVid`（`sizeGroups()` 体系→尺码，
    站点默认组由 `sizeSpecConfig(catId).groupIdConfigResp.siteId2GroupIdsMap` 决定）、
    `origin{countryShortName, region2Id}` + `manufacturing[]`（`regions()` 给候选）、
    `isSensitive/sensitiveTypes/sensitiveList`（类目配置 `matchSensitiveConfigList` 非空时自动置位）、
    `skuClassification`(1 单品 / 2 多件) + `pieces` + `individuallyPacked`（**仅多件可填**）、
    `warehouseIds[]` + `stock`（多仓按 `warehouseStockQuantityReqs` 拆分）、`freightTemplateId`、
    `sizeTemplateIds[]`、`volume{len,width,height,inputXxx}` + `weight`（**毫米/毫克**）、
    `referenceLink`、`suggestedRetailPrice`。
    **详情图文 = `goodsLayerDecorationReqs`**，走 `form.decorationImages[]`（每张一个楼层，
    3:4 / 1350×1800），不传则回退首张轮播图。⚠️ **详情图文 ≠ `productGuideV2`**
    （后者是说明书文件 `productGuideFileNewReqList`），两者别混。
    固定枚举别硬编码：`GET /product/constants` 返回 `SENSITIVE_TYPES`（1纯电/2内电/3液体/4粉末/
    5磁性/6膏体/7刀具）与 `SKU_CLASSIFICATION`（1单品/2同款多件装/3混合套装）。
    产地：`regions()` 真实字段是 `productOriginRegions`（国家，取 `shortName`）/
    `productOriginZhRegion2List`（省份，取 `code` 填 `region2Id`）/`manufacturingLocationRegion1List`，
    **不是数组按索引取**。库存创建时**非必填**。
  - **尺码的三套东西别混**（v1.2.2 澄清）：
    ① 模板里的尺码值 `eng.sizeGroups()` → 填 `productSpecPropertyReqs`（我们用它做「尺码体系切换」）；
    ② 尺码体系分组 `sizeTypeConfig()` → `sizeType → specId 集合`，不直接进 body；
    ③ **尺码表模板** `sizeTemplateList(catId)` → 用户保存的尺码表，
    `businessId` 才是 `sizeTemplateIds` 的值（官网「尺码表 中国码(29)」指的是这套）。
  - **表单联动是前端约束**（v1.2.2）：没选尺码时价格/库存/体积重量不可填，后端不校验顺序。
  - **发货仓**：`warehouseList(siteId)` 给「站点↔仓库」绑定（`all` = 各站点的 `warehouseIdList`，
    提交时变成 `productWarehouseRouteReq.targetRouteList`）；**仓库名称只在 `sendWarehouses()` 里**
    （`warehouseId` / `warehouseName` / `coverSites`），`warehouseList` 只有 ID。
    `form.warehouseIds` 优先于 `env.warehouseIds`，允许用户跨站调整。
  - **批量创建**（`product_create.start_batch`）：后台线程串行，`ITEM_INTERVAL_SECONDS = 20`
    （3 请求/商品 × 20 秒 ≈ 贴着 8~10 次/分钟限流线），单批上限 50；进度存内存
    （重启丢失），单条失败不拖垮整批。**绝不允许并发创建**。
- **后台页面**：`/admin/store-sessions`（会话总览 + 强制退出）、`/admin/store-logs`（登录/失效/冷却事件）、
  `/admin/kefu-actions`（跨用户写操作审计），对应接口 `/api/admin/store-sessions`、`/api/admin/store-logs`、
  `/api/admin/kefu-actions`，全部走 `admin_user` 依赖；**所有后台视图里的 Temu 账号一律脱敏**。
- **批量执行**（`services/store_kefu_batch.py` + `temu_kefu_batches` 表）：一批编号由后端线程串行处理，
  每项之间按 `MIN_SUBMIT_INTERVAL`（`store_kefu_actions` 里的全局常量，当前 5 秒；上游实测 15 秒稳定，
  这里按运营要求取更激进的 5 秒，靠降级/失败重试兜底）间隔（拦截检查有状态 + 平台隐形限流，绝不能并发）；
  单批上限 50（约 4 分钟）；`temu_kefu_actions.batch_id` 关联每项结果，失败也落一条 `failed`。线程入口是
  模块内的 `_spawn()`，**测试替换它，不要 patch 全局 `threading.Thread`**（会连带改掉 pytest/anyio 自己的线程池，直接卡死）。
- **回执分类**（`store_kefu_actions.classify_reply`）：判定顺序必须是 `contentType=12` 终态 → 失败话术
  （系统忙碌/稍后重试…）→ **降级话术（加速等待）** → 受理话术（请稍候/正在处理/排队中）→ 业务拒绝 → `unknown`。
  ⚠️「加速等待」**不是受理**，是平台降级信号（提交没进同步执行器，几分钟后必然「系统忙碌」）；
  「稍后」这类宽泛词只能放在失败词表里。时间窗兜底不能取最早一条，要按「终态 > 失败/拒绝 > 最新」挑。
- **卡片上下文**（`store_kefu.ensure_card` → bridge `POST /kefu/ensure`）：
  ⚠️ 唤醒卡片**不是发文本**，而是 **QuickTool**：先 `POST /kefu/connect`
  （`responseChatWithCustomerSupportV2`，`{consultantBizAccountType:1, platSource:1001, sendRobotHotFaqMsg:true}`；
  会话闲置 2~3 小时变 off，此时机器人完全不响应），再发 `sendMessage contentType=10` +
  `{desc, id: toolId, scene: 1}`，机器人 2~3 秒后推 `contentType=6` 卡片（卡片 msgId 即 parentMsgId）。
  卡片按「账号+工具」缓存 10 分钟（`FRESH_CARD_MS`），提交前再用 3 分钟阈值校验
  （`SUBMIT_CARD_MAX_AGE_MS`）；`selfServiceTools` 刚 connect 后可能返回空列表，已加 2 秒 × 3 次重试。
  降级重试走 60 秒短退避（`DEGRADED_RETRY_DELAY`），失败重试 2/7/15 分钟。
- **失败重试**（`services/store_kefu_retry.py`）：`retry_pending` + `next_retry_at`（2/7/15 分钟退避、
  最多 3 次），重试**只发已存的 `request_body`**（`_resend_action` → `_send_action`），绝不重新
  `queryProduct` / `preIntercept`。调度器每 60 秒处理 1 条（`BATCH=1`），远超 `MIN_SUBMIT_INTERVAL`，
  不会自己撞自己的限流；可在系统设置用 `kefu_auto_retry_enabled` 关闭。

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

### 版本号与更新日志
- `VERSION`（语义化版本，如 `0.6.0`）是版本号唯一来源；`CHANGELOG.md` 记录每个版本改了什么，分「新增 / 优化 / 修复」。
- 发版：改 `VERSION` + 在 `CHANGELOG.md` 顶部加一节 → 提交推送；CI 会额外构建 `v<版本号>` 标签的镜像。
- 后端 `GET /api/version`（管理员）返回版本号、提交号与解析后的日志；运维边车的 `/status` 返回当前版本与远端版本。
- 日志解析逻辑在 `backend/app/version.py` 与 `docker/updater/server.py` 各有一份（边车是独立镜像），改动要同步。
- **改动 `docker/updater/**`（边车代码）后，必须在服务器重跑 `./scripts/enable-updater.sh` 才生效**：一键更新只重建前后端，边车按设计不重建自己（保证更新失败时还能回滚）。
