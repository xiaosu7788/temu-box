# Temu-Box Bridge

内网 HTTP 桥接服务：把 [temu-ops](https://github.com/) 的零依赖逆向客户端包装成
**多账号会话池**，只给 temu-box 后端调用。店铺管理模块（商品查询 / 客服自助工具）
的所有上游请求都从这里出去。

## 为什么需要它

temu-ops 的上游 `ops_server.js` 是**单账号单例**（模块级 `const ops = new TemuOps()`），
而本系统要求「每个用户登录自己的店铺」。直接把上游放进多用户环境会立刻串号，
所以这里做了 fork 补丁。

## 与上游的差异（补丁清单）

| # | 位置 | 上游行为 | 本仓库行为 |
|---|---|---|---|
| 1 | `server.js` | 全局单例 `ops` | `Map<accountKey, 会话>`，不同账号并行、同账号串行（`withLock`） |
| 2 | `lib/glo_client.js` | `loadKjCookies()` 扫目录取**最新** `session_*.json` | 改为按 `account` 精确读取，无账号返回 `null`（**串号根因**） |
| 3 | `lib/glo_client.js` | `new GloClient()` 无参 → 走上面那条"取最新" | 构造必须带 `account`，`sessionFile` 按账号命名 |
| 4 | `lib/temu_ops.js` | `probe = new GloClient()`、`glo = new GloClient()` | 全部传 `{ account, mallId }` |
| 5 | `lib/temu_ops.js` | `fetchMallInfo()` 静默吞掉 userInfo 失败 | 无 `result` 时主动抛错，触发 KJ 重登而不是带空 cookie 硬走 |
| 6 | `lib/glo_client.js` | 只取 `companyList[0].malInfoList[0]` | 收集 `mallList`，支持 `mallId` 指定店铺 |
| 7 | `server.js` | 无鉴权、暴露 `/api` 直通与 `/orders`、`/region` | 强制 `X-Bridge-Token`；只暴露 session/goods/kefu 白名单 |
| 8 | `server.js` | 无登录限流 | 同账号 ≥3s、60s ≤5 次、`54001` 冷却 30 分钟 |
| 9 | `server.js` | 密码长期留在 `ops.opts` 里 | 登录成功后立刻清空密码，会话失效要求用户重新登录 |
| 10 | `lib/glo_client.js` | 会话文件默认权限 | 写入时 `mode: 0o600` |
| 11 | `lib/kefu_offline.js` | `toolCard()` 只查最近 50 条（卡片被挤出后永远找不到） | 支持 `toolId` 过滤 + `maxPages` 翻页（`direction:1`），并新增 `allToolCards()` |
| 12 | `server.js` | 无 `/kefu/cards` | 新增 `/kefu/cards` 列出各工具卡片；`/kefu/card` 支持 `toolId`/`maxPages` |
| 13 | `lib/kefu_offline.js` | 无「唤醒卡片」能力 | 新增 `wakeTool(toolName)`（发工具名文本让机器人推卡片）与 `ensureToolCard(toolName)`（先翻页找、找不到再唤醒） |
| 14 | `server.js` | 无 `/kefu/wake` | 新增 `POST /kefu/wake`；提交前缺卡片上下文会被平台降级（回执「加速等待」），必须靠它修复 |
| 15 | `lib/kefu_offline.js` | `wakeTool` 发工具名**文本**（无效） | 改为 **QuickTool**：`sendMessage contentType=10` + `{desc,id:toolId,scene:1}`；新增 `connectSession()`（`responseChatWithCustomerSupportV2`），会话 off 时机器人不响应任何消息 |
| 16 | `server.js` | 无 `/kefu/connect` | 新增 `POST /kefu/connect`（建立/恢复客服会话，拿 `receptionId`）；`/kefu/wake` 支持 `toolId`/`autoConnect` |
| 17 | `lib/lifecycle.js` + `server.js` | 无上新生命周期接口 | 新增 `lib/lifecycle.js`（robin `searchForSemiSupplier` / 站点 / 快捷筛选 / 处罚 / 下架原因）与 `GET /goods/lifecycle[/all|/sites|/filters]`；商品查询改用该接口做数据源 |

## 接口

```
GET  /ping                          存活（Docker healthcheck，无需 token）
GET  /health                        汇总（脱敏账号列表）
GET  /health?account=               单账号会话状态（alive / mallId / mallList / cooldown）

POST /session/login   {account,password,mallId?}
POST /session/logout  {account}

GET  /goods/list?account=&page=&pageSize=&bindSiteIds=
GET  /goods/sites?account=&force=
GET  /goods/detail?account=&productId=

GET  /kefu/tools?account=
GET  /kefu/card?account=
GET  /kefu/messages?account=&limit=
POST /kefu/query     {account,type,id,toolId?}
POST /kefu/intercept {account,id,toolId?}
POST /kefu/submit    {account,...,dryRun}      // 默认 dryRun=true
POST /kefu/reply     {account,sinceMsgId}
```

响应统一：`{ ok: true, data }` / `{ ok: false, error: { stage, code, msg, retryAfterSeconds? } }`。
HTTP 状态：200 / 400 参数 / 401 未登录 / 403 token / 404 未知接口 / 429 限流或需验证码 / 500。

## 本地运行

```powershell
cd bridge
$env:BRIDGE_TOKEN = "dev-token"
$env:BRIDGE_ALLOW_NO_TOKEN = ""     # 生产不要设
node server.js                       # http://127.0.0.1:8898
node test/smoke.js                   # 离线自检（不联网、不登录真实账号）
```

## Docker

compose 里是 `stores` profile 服务，不 publish 端口，只在内网给 backend 用：

```bash
docker compose --profile stores up -d bridge
```

需要的环境变量：`BRIDGE_TOKEN`（必填，与后端 `STORE_BRIDGE_TOKEN` 一致）。
会话文件落在 `/data` 卷，退出登录即删。

## 升级上游时的注意点

上游改版只需要把这几个文件的新版本拷进来，然后重新套用上面 10 条补丁：
`lib/temu_ops.js`、`lib/glo_client.js`、`lib/fast_login.js`、`lib/kj_local.js`、
`lib/goods_list.js`、`lib/kefu_offline.js`、`lib/order_list.js`、`lib/region_switch.js`、
`runtime/anti_content/*`。

**每次升级后必须跑 `node test/smoke.js`**，尤其是补丁 1~4（串号相关），
它们没有编译期保护，只能靠自检覆盖。
