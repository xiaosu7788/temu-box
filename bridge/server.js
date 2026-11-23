/**
 * Temu-Box Bridge
 * ===============
 * 把 temu-ops 的多账号会话池暴露成内网 HTTP 接口，只给 temu-box 后端调用。
 *
 * 与上游 temu-ops/lib/ops_server.js 的差异（全部是「多账号 + 安全」补丁）：
 *   1. 全局单例 TemuOps → per-account 会话池（不同账号可并行，同账号串行）
 *   2. 每个请求必须带 account，会话文件按账号隔离（修掉"取最新 session"串号）
 *   3. 必须带 X-Bridge-Token；不暴露 /api 直通、/orders、/region
 *   4. 登录闸门：同账号 ≥3s、60s 内 ≤5 次、54001 冷却 30 分钟
 *   5. 不保存密码：登录成功后立刻从内存清掉，会话失效要求用户重新登录
 *
 * 启动：
 *   BRIDGE_TOKEN=xxx node server.js          # 默认 127.0.0.1:8898
 *   PORT=8898 HOST=0.0.0.0 BRIDGE_TOKEN=xxx node server.js
 *
 * 约定：
 *   成功 { ok:true, data:... }
 *   失败 { ok:false, error:{ stage, code, msg, retryAfterSeconds? } }
 *   HTTP 200 / 400 参数 / 401 未登录 / 403 token 错 / 429 需验证码 / 500 其他
 */
const http = require('http');
const path = require('path');
const fs = require('fs');
const { URL } = require('url');
const { TemuOps, TemuOpsError } = require('./lib/temu_ops');
const { Lifecycle } = require('./lib/lifecycle');
// 商品创建（temu-ops v1.1.0，零依赖，直接从上游复制）
const { Product } = require('./lib/product');
const { Material } = require('./lib/material');

const PORT = Number(process.env.PORT || 8898);
const HOST = process.env.HOST || '127.0.0.1';
const TOKEN = (process.env.BRIDGE_TOKEN || '').trim();
const ALLOW_NO_TOKEN = process.env.BRIDGE_ALLOW_NO_TOKEN === '1';
const DATA_DIR = process.env.TEMU_OPS_DATA || path.join(__dirname, 'runtime');
const RUNTIME_DIR = path.join(__dirname, 'runtime');

// 登录闸门参数（对应 PERFORMANCE_AND_RISK.md 的实测阈值，取 60% 安全区）
const LOGIN_MIN_INTERVAL_MS = Number(process.env.LOGIN_MIN_INTERVAL_MS || 3000);
const LOGIN_WINDOW_MS = 60 * 1000;const LOGIN_WINDOW_MAX = Number(process.env.LOGIN_WINDOW_MAX || 5);
const CAPTCHA_COOLDOWN_MS = Number(process.env.CAPTCHA_COOLDOWN_MS || 30 * 60 * 1000);

if (!TOKEN && !ALLOW_NO_TOKEN) {
  console.error('[bridge] 缺少 BRIDGE_TOKEN。生产环境必须设置；本地调试可设 BRIDGE_ALLOW_NO_TOKEN=1');
  process.exit(1);
}

// ==================== 账号工具 ====================
const accountKey = (account) =>
  require('crypto').createHash('sha1').update(String(account || '').trim().toLowerCase()).digest('hex').slice(0, 16);

/** 脱敏：a***@qq.com / 138****8888 */
function maskAccount(account) {
  const text = String(account || '');
  if (!text) return '';
  if (text.includes('@')) {
    const [name, domain] = text.split('@');
    return (name[0] || '') + '***@' + domain;
  }
  if (text.length >= 7) return text.slice(0, 3) + '****' + text.slice(-2);
  return text.slice(0, 1) + '***';
}

// ==================== 会话池 ====================
/** key = accountKey, value = 会话状态 */
const sessions = new Map();
/** key = accountKey, value = 串行锁尾 */
const locks = new Map();
// 同账号两次上游请求的最小间隔：平台连续 8~10 次/分钟会 40001，留 350ms 间隔比较安全
const MIN_REQUEST_GAP_MS = Number(process.env.BRIDGE_MIN_GAP_MS || 350);
const lastCallAt = new Map();

function sessionOf(account) {
  const key = accountKey(account);
  let state = sessions.get(key);
  if (!state) {
    state = {
      key,
      account: String(account).trim(),
      masked: maskAccount(account),
      ops: null,
      booting: null,
      loginAt: null,
      lastError: null,
      lastErrorCode: null,
      cooldownUntil: 0,
      loginHistory: [], // 仅记录时间戳，用于频率闸门
    };
    sessions.set(key, state);
  }
  return state;
}

/** 同一账号的请求串行执行：并发登录/查询会互相顶号 */
async function withLock(key, fn) {
  const previous = locks.get(key) || Promise.resolve();
  let release;
  const current = new Promise((resolve) => { release = resolve; });
  locks.set(key, previous.then(() => current));
  await previous;
  try {
    // 同账号请求最小间隔：平台连续 8~10 次/分钟会返回 40001（登录态失效），
    // 全量翻页 + 补销量叠在一起很容易触发
    const last = lastCallAt.get(key) || 0;
    const wait = MIN_REQUEST_GAP_MS - (Date.now() - last);
    if (wait > 0) await new Promise((r) => setTimeout(r, wait));
    lastCallAt.set(key, Date.now());
    return await fn();
  } finally {
    release();
  }
}

function pruneLoginHistory(state, now) {
  state.loginHistory = state.loginHistory.filter((ts) => now - ts < LOGIN_WINDOW_MS);
}

/** 登录闸门：返回 null 表示放行，否则返回 { code, msg, retryAfterSeconds } */
function loginGate(state) {
  const now = Date.now();
  if (state.cooldownUntil > now) {
    return {
      code: '54001',
      msg: '需要图形验证码，请冷却后再试',
      retryAfterSeconds: Math.ceil((state.cooldownUntil - now) / 1000),
    };
  }
  pruneLoginHistory(state, now);
  if (state.loginHistory.length >= LOGIN_WINDOW_MAX) {
    const oldest = state.loginHistory[0];
    return {
      code: 'RATE_LIMIT',
      msg: `登录过于频繁（每分钟最多 ${LOGIN_WINDOW_MAX} 次）`,
      retryAfterSeconds: Math.max(1, Math.ceil((oldest + LOGIN_WINDOW_MS - now) / 1000)),
    };
  }
  const last = state.loginHistory[state.loginHistory.length - 1];
  if (last && now - last < LOGIN_MIN_INTERVAL_MS) {
    return {
      code: 'RATE_LIMIT',
      msg: '登录间隔过短，请稍后再试',
      retryAfterSeconds: Math.ceil((LOGIN_MIN_INTERVAL_MS - (now - last)) / 1000),
    };
  }
  state.loginHistory.push(now);
  return null;
}

// 商品创建的客户端按需实例化（旧版 TemuOps 不挂 product/material）
function productOf(state) {
  // 传入 account 作为缓存命名空间：多用户并发时各自读自己的站点/仓库/模板
  if (!state._product) state._product = new Product(state.ops.glo, { account: state.key });
  return state._product;
}

function materialOf(state) {
  if (!state._material) state._material = new Material(state.ops.glo);
  return state._material;
}

/** 类目路径支持数组、">"、"/"、"," 分隔 */
function catPathOf(value) {
  if (Array.isArray(value)) return value.map(Number).filter(Boolean);
  return String(value || '').split(/[>\/,]/).map((x) => Number(String(x).trim())).filter(Boolean);
}

function leafCatOf(value) {
  const arr = catPathOf(value);
  return arr.length ? arr[arr.length - 1] : 0;
}

/** 取会话（未登录时抛 NOT_LOGIN，绝不自动重登） */
function requireOps(account) {
  if (!account) throw new TemuOpsError('session', 'ARGS', '需要 account 参数');
  const state = sessionOf(account);
  if (!state.ops) {
    throw new TemuOpsError('session', 'NOT_LOGIN', '该账号尚未登录，请先登录店铺');
  }
  return state;
}

// ==================== HTTP 工具 ====================
function send(res, status, obj) {
  if (res.writableEnded || res.headersSent) return;
  let body;
  try {
    body = JSON.stringify(obj);
  } catch (e) {
    body = JSON.stringify({ ok: false, error: { stage: 'serialize', code: 'ERR', msg: e.message } });
    status = 500;
  }
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-store',
  });
  res.end(body);
}

const ok = (res, data) => send(res, 200, { ok: true, data });

function fail(res, error) {
  const code = error.code || 'ERR';
  const status = code === 'NOT_LOGIN' ? 401
    : code === 'ARGS' ? 400
      : code === 'FORBIDDEN' ? 403
        : code === 'NOT_FOUND' ? 404
          : code === '54001' || code === 'RATE_LIMIT' ? 429
            : 500;
  send(res, status, {
    ok: false,
    error: {
      stage: error.stage || 'unknown',
      code,
      msg: error.message,
      ...(error.retryAfterSeconds ? { retryAfterSeconds: error.retryAfterSeconds } : {}),
    },
  });
}

const num = (value, fallback) => {
  if (value === null || value === undefined || value === '') return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};
const str = (value) => (value === null || value === undefined || value === '' ? undefined : String(value));

/**
 * 详情图文：纯图片楼层不能带 textModuleDetails。
 * SDK 的 _buildDecoration 会无条件塞 { fontSize:null, fontColor:null, ... }，
 * 平台只要看到这个对象就要求 fontColor 必填 → 400 "Font Color cannot be empty"。
 * buildBody 里 decorationReqs 优先于 decorationImages，这里直接构造干净的楼层。
 */
function toDecorationReqs(images) {
  if (!Array.isArray(images) || !images.length) return null;
  const base = Date.now();
  return images.map((item, i) => {
    const url = typeof item === 'string' ? item : (item && item.url);
    if (!url) return null;
    return {
      lang: 'zh',
      floorId: base + i * 32,
      key: 'DecImage',
      type: 'image',
      priority: i,
      contentList: [{ imgUrl: url, height: 1800, width: 1350 }],
    };
  }).filter(Boolean);
}

function readBody(req, maxBytes = 1024 * 1024) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > maxBytes) {
        reject(new TemuOpsError('body', 'ARGS', '请求体过大'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      const text = Buffer.concat(chunks).toString('utf8');
      if (!text) return resolve({});
      try {
        resolve(JSON.parse(text));
      } catch (e) {
        reject(new TemuOpsError('body', 'ARGS', '请求体不是合法 JSON'));
      }
    });
    req.on('error', reject);
  });
}

/** 账号参数：query 或 body 二选一 */
function accountFrom(req, url, body) {
  return str(url.searchParams.get('account')) || str(body && body.account);
}

// ==================== 登录 / 登出 ====================
async function login(account, password, mallId, verifyCode) {
  if (!account || !password) {
    throw new TemuOpsError('login', 'ARGS', '需要 account 与 password');
  }
  const state = sessionOf(account);

  if (state.ops) {
    // 已有会话：先杀掉旧会话再重建，避免单账号双会话互踢
    await logout(account).catch(() => { });
  }

  const gate = loginGate(state);
  if (gate) {
    const error = new TemuOpsError('login', gate.code, gate.msg);
    error.retryAfterSeconds = gate.retryAfterSeconds;
    state.lastErrorCode = gate.code;
    state.lastError = gate.msg;
    throw error;
  }

  if (!state.booting) {
    state.booting = (async () => {
      // 带验证码：先用 fast_login 建立 KJ 会话（ops.login 之后会复用存量会话，
      // 不会再走一遍密码登录），这样风控场景不用等 30 分钟冷却
      if (verifyCode) {
        const kj = require('./lib/kj_local');
        await kj.login(account, password, { verifyCode, force: true });
      }
      const ops = new TemuOps({ account, password, mallId: mallId || undefined });
      await ops.login();
      // 登录成功后立刻丢掉明文密码，后续会话失效要求用户重新登录
      if (ops.opts) {
        ops.opts.password = null;
        ops.opts.account = account;
      }
      state.ops = ops;
      // 关键：Product/Material 实例绑的是旧的 glo，必须重建
      state._product = null;
      state._material = null;
      // 换账号后必须清缓存：sites/regions/仓库 的缓存没有账号维度，
      // 不清就会拿上一个账号的站点和发货仓（表现为「该站点没有绑定发货仓」）
      try {
        require('./lib/product').clearCache();
      } catch (e) { /* 缓存清理失败不影响登录 */ }
      state.loginAt = Date.now();
      state.lastError = null;
      state.lastErrorCode = null;
      const health = await ops.health();
      return { health };
    })().finally(() => {
      state.booting = null;
    });
  }

  try {
    const { health } = await state.booting;
    return {
      account: state.masked,
      mallId: health.mallId,
      mallName: health.mallName,
      mallList: health.mallList,
      alive: health.alive,
      loginAt: state.loginAt,
    };
  } catch (e) {
    state.ops = null;
  if (state._product) state._product = null;
  try {
    require('./lib/product').clearCache();
  } catch (e) { /* 忽略 */ }
    state.lastError = e.message;
    state.lastErrorCode = e.code || 'ERR';
    const code = e && e.code ? String(e.code) : '';
    if (code === '54001' || code === 'CAPTCHA' || /54001|验证码/.test(e.message || '')) {
      // 已经提交过验证码还失败 → 是码错了/过期，别把用户锁进 30 分钟冷却
      if (verifyCode) {
        throw new TemuOpsError('login', 'CODE_INVALID', `验证码未通过：${e.message}`);
      }
      state.cooldownUntil = Date.now() + CAPTCHA_COOLDOWN_MS;
      const error = new TemuOpsError('login', '54001', '需要图形验证码，请 30 分钟后再试');
      error.retryAfterSeconds = Math.ceil(CAPTCHA_COOLDOWN_MS / 1000);
      throw error;
    }
    // 保留 KJ 侧的 errorCode，避免「密码错 / 账号异常」被统一报成「登录过期」
    if (e instanceof TemuOpsError) throw e;
    throw new TemuOpsError('login', code === 'KJ_LOGIN' ? 'KJ_LOGIN' : 'LOGIN_FAILED', e.message);
  }
}

/**
 * 发登录验证码
 * @param {string} account 邮箱/手机
 * @param {number} [contactType] 1=邮箱 0=手机，不传自动识别
 */
/**
 * 发货仓为空时定位原因：分别重跑三个接口，把错误原文带回前端。
 * 不能只报「未配置」——大概率是接口报错（会话/权限/站点ID 不匹配）。
 */
async function diagnoseEnv(ops, siteId, refresh) {
  const out = { siteId: siteId || null, errors: {}, counts: {} };
  const tasks = [
    ['warehouseRoute', () => ops.warehouseList(siteId, { refresh })],
    ['sendWarehouse', () => ops.sendWarehouses({ siteId })],
    ['freightTemplates', () => ops.freightTemplates([siteId], { refresh })],
  ];
  for (const [name, run] of tasks) {
    try {
      const r = await run();
      if (name === 'warehouseRoute') {
        out.counts.warehouseIds = (r && r.warehouseIds ? r.warehouseIds.length : 0);
        out.counts.routeSites = (r && r.all ? r.all.length : 0);
        out.counts.routeTemplateId = (r && r.templateId) || null;
      } else if (name === 'sendWarehouse') {
        out.counts.sendWarehouses = (r || []).length;
        // 该站点覆盖的仓库
        out.counts.sendWarehousesForSite = (r || []).filter(
          (w) => !siteId || (w.siteIds || []).some((s) => String(s) === String(siteId)),
        ).length;
      } else {
        out.counts.freightTemplates = (r || []).length;
      }
    } catch (e) {
      out.errors[name] = (e && e.message) || String(e);
    }
  }
  return out;
}

async function sendLoginCode(account, contactType) {
  if (!account) throw new TemuOpsError('login', 'ARGS', '需要 account');
  // 发码也走登录闸门，防止被刷短信/邮件
  const gate = loginGate(sessionOf(account));
  if (gate) {
    const error = new TemuOpsError('login', gate.code, gate.msg);
    error.retryAfterSeconds = gate.retryAfterSeconds;
    throw error;
  }
  const fast = require('./lib/fast_login');
  const r = await fast.sendCode(account, contactType === undefined ? {} : { contactType });
  const res = r.res || {};
  if (!r.ok) {
    throw new TemuOpsError(
      'login',
      'SEND_CODE',
      res.errorMsg || res.errorMsgI18n || res.msg || `发码失败（code=${res.errorCode}）`,
    );
  }
  return { sent: true, contactType: r.contactType };
}

async function logout(account) {
  if (!account) throw new TemuOpsError('logout', 'ARGS', '需要 account 参数');
  const state = sessionOf(account);
  const had = !!state.ops;
  if (state.ops) {
    try { state.ops.close(); } catch (e) { }
  }
  state.ops = null;
  state.loginAt = null;

  // 硬清理：会话文件必须删，否则下一个人用同账号登录会复用旧 cookie
  const files = [
    path.join(DATA_DIR, `session_${state.key}.json`),
    path.join(DATA_DIR, `glo_session_${state.key}.json`),
    path.join(RUNTIME_DIR, `session_${state.key}.json`),
    path.join(RUNTIME_DIR, `glo_session_${state.key}.json`),
  ];
  for (const file of files) {
    try { if (fs.existsSync(file)) fs.unlinkSync(file); } catch (e) { }
  }
  return { account: state.masked, loggedOut: true, hadSession: had };
}

// ==================== 路由 ====================
async function route(req, res, url) {
  const pathname = url.pathname;
  const query = url.searchParams;
  const method = req.method || 'GET';

  // 存活探测（Docker healthcheck）：不需要 token，不暴露任何信息
  if (pathname === '/ping') return ok(res, { pong: true, ts: Date.now() });

  // ---------- 以下全部需要 token ----------
  if (TOKEN) {
    const provided = req.headers['x-bridge-token'];
    if (!provided || provided !== TOKEN) {
      throw new TemuOpsError('auth', 'FORBIDDEN', 'X-Bridge-Token 无效');
    }
  }

  if (pathname === '/health') {
    const account = str(query.get('account'));
    if (account) {
      const state = sessionOf(account);
      if (!state.ops) {
        return ok(res, {
          account: state.masked, alive: false, loginAt: null,
          cooldownUntil: state.cooldownUntil || null,
          lastError: state.lastError, lastErrorCode: state.lastErrorCode,
        });
      }
      const health = await state.ops.health();
      return ok(res, { account: state.masked, ...health });
    }
    // 汇总视图（给后端排障用；只给脱敏账号）
    return ok(res, {
      total: sessions.size,
      sessions: [...sessions.values()].map((state) => ({
        account: state.masked,
        alive: !!state.ops,
        loginAt: state.loginAt,
        cooldownUntil: state.cooldownUntil || null,
      })),
    });
  }

  if (pathname === '/session/login' && method === 'POST') {
    const body = await readBody(req);
    const data = await login(
      str(body.account),
      str(body.password),
      str(body.mallId),
      str(body.verifyCode) || undefined,
    );
    return ok(res, data);
  }

  // 发登录验证码（触发风控时前端弹窗调用）
  if (pathname === '/session/send-code' && method === 'POST') {
    const body = await readBody(req);
    const type = body.contactType === undefined ? undefined : num(body.contactType, undefined);
    return ok(res, await sendLoginCode(str(body.account), type));
  }

  if (pathname === '/session/logout' && method === 'POST') {
    const body = await readBody(req);
    return ok(res, await logout(accountFrom(req, url, body)));
  }

  // ---------- 商品 ----------
  if (pathname === '/goods/list') {
    const state = requireOps(accountFrom(req, url, null));
    return await withLock(state.key, async () => {
      const result = await state.ops.goods.pageQuery({
        page: num(query.get('page'), 1),
        // 服务端上限 500（超过会被忽略），关键词搜索需要一次多取几页
        pageSize: Math.min(num(query.get('pageSize'), 20), 500),
        siteIds: str(query.get('site')) ? [str(query.get('site'))] : undefined,
        bindSiteIds: str(query.get('bindSiteIds'))
          ? String(query.get('bindSiteIds')).split(',').map(Number)
          : undefined,
        // 0 = 未发布到任何站点，1 = 已发布到至少一个站点（唯一有效的状态筛选参数）
        skcSiteStatus: str(query.get('skcSiteStatus')) ? Number(query.get('skcSiteStatus')) : undefined,
        // 服务端筛选（上游实测有效）：商品名模糊 / 批量 ID / 类目 / 创建时间范围
        productName: str(query.get('productName')) || undefined,
        productIds: str(query.get('productIds'))
          ? String(query.get('productIds')).split(',').map(Number).filter(Boolean)
          : undefined,
        productSkcIds: str(query.get('productSkcIds'))
          ? String(query.get('productSkcIds')).split(',').map(Number).filter(Boolean)
          : undefined,
        catIds: str(query.get('catIds'))
          ? String(query.get('catIds')).split(',').map(Number).filter(Boolean)
          : undefined,
        createdAtStart: str(query.get('createdAtStart')) ? Number(query.get('createdAtStart')) : undefined,
        createdAtEnd: str(query.get('createdAtEnd')) ? Number(query.get('createdAtEnd')) : undefined,
      });
      return ok(res, {
        code: result.code,
        total: result.result.total,
        items: result.result.pageItems || [],
      });
    });
  }

  // ---------- 上新生命周期管理（robin / searchForSemiSupplier）----------
  // 比商品列表多了 skcList[].selectStatus（生命周期阶段）和 statusTime（10 节点时间轴），
  // 但不返回销量 —— 销量仍要商品列表补。
  if (pathname === '/goods/lifecycle') {
    const state = requireOps(accountFrom(req, url, null));
    // 支持服务端筛选（上游实测参数）：只拉当前页，避免全量翻页触发 40001
    const numList = (name) => (str(query.get(name))
      ? String(query.get(name)).split(',').map(Number).filter((n) => !Number.isNaN(n))
      : undefined);
    const strList = (name) => (str(query.get(name))
      ? String(query.get(name)).split(',').filter(Boolean)
      : undefined);
    const numArg = (name) => (str(query.get(name)) ? Number(query.get(name)) : undefined);
    const strArg = (name) => str(query.get(name)) || undefined;

    return await withLock(state.key, async () => {
      const lifecycle = new Lifecycle(state.ops.glo);
      const result = await lifecycle.list({
        pageNum: num(query.get('pageNum'), 1),
        pageSize: Math.min(num(query.get('pageSize'), 20), 100),
        // 生命周期阶段（9 待选品 / 10 待处理 / 12 已加入站点 / 13 待价格校验）
        secondarySelectStatusList: numList('selectStatus'),
        // 商品名模糊
        productNameKeyword: strArg('keyword'),
        // 批量 ID（SPU / SKC / SKU）
        productSpuIdList: numList('productIds'),
        productSkcIdList: numList('productSkcIds'),
        productSkuIdList: numList('productSkuIds'),
        // 批量货号（SKC / SKU）
        skcExtCodeList: strList('extCodes'),
        skuExtCodeList: strList('skuExtCodes'),
        // 类目 / 站点
        leafCatIdList: numList('catIds'),
        siteIdList: numList('siteIds'),
        // 时间范围（1 创建 / 4 价格确认 / 6 加入站点 / 7 下架）
        timeType: numArg('timeType'),
        timeBegin: numArg('timeBegin'),
        timeEnd: numArg('timeEnd'),
        // 状态类筛选
        priceReviewStatusList: numList('priceReviewStatus'),
        sampleQcOrderStatusList: numList('sampleQcStatus'),
        supplierTodoTypeList: numList('todoTypes'),
        punishStatusList: numList('punishStatus'),
        limitStatusList: numList('limitStatus'),
        removeStatus: numArg('removeStatus'),
        reSampleTimesBegin: numArg('reSampleBegin'),
        reSampleTimesEnd: numArg('reSampleEnd'),
        skipCheck: true,
      });
      if (!result.ok) {
        throw new TemuOpsError('goods.lifecycle', String(result.code || 'FAIL'), '生命周期列表查询失败');
      }
      return ok(res, {
        total: result.total,
        items: result.items,
        aggregation: result.aggregation,
        scanned: result.items.length,
      });
    });
  }

  // 全量（自动翻页）：本地搜索/筛选用
  // ⚠️ 每页单独取锁：一次性占住整批翻页会把该账号的其他请求（商品列表、客服）全部饿死
  if (pathname === '/goods/lifecycle/all') {
    const state = requireOps(accountFrom(req, url, null));
    const pageSize = Math.min(num(query.get('pageSize'), 100), 100);
    const items = [];
    let total = null;
    for (let pageNum = 1; pageNum <= 20; pageNum++) {
      const page = await withLock(state.key, () =>
        new Lifecycle(state.ops.glo).list({ pageNum, pageSize }));
      if (!page.ok) {
        if (!items.length) {
          throw new TemuOpsError('goods.lifecycle', String(page.code || 'FAIL'), '生命周期列表查询失败');
        }
        break;
      }
      if (total === null) total = page.total;
      items.push(...page.items);
      if (page.items.length < pageSize) break;
      if (total && items.length >= total) break;
    }
    return ok(res, { total: total === null ? items.length : total, count: items.length, items });
  }

  // 站点基础信息（robin 的 48 个站）
  if (pathname === '/goods/lifecycle/sites') {
    const state = requireOps(accountFrom(req, url, null));
    return await withLock(state.key, async () => {
      const lifecycle = new Lifecycle(state.ops.glo);
      const result = await lifecycle.sites();
      return ok(res, { items: result.list });
    });
  }

  // 快捷筛选计数（生命周期 tab 的数字）
  if (pathname === '/goods/lifecycle/filters') {
    const state = requireOps(accountFrom(req, url, null));
    return await withLock(state.key, async () => {
      const lifecycle = new Lifecycle(state.ops.glo);
      const result = await lifecycle.quickFilterCount();
      return ok(res, { items: result.list });
    });
  }

  // 待办计数
  if (pathname === '/goods/lifecycle/todo') {
    const state = requireOps(accountFrom(req, url, null));
    return await withLock(state.key, async () => {
      const lifecycle = new Lifecycle(state.ops.glo);
      const result = await lifecycle.todoCount();
      return ok(res, { total: result.total, items: result.list });
    });
  }

  if (pathname === '/goods/sites') {    const state = requireOps(accountFrom(req, url, null));
    return await withLock(state.key, async () => {
      const list = await state.ops.goods.sites(query.get('force') === 'true');
      return ok(res, { total: list.length, sites: list });
    });
  }

  if (pathname === '/goods/detail') {
    const state = requireOps(accountFrom(req, url, null));
    const productId = str(query.get('productId'));
    if (!productId) throw new TemuOpsError('goods.detail', 'ARGS', '需要 productId');
    return await withLock(state.key, async () => {
      const detail = await state.ops.goods.detail(productId);
      if (!detail.ok) {
        throw new TemuOpsError('goods.detail', String(detail.code || 'FAIL'), '详情查询失败');
      }
      const { raw, ...rest } = detail;
      return ok(res, rest);
    });
  }

  // ---------- 客服自助工具 ----------
  // ==================== 商品创建 ====================
  // 商品名 → 类目（精确，通常 1 条；对应官网「商品名失焦」的行为）
  if (pathname === '/product/category/suggest' && method === 'POST') {
    const body = await readBody(req);
    const state = requireOps(str(body.account));
    const zh = str(body.productNameZh);
    const en = str(body.productNameEn);
    if (!zh && !en) throw new TemuOpsError('product.category', 'ARGS', '需要商品名称');
    return await withLock(state.key, async () => {
      const paths = await productOf(state).suggestCategory(zh, { enName: en });
      return ok(res, {
        total: paths.length,
        paths: paths.map((x) => ({ ...x, label: (x.names || []).join(' > ') })),
      });
    });
  }

  // 类目关键字搜索（模糊，多条；官网分类搜索框用的就是这个，参数名是 searchText）
  if (pathname === '/product/category/search' && method === 'POST') {
    const body = await readBody(req);
    const state = requireOps(str(body.account));
    const keyword = str(body.keyword);
    if (!keyword) throw new TemuOpsError('product.category', 'ARGS', '需要 keyword');
    return await withLock(state.key, async () => {
      const paths = await productOf(state).searchCategories(keyword, {
        isEn: !!body.isEn,
        onlySecondHand: !!body.onlySecondHand,
      });
      return ok(res, {
        total: paths.length,
        paths: paths.map((x) => ({ ...x, label: (x.names || []).join(' > ') })),
      });
    });
  }

  // 常用推荐（新建页顶部那 5 条）
  if (pathname === '/product/category/hints') {
    const state = requireOps(accountFrom(req, url, null));
    return await withLock(state.key, async () => {
      const paths = await productOf(state).categoryHints();
      return ok(res, {
        total: paths.length,
        paths: paths.map((x) => ({ ...x, label: (x.names || []).join(' > ') })),
      });
    });
  }

  // 发货仓（含仓库名称 + 覆盖站点）
  if (pathname === '/product/warehouses') {
    const state = requireOps(accountFrom(req, url, null));
    const siteId = num(query.get('siteId'), 0) || undefined;
    return await withLock(state.key, async () => {
      const list = await productOf(state).sendWarehouses(siteId ? { siteId } : {});
      return ok(res, { total: list.length, items: list });
    });
  }

  // 逐级类目树：parentCatId=0 取一级
  // 类目配置（哪些字段是必填、标题长度上限、敏感属性要求）
  // 产地 / 制造地点（真实字段：productOriginRegions / productOriginZhRegion2List / manufacturingLocationRegion1List）
  // 固定枚举（敏感类型 / SKU 分类），前端不要硬编码
  if (pathname === '/product/constants') {
    const { SKU_CLASSIFICATION, SENSITIVE_TYPES } = require('./lib/product');
    return ok(res, {
      skuClassification: Object.entries(SKU_CLASSIFICATION).map(([label, value]) => ({ label, value })),
      sensitiveTypes: SENSITIVE_TYPES,
    });
  }

  if (pathname === '/product/regions') {
    const state = requireOps(accountFrom(req, url, null));
    return await withLock(state.key, async () => {
      const raw = (await productOf(state).regions({ refresh: query.get('refresh') === '1' })) || {};
      const norm = (list, codeKeys, nameKeys) => (list || []).map((x) => {
        const code = codeKeys.map((k) => x && x[k]).find((v) => v !== undefined && v !== null && v !== '') ?? '';
        const name = nameKeys.map((k) => x && x[k]).find((v) => v) ?? String(code);
        return { code, name, shortName: (x && (x.shortName || x.code)) || '' };
      }).filter((x) => String(x.code) !== '');
      return ok(res, {
        // 产地国家：origin.countryShortName 取 shortName；省份：origin.region2Id 取 code
        originCountries: norm(raw.productOriginRegions, ['shortName', 'code'], ['chineseName', 'name']),
        cnProvinces: norm(raw.productOriginZhRegion2List, ['code'], ['chineseName', 'name']),
        manufacturingCountries: norm(raw.manufacturingLocationRegion1List, ['shortName', 'code'], ['chineseName', 'name']),
      });
    });
  }

  // 逐级类目树：parentCatId=0 取一级
  if (pathname === '/product/category/children') {
    // 后端用 GET，account 在 query 里；只在 POST 时才读 body
    const body = method === 'POST' ? await readBody(req) : {};
    const state = requireOps(accountFrom(req, url, body));
    const parentCatId = num(query.get('parentCatId') ?? body.parentCatId, 0);
    return await withLock(state.key, async () => {
      const nodes = await productOf(state).categoryChildren(parentCatId);
      return ok(res, { parentCatId, total: nodes.length, nodes });
    });
  }

  // 类目配置（哪些字段是必填、标题长度上限、敏感属性要求）
  if (pathname === '/product/category/config' && method === 'GET') {
    const state = requireOps(accountFrom(req, url, null));
    const leafCatId = num(query.get('leafCatId'), 0);
    if (!leafCatId) throw new TemuOpsError('product.categoryConfig', 'ARGS', '需要 leafCatId');
    const siteId = num(query.get('siteId'), 0);
    return await withLock(state.key, async () => {
      const config = await productOf(state).categoryConfig(leafCatId, {
        bindSiteIds: siteId ? [siteId] : [],
      });
      return ok(res, { config });
    });
  }

  // catId 反查完整类目路径（把手填 ID 显示成中文）
  if (pathname === '/product/category/path' && method === 'POST') {
    const body = await readBody(req);
    const state = requireOps(str(body.account));
    const catIds = (body.catIds || body.cat_ids || []).map(Number).filter((x) => x > 0);
    if (!catIds.length) throw new TemuOpsError('product.categoryPath', 'ARGS', '需要 catIds');
    return await withLock(state.key, async () => {
      const list = await productOf(state).categoryPath(catIds);
      return ok(res, { total: list.length, list });
    });
  }

  // 类目属性模板（前端据此渲染表单）
  if (pathname === '/product/template' && method === 'GET') {
    const state = requireOps(accountFrom(req, url, null));
    const catId = num(query.get('catId'), 0);
    if (!catId) throw new TemuOpsError('product.categoryTemplate', 'ARGS', '需要 catId');
    return await withLock(state.key, async () => {
      const ops2 = productOf(state);
      const template = await ops2.categoryTemplate(catId, {
        refresh: query.get('refresh') === '1',
      });
      // 前端要显示「模板已加载：N 个属性」，N 来自 engine.summary()，只回 template 拿不到
      let summary = null;
      try {
        summary = ops2.engine(template).summary();
      } catch (e) {
        summary = null;
      }
      return ok(res, { template, summary });
    });
  }

  // 按已选属性返回：应显示字段 / 颜色尺码选项
  if (pathname === '/product/template/describe' && method === 'POST') {
    const body = await readBody(req);
    const state = requireOps(str(body.account));
    const catId = num(body.catId, 0);
    if (!catId) throw new TemuOpsError('product.describe', 'ARGS', '需要 catId');
    const selected = body.selected && typeof body.selected === 'object' ? body.selected : {};
    return await withLock(state.key, async () => {
      const p = productOf(state);
      const template = await p.categoryTemplate(catId);
      const eng = p.engine(template);
      const pick = (fn, dflt = []) => { try { return fn(); } catch (e) { return dflt; } };

      // 尺码标准归并（模板 groupId → sizeSpec groupId）
      let groupCfg = null;
      let specToType = new Map();
      let typeMeta = [];
      try {
        const sc = await p.sizeSpecConfig(catId);
        groupCfg = (sc && sc.data && sc.data.groupIdConfigResp) || null;
      } catch (e) { groupCfg = null; }
      try {
        const tc = await p.sizeTypeConfig();
        typeMeta = (tc.sizeTypes || []).map((x) => ({ sizeType: x.sizeType, subTypes: x.subTypes || null }));
        for (const x of tc.sizeTypes || []) {
          for (const sid of x.specIds || []) specToType.set(Number(sid), x.sizeType);
        }
      } catch (e) { specToType = new Map(); }

      const sizeGroupsRaw = pick(() => eng.sizeGroups());
      const sizeGroups = sizeGroupsRaw.map((g) => ({
        ...g,
        sizes: (g.sizes || []).map((s) => ({ ...s, sizeType: specToType.get(Number(s.specId)) ?? null })),
      }));
      const sampleSpecIds = sizeGroupsRaw
        .flatMap((g) => (g.sizes || []).map((s) => s.specId))
        .filter((x) => x !== undefined && x !== null)
        .slice(0, 8);

      return ok(res, {
        summary: pick(() => eng.summary(), null),
        fields: pick(() => eng.describe(selected)),
        missing: pick(() => eng.validate(selected)),
        colors: pick(() => eng.colorOptions()),
        sizes: pick(() => eng.sizeOptions()),
        colorGroups: pick(() => eng.colorGroups()),
        sizeGroups,
        // v1.2.4 官方三层结构：标准 → 类型 → 尺码值
        sizeTree: pick(() => {
          const tree = eng.sizeOptionsTree();
          const out = {};
          for (const [std, types] of Object.entries(tree || {})) {
            out[std] = {};
            for (const [ty, list] of Object.entries(types || {})) {
              out[std][ty] = (list || []).map((s) => ({ vid: s.vid, value: s.value, specId: s.specId }));
            }
          }
          return out;
        }, {}),
        sizeTypes: typeMeta,
        sizeGroupConfig: groupCfg,
        sizeTypeDebug: {
          sampleSpecIds,
          specIdCount: specToType.size,
          matchedSizes: sizeGroupsRaw.flatMap((g) => g.sizes || []).filter((s) => specToType.has(Number(s.specId))).length,
          totalSizes: sizeGroupsRaw.flatMap((g) => g.sizes || []).length,
          rawSizeTypes: typeMeta.map((x) => x.sizeType),
        },
      });
    });
  }

  // 可售站点
  if (pathname === '/product/sites' && method === 'GET') {
    const state = requireOps(accountFrom(req, url, null));
    return await withLock(state.key, async () => {
      const raw = (await productOf(state).siteList({ refresh: query.get('refresh') === '1' })) || {};
      // ⚠️ 上游真实字段是 siteBaseList（历史踩过：写 siteList/siteVOS 永远拿不到数据，
      // 结果是下拉为空、el-select 退化成显示 siteId 原始数字）
      const items = raw.siteBaseList || raw.siteList || raw.siteVOS || raw.list
        || (Array.isArray(raw) ? raw : []);
      return ok(res, { total: items.length, items });
    });
  }

  // 店铺环境（仓库 / 运费模板 / 发货时效）
  if (pathname === '/product/env' && method === 'GET') {
    const state = requireOps(accountFrom(req, url, null));
    const siteId = num(query.get('siteId'), 0) || undefined;
    return await withLock(state.key, async () => {
      const ops2 = productOf(state);
      const refresh = query.get('refresh') === '1';
      // storeEnv 内部会把三个子请求的异常吞成空数组，导致「接口报错」被显示成「没配置」。
      // 这里单独再取一次带错误的诊断信息。
      const env = await ops2.storeEnv(siteId, { refresh });
      if (!env.warehouses || !env.warehouses.length) {
        const diag = await diagnoseEnv(ops2, siteId, refresh);
        env.diagnostics = diag;
        // 前端约定字段：storeEnv 吞掉的错误 / 全站仓库总数，用来区分
        // 「接口报错」「真的没配」「站点没绑定」三种情况
        env.warehousesError =
          diag.errors.sendWarehouse || diag.errors.warehouseRoute || '';
        env.warehousesAllCount = diag.counts.sendWarehouses || 0;
        env.routeSitesCount = diag.counts.routeSites || 0;
      }
      return ok(res, env);
    });
  }

  // 尺码体系映射（哪个站点用哪组尺码）
  if (pathname === '/product/size-spec-config' && method === 'GET') {
    const state = requireOps(accountFrom(req, url, null));
    const catId = num(query.get('catId'), 0);
    if (!catId) throw new TemuOpsError('product.sizeSpecConfig', 'ARGS', '需要 catId');
    return await withLock(state.key, async () => {
      return ok(res, await productOf(state).sizeSpecConfig(catId));
    });
  }

  // 尺码表模板（官网「尺码表」下拉：businessId 是「蓝本」，建表后才可提交）
  if (pathname === '/product/size-templates' && method === 'GET') {
    const state = requireOps(accountFrom(req, url, null));
    const catId = num(query.get('catId'), 0);
    if (!catId) throw new TemuOpsError('product.sizeTemplate', 'ARGS', '需要 catId');
    // 官网拉「尺码表」列表带 groupId（由站点决定）；不带会拿到不分组/别的组的模板
    let groupId = num(query.get('groupId'), 0);
    const templateSiteId = num(query.get('siteId'), 100);
    return await withLock(state.key, async () => {
      const p = productOf(state);
      if (!groupId) {
        groupId = await p.siteSizeGroupId(catId, templateSiteId).catch(() => null);
      }
      const r = await p.sizeTemplateList(catId, groupId ? { groupId } : {});
      return ok(res, {
        total: r.total || 0,
        groupId: groupId || null,
        list: (r.list || []).map((x) => ({
          businessId: x.businessId,
          classId: x.classId,
          name: x.name,
          generalSizeType: x.generalSizeType,
          sizeTypeName: x.sizeTypeName || '',
          sizes: x.sizes || [],
          rowCount: x.rowCount || 0,
          elements: x.elements || [],
          updatedAt: x.updatedAt || null,
        })),
      });
    });
  }

  // 素材库图片 / 视频（创建商品只能从这里选）
  if (pathname === '/product/images' && method === 'GET') {
    const state = requireOps(accountFrom(req, url, null));
    // 后端传的是 type（单数）；不传时默认「图片」=1，绝不能默认 0（那是视频）
    const typeRaw = query.get('type') ?? query.get('materialType') ?? query.get('types');
    const types = (typeRaw === null || typeRaw === undefined || typeRaw === String(typeRaw) && typeRaw === '')
      ? [1]
      : String(typeRaw).split(',').map((x) => Number(x)).filter((x) => !Number.isNaN(x));
    const name = (query.get('name') || '').trim();
    const pageNo = num(query.get('pageNo'), 1);
    const pageSize = num(query.get('pageSize'), 40);
    return await withLock(state.key, async () => {
      const r = await state.ops.glo.api('POST', '/phoenix-mms/material/pageQuery', {
        pageInfo: { pageNo, pageSize },
        // 只取「可用」(3)：1 转码中 / 2 失败 / 4 转存失败 的素材没有有效地址，
        // 混进来会让浏览器一直等超时，表现为整屏加载很慢
        uploadStatusList: [3],
        folderId: num(query.get('folderId'), 0),
        materialTypeList: types,
        ...(name ? { materialNameLike: name } : {}),
      });
      const payload = r.body && r.body.result;
      const raw = (payload && payload.materialList) || [];
      const list = raw.map((x) => {
        const isVideo = Number(x.materialType) === 0;
        const w = Number(x.width) || 0;
        const h = Number(x.height) || 0;
        return {
          id: x.id ?? null,
          // 图片用 imgUrl；视频没有 imgUrl，用封面首帧
          imgUrl: isVideo ? (x.firstImageUrl || '') : (x.imgUrl || ''),
          videoUrl: isVideo ? (x.destMaterialUrl || x.originalMaterialUrl || '') : '',
          name: x.materialName || '',
          md5: x.materialMd5 || '',
          width: w,
          height: h,
          size: Number(x.materialSize) || 0,
          proportion: x.proportion || '',
          materialType: Number(x.materialType),
          duration: Number(x.materialDuration) || 0,
          vid: x.vid || '',
          uploadStatus: Number(x.uploadStatus) || 0,
          failReason: x.failReason || '',
        };
      }).filter((x) => x.imgUrl || x.videoUrl);
      return ok(res, {
        total: (payload && payload.total) || list.length,
        list,
        requestedType: types[0],
        returnedTypes: [...new Set(list.map((x) => x.materialType))],
      });
    });
  }

  if (pathname === '/product/draft' && method === 'POST') {
    const body = await readBody(req);
    const state = requireOps(str(body.account));
    const catPath = catPathOf(body.catPath);
    if (!catPath.length) throw new TemuOpsError('product.draft', 'ARGS', '需要 catPath');
    return await withLock(state.key, async () => {
      const draftId = await productOf(state).createDraft(catPath, num(body.siteId, 100));
      return ok(res, { draftId });
    });
  }

  if (pathname === '/product/preview' && method === 'POST') {
    const body = await readBody(req);
    const state = requireOps(str(body.account));
    const form = body.form && typeof body.form === 'object' ? body.form : {};
    const catId = leafCatOf(form.catPath);
    if (!catId) throw new TemuOpsError('product.preview', 'ARGS', '需要 form.catPath');
    return await withLock(state.key, async () => {
      const p = productOf(state);
      const tmpl = await p.categoryTemplate(catId);
      const eng = p.engine(tmpl);
      const env = await p.storeEnv(num(form.siteId, 100));
      // 前端只传 selected，属性请求体在这里由模板引擎生成（依赖/条件全部交给 SDK）
      const merged = Object.assign({}, form);
      if (form.selected && !form.props) merged.props = eng.buildPropertyReqs(form.selected);
      if (Array.isArray(merged.sizeVids) && merged.sizeVids.length) merged.sizeVid = Number(merged.sizeVids[0]);
      const decoReqs = toDecorationReqs(merged.decorationImages);
      if (decoReqs) merged.decorationReqs = decoReqs;
      if (process.env.LOG) {
        const cp2 = Array.isArray(merged.catPath) ? merged.catPath : [];
        console.log('[preview] catPath=%s leafCat=%s sizeTemplateIds=%s sizeVids=%s',
          JSON.stringify(cp2),
          cp2[cp2.length - 1] || 0,
          JSON.stringify(merged.sizeTemplateIds),
          JSON.stringify(merged.sizeVids || merged.sizeVid));
      }
      const built = p.buildBody(merged, tmpl, env, eng, null);
      // 预览也按多尺码展开，SKU 数才对得上
      const vids = Array.isArray(merged.sizeVids) ? merged.sizeVids.map(Number) : [];
      if (vids.length > 1 && built.productSkcReqs && built.productSkcReqs[0]) {
        const skc0 = built.productSkcReqs[0];
        const base0 = skc0.productSkuReqs && skc0.productSkuReqs[0];
        if (base0) {
          const colorSpec = eng.buildSkuSpec('color', form.colorVid);
          skc0.productSkuReqs = vids.map((vid) => {
            const clone = JSON.parse(JSON.stringify(base0));
            clone.productSkuSpecReqs = [colorSpec, eng.buildSkuSpec('size', vid)];
            return clone;
          });
        }
      }
      // 预览也做一次尺码模板预检，否则会「预览全绿、点创建才炸 6000012」
      let sizeTemplateCheck = null;
      if (Array.isArray(merged.sizeTemplateIds) && merged.sizeTemplateIds.length) {
        try {
          sizeTemplateCheck = await p.validateSizeTemplate({
            catId: num(body.catId, 0) || leafCatOf(body.catPath),
            sizeTemplateIds: merged.sizeTemplateIds,
          });
        } catch (e) {
          sizeTemplateCheck = { ok: true, reason: '预检失败，已跳过' };
        }
      }
      return ok(res, {
        body: built,
        size: JSON.stringify(built).length,
        propertyCount: (built.productPropertyReqs || []).length,
        sizeTemplateCheck,
      });
    });
  }

  if (pathname === '/product/create' && method === 'POST') {
    const body = await readBody(req);
    const state = requireOps(str(body.account));
    const form = body.form && typeof body.form === 'object' ? body.form : {};
    if (!leafCatOf(form.catPath)) throw new TemuOpsError('product.create', 'ARGS', '需要 form.catPath');
    // 默认只走到 draft/save，显式 dryRun=false 才真正创建
    const dryRun = body.dryRun !== false;
    return await withLock(state.key, async () => {
      const p = productOf(state);
      const tmpl = await p.categoryTemplate(leafCatOf(form.catPath));
      const eng = p.engine(tmpl);
      const env = await p.storeEnv(num(form.siteId, 100));
      const merged = Object.assign({}, form);
      if (form.selected && !form.props) merged.props = eng.buildPropertyReqs(form.selected);
      // 多尺码：buildBody 只生成 1 个 SKU，这里按 sizeVids 展开（不改 SDK 文件）
      const sizeVids = Array.isArray(merged.sizeVids) && merged.sizeVids.length
        ? merged.sizeVids.map(Number)
        : null;
      if (sizeVids && sizeVids.length) merged.sizeVid = sizeVids[0];
      const decoReqs = toDecorationReqs(merged.decorationImages);
      if (decoReqs) merged.decorationReqs = decoReqs;
      if (process.env.LOG) {
        const cp = Array.isArray(merged.catPath) ? merged.catPath : [];
        console.log('[create] catPath=%s leafCat=%s sizeTemplateIds=%s sizeVids=%s',
          JSON.stringify(cp),
          cp[cp.length - 1] || 0,
          JSON.stringify(merged.sizeTemplateIds),
          JSON.stringify(merged.sizeVids || merged.sizeVid));
      }

      // SDK 的 create() 只取出 errorMsg，把上游完整响应丢了。
      // 6000012/6000002 这类码经常是笼统报错，真正原因在 body 的其它字段里，
      // 所以临时拦一层 glo.api 把 product/add 的原始响应打出来。
      let rawAdd = null;
      const gloObj = state.ops.glo || state.ops._glo;
      const origApi = gloObj && gloObj.api ? gloObj.api.bind(gloObj) : null;
      if (origApi && process.env.LOG) {
        gloObj.api = async (method, path, body) => {
          const rr = await origApi(method, path, body);
          if (String(path).includes('/product/add') && String(rr.code) !== '1000000') {
            rawAdd = rr;
          }
          return rr;
        };
      }
      let r;
      try {
        r = await p.createProduct(merged, { template: tmpl, env, dryRun });
      } finally {
        if (origApi && gloObj) gloObj.api = origApi;
      }
      if (process.env.LOG && rawAdd) {
        console.log('[create:raw] code=%s body=%s', rawAdd.code,
          JSON.stringify(rawAdd.body).slice(0, 4000));
      }
      if (process.env.LOG && rawAdd) {
        const b = r && r.body;
        if (b) {
          console.log('[create:size] sizeTemplateIds=%s showSizeTemplateIds=%s',
            JSON.stringify(b.sizeTemplateIds), JSON.stringify(b.showSizeTemplateIds));
          console.log('[create:size] goodsModelReqs=%s', JSON.stringify(b.goodsModelReqs));
          const skc = (b.productSkcReqs || [])[0] || {};
          const sku = (skc.productSkuReqs || [])[0] || {};
          console.log('[create:size] skuSpecs=%s', JSON.stringify(sku.productSkuSpecReqs));
          console.log('[create:size] specReqs=%s', JSON.stringify(b.productSpecPropertyReqs));
          console.log('[create:size] 尺码表相关字段=%s',
            JSON.stringify(Object.keys(b).filter((k) => /size/i.test(k)).map((k) => [k, b[k]])));
        }
      }
      if (process.env.LOG && r && r.ok === false) {
        // 失败时把上游完整响应吐出来：6000012/6000002 这类码经常是笼统报错，
        // 真正原因藏在 body 的其它字段里
        console.log('[create:fail] stage=%s code=%s msg=%s', r.stage, r.code, r.msg);
        console.log('[create:fail] full=%s', JSON.stringify({
          ok: r.ok, stage: r.stage, code: r.code, msg: r.msg,
          errors: r.errors, available: r.available, suggestion: r.suggestion,
          upstream: r.upstream, body: r.body, raw: r.raw,
        }).slice(0, 3000));
      }

      if (sizeVids && sizeVids.length > 1 && r.body && r.body.productSkcReqs) {
        const skc = r.body.productSkcReqs[0];
        const baseSku = skc.productSkuReqs && skc.productSkuReqs[0];
        if (baseSku) {
          const colorSpec = eng.buildSkuSpec('color', form.colorVid);
          skc.productSkuReqs = sizeVids.map((vid, idx) => {
            const clone = JSON.parse(JSON.stringify(baseSku));
            clone.productSkuSpecReqs = [colorSpec, eng.buildSkuSpec('size', vid)];
            // 第 1 个 SKU 保留用户填的原货号，其余才加后缀。
            // 全部加后缀会让用户填的货号一个都用不上，库存/订单按 extCode 对账会全错位。
            if (form.extCode) {
              clone.extCode = idx === 0 ? form.extCode : `${form.extCode}-${idx + 1}`;
            }
            return clone;
          });
        }
      }
      return ok(res, {
        ok: r.ok !== false,
        stage: r.stage,
        // v1.1.5 配置驱动校验的错误列表（stage === 'validate' 时）
        errors: r.errors || [],
        draftId: r.draftId,
        productId: r.productId || null,
        productSkcList: r.productSkcList || null,
        code: r.code || null,
        msg: r.msg || '',
        // 素材图留空时 SDK 会自动从首图裁 1:1，这里回传实际用的 URL
        materialImgUrl: r.materialImgUrl || '',
        propertyCount: ((r.body || {}).productPropertyReqs || []).length,
        size: r.body ? JSON.stringify(r.body).length : 0,
      });
    });
  }

  // 尺码模板预检：这个模板是否属于该叶子类目（6000012 的根因）
  if (pathname === '/product/size-template/validate') {
    const state = requireOps(accountFrom(req, url, null));
    const catId = num(query.get('catId'), 0);
    const ids = str(query.get('ids')) || '';
    const list = ids.split(',').map((x) => Number(x.trim())).filter(Boolean);
    if (!catId || !list.length) {
      throw new TemuOpsError('product.validateSizeTemplate', 'ARGS', '需要 catId 与 ids');
    }
    const gst = num(query.get('generalSizeType'), 0);
    const groupId = num(query.get('groupId'), 0);
    // 本次要提交的尺码值（M/L/one-size…），给了就一并比对模板的 sizes 覆盖
    const sizeValues = (str(query.get('sizeValues')) || '')
      .split(',').map((x) => x.trim()).filter(Boolean);
    return await withLock(state.key, async () => {
      const r = await productOf(state).validateSizeTemplate({
        catId,
        sizeTemplateIds: list,
        ...(gst ? { generalSizeType: gst } : {}),
        ...(sizeValues.length ? { sizeValues } : {}),
      });
      return ok(res, r);
    });
  }

  // 素材文件夹树
  // 压缩图片（超尺寸时用）
  if (pathname === '/product/material/compress' && method === 'POST') {
    const body = await readBody(req);
    const state = requireOps(str(body.account));
    const urls = (Array.isArray(body.urls) ? body.urls : []).filter(Boolean);
    if (!urls.length) throw new TemuOpsError('material.compress', 'ARGS', '需要 urls');
    return await withLock(state.key, async () => {
      const map = await materialOf(state).compressImage(urls);
      return ok(res, { compressMap: map || {} });
    });
  }

  // 服务端裁剪（已有 CDN 图 → 裁出新图）
  if (pathname === '/product/material/crop' && method === 'POST') {
    const body = await readBody(req);
    const state = requireOps(str(body.account));
    const url = str(body.url);
    const cut = body.cutConfig || {};
    if (!url || !cut.width || !cut.height) {
      throw new TemuOpsError('material.crop', 'ARGS', '需要 url 与 cutConfig.width/height');
    }
    return await withLock(state.key, async () => {
      const r = await materialOf(state).cropImage({ url, cutConfig: cut });
      return ok(res, r);
    });
  }

  if (pathname === '/product/folders') {
    const state = requireOps(accountFrom(req, url, null));
    return await withLock(state.key, async () => {
      const root = await materialOf(state).folderTree();
      // 归一化：{id, name, children}
      const norm = (node) => (node ? {
        id: Number(node.id ?? node.folderId ?? 0),
        name: node.folderName || node.name || '未命名',
        children: (node.children || node.childFolders || []).map(norm),
      } : null);
      return ok(res, { root: norm(root) });
    });
  }

  // 删除素材（批量下架）
  if (pathname === '/product/material/remove' && method === 'POST') {
    const body = await readBody(req);
    const state = requireOps(str(body.account));
    const imgIds = (Array.isArray(body.imgIds) ? body.imgIds : []).map(Number).filter(Boolean);
    const videoIds = (Array.isArray(body.videoIds) ? body.videoIds : []).map(Number).filter(Boolean);
    if (!imgIds.length && !videoIds.length) {
      throw new TemuOpsError('material.remove', 'ARGS', '需要 imgIds 或 videoIds');
    }
    if (imgIds.length > 100 || videoIds.length > 100) {
      throw new TemuOpsError('material.remove', 'ARGS', '单次最多 100 个');
    }
    return await withLock(state.key, async () => {
      const r = await materialOf(state).remove({ imgIds, videoIds });
      return ok(res, { ok: r.ok, msg: r.msg || '', count: imgIds.length + videoIds.length });
    });
  }

  if (pathname === '/product/upload-video' && method === 'POST') {
    // 视频走 base64，放宽 body 上限（默认 1MB 不够）
    const body = await readBody(req, 40 * 1024 * 1024);
    const state = requireOps(str(body.account));
    const content = str(body.contentBase64);
    if (!content) throw new TemuOpsError('product.uploadVideo', 'ARGS', '需要 contentBase64');
    const buf = Buffer.from(content, 'base64');
    if (buf.length > 30 * 1024 * 1024) {
      throw new TemuOpsError('product.uploadVideo', 'ARGS', '视频超过 30MB 限制');
    }
    return await withLock(state.key, async () => {
      const mat = materialOf(state);
      const r = await mat.uploadVideo(buf, str(body.fileName) || 'upload.mp4');
      return ok(res, {
        url: r.url || '',
        videoUrl: r.videoUrl || r.url || '',
        coverUrl: r.coverUrl || '',
        materialId: r.materialId || null,
        vid: r.vid || '',
        width: r.width || 0,
        height: r.height || 0,
        split: !!r.split,
        parts: r.parts || 0,
      });
    });
  }

  if (pathname === '/product/upload' && method === 'POST') {
    // 图片走 base64（膨胀 33%），默认 1MB 连 800KB 的图都传不了
    const body = await readBody(req, 28 * 1024 * 1024);
    const state = requireOps(str(body.account));
    const content = str(body.contentBase64);
    if (!content) throw new TemuOpsError('product.upload', 'ARGS', '需要 contentBase64');
    const buf = Buffer.from(content, 'base64');
    // 上游 1.3.10：官网前端硬拦是 5MB（旧的 2MB 是上游写错的）
    if (buf.length > 5 * 1024 * 1024) {
      throw new TemuOpsError('product.upload', 'ARGS', '图片超过 5MB 限制');
    }
    return await withLock(state.key, async () => {
      const mat = materialOf(state);
      // 上传时裁切（pic_operations.rules）：一次拿到最终图，不产生废素材
      const cw = num(body.cropWidth, 0);
      const ch = num(body.cropHeight, 0);
      const opts = cw > 0 && ch > 0 ? { crop: { width: cw, height: ch, dx: 0, dy: 0 } } : {};
      const r = await mat.upload(buf, str(body.fileName) || 'upload.jpg', opts);
      return ok(res, r);
    });
  }

  if (pathname === '/kefu/tools') {
    const state = requireOps(accountFrom(req, url, null));
    return await withLock(state.key, async () => {
      const result = await state.ops.kefu.selfServiceTools();
      if (!result.ok) throw new TemuOpsError('kefu.tools', 'FAIL', result.err || '读取工具失败');
      return ok(res, { total: result.list.length, tools: result.list });
    });
  }

  if (pathname === '/kefu/card') {
    const state = requireOps(accountFrom(req, url, null));
    // 卡片会被后续消息挤出最近 50 条窗口：支持按 toolId 精确找 + 自动向前翻页
    const toolId = str(query.get('toolId'));
    const maxPages = Math.min(num(query.get('maxPages'), 10), 50);
    return await withLock(state.key, async () => {
      const card = await state.ops.kefu.toolCard({ toolId: toolId || undefined, maxPages });
      if (!card.ok) throw new TemuOpsError('kefu.card', 'EMPTY', card.err || '未找到工具卡片');
      return ok(res, card);
    });
  }

  if (pathname === '/kefu/cards') {
    const state = requireOps(accountFrom(req, url, null));
    const maxPages = Math.min(num(query.get('maxPages'), 10), 50);
    return await withLock(state.key, async () => {
      const list = await state.ops.kefu.allToolCards({ maxPages });
      return ok(res, {
        total: list.length,
        cards: list.map((card) => ({
          toolId: card.toolId,
          msgId: card.msgId,
          dataType: card.dataType,
          optionTitle: card.optionTitle,
          optionItemList: card.optionItemList,
        })),
      });
    });
  }

  // ★ 建立/恢复客服会话：会话 off 时机器人不响应任何消息（发 QuickTool 也拿不到卡片）
  if (pathname === '/kefu/connect' && method === 'POST') {
    const body = await readBody(req);
    const state = requireOps(str(body.account));
    return await withLock(state.key, async () => {
      const result = await state.ops.kefu.connectSession({
        sendRobotHotFaqMsg: body.sendRobotHotFaqMsg !== false,
        platSource: num(body.platSource, 1001),
      });
      if (!result.ok) {
        throw new TemuOpsError('kefu.connect', 'FAIL', '建立客服会话失败（code=' + result.code + '）');
      }
      return ok(res, {
        receptionId: result.receptionId,
        resultCode: result.resultCode,
        waitCount: result.waitCount,
      });
    });
  }

  // ★ 唤醒工具卡片：往会话发一条工具名文本，机器人会推卡片
  //   没有新鲜卡片时提交会掉进异步队列（回执「加速等待序列」）
  if (pathname === '/kefu/wake' && method === 'POST') {
    const body = await readBody(req);
    const toolName = str(body.toolName);
    if (!toolName) throw new TemuOpsError('kefu.wake', 'ARGS', '需要 toolName');
    const state = requireOps(str(body.account));
    return await withLock(state.key, async () => {
      const woken = await state.ops.kefu.wakeTool(toolName, {
        toolId: str(body.toolId) || undefined,
        autoConnect: body.autoConnect !== false,
        timeoutMs: Math.min(num(body.timeoutMs, 30000), 120000),
      });
      if (!woken.ok) {
        throw new TemuOpsError('kefu.wake', woken.stage || 'FAIL', woken.err || '唤醒工具卡片失败');
      }
      return ok(res, {
        msgId: woken.msgId,
        toolId: woken.toolId,
        optionTitle: woken.optionTitle || null,
        optionItemList: woken.optionItemList || [],
        waitedMs: woken.waitedMs,
      });
    });
  }

  // 确保有「新鲜」的工具卡片：够新就复用，太旧就发工具名文本唤醒
  // 提交前必须走这里，否则卡片上下文过期 → 平台降级（回执「加速等待序列」）
  if (pathname === '/kefu/ensure' && method === 'POST') {
    const body = await readBody(req);
    const toolName = str(body.toolName);
    if (!toolName) throw new TemuOpsError('kefu.ensure', 'ARGS', '需要 toolName');
    const state = requireOps(str(body.account));
    return await withLock(state.key, async () => {
      const card = await state.ops.kefu.ensureToolCard(toolName, {
        toolId: str(body.toolId) || undefined,
        maxAgeMs: num(body.maxAgeMs, 600000),
        maxPages: Math.min(num(body.maxPages, 3), 20),
        timeoutMs: Math.min(num(body.timeoutMs, 30000), 120000),
      });
      if (!card.ok) {
        throw new TemuOpsError('kefu.ensure', card.stage || 'EMPTY', card.err || '未找到工具卡片');
      }
      return ok(res, {
        msgId: card.msgId,
        toolId: card.toolId,
        source: card.source || 'find',
        waitedMs: card.waitedMs || 0,
        optionTitle: card.optionTitle || null,
        optionItemList: card.optionItemList || [],
      });
    });
  }

  if (pathname === '/kefu/messages') {
    const state = requireOps(accountFrom(req, url, null));
    return await withLock(state.key, async () => {
      const result = await state.ops.kefu.messages({ limit: Math.min(num(query.get('limit'), 50), 50) });
      if (!result.ok) throw new TemuOpsError('kefu.messages', 'FAIL', result.err || '读取会话失败');
      return ok(res, {
        total: result.total,
        items: result.list.map((msg) => ({
          msgId: msg.msgId,
          contentType: msg.contentType,
          timestamp: msg.timestamp,
          content: String(msg.content || '').replace(/<[^>]+>/g, '').slice(0, 300),
        })),
      });
    });
  }

  if (pathname === '/kefu/query' && method === 'POST') {
    const body = await readBody(req);
    const state = requireOps(str(body.account));
    if (!body.type || !body.id) throw new TemuOpsError('kefu.query', 'ARGS', '需要 type(SKC/SKU/SPU) 和 id');
    return await withLock(state.key, async () => {
      const result = await state.ops.kefu.queryProduct(body.type, body.id, body.toolId);
      return ok(res, result);
    });
  }

  if (pathname === '/kefu/intercept' && method === 'POST') {
    const body = await readBody(req);
    const state = requireOps(str(body.account));
    if (!body.id) throw new TemuOpsError('kefu.intercept', 'ARGS', '需要 id');
    return await withLock(state.key, async () => {
      const result = await state.ops.kefu.preIntercept(body.id, body.toolId);
      return ok(res, result);
    });
  }

  if (pathname === '/kefu/submit' && method === 'POST') {
    const body = await readBody(req);
    const state = requireOps(str(body.account));
    return await withLock(state.key, async () => {
      const result = await state.ops.kefu.submit({
        skcId: body.skcId,
        skuId: body.skuId,
        spuId: body.spuId,
        toolName: body.toolName,
        toolId: body.toolId,
        reason: body.reason,
        detail: body.detail,
        parentMsgId: body.parentMsgId,
        // 关键：允许调用方传入已查到的商品与已消费的拦截结果。
        // 拦截检查（preIntercept）**有状态**，同一商品只能成功一次，
        // 重复调用会得到 interceptCode=1「请勿重复咨询」，所以必须复用。
        _product: body.product,
        intercept: body.intercept,
        // 默认只预览：只有显式 dryRun:false 才真正发消息
        dryRun: body.dryRun !== false,
      });
      if (!result.ok) {
        throw new TemuOpsError('kefu.submit', result.stage || 'FAIL', result.err || result.msg || '提交失败');
      }
      return ok(res, result);
    });
  }

  if (pathname === '/kefu/reply' && method === 'POST') {
    const body = await readBody(req);
    const state = requireOps(str(body.account));
    return await withLock(state.key, async () => {
      const result = await state.ops.kefu.reply(body.sinceMsgId);
      return ok(res, result);
    });
  }

  throw new TemuOpsError('router', 'NOT_FOUND', '未知接口: ' + pathname);
}

// ==================== 服务 ====================
const server = http.createServer(async (req, res) => {
  const started = Date.now();
  let account = '';
  try {
    if (req.method === 'OPTIONS') {
      res.writeHead(204, { 'Allow': 'GET,POST,OPTIONS' });
      return res.end();
    }
    const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
    account = maskAccount(url.searchParams.get('account') || '');
    await route(req, res, url);
  } catch (e) {
    if (!(e instanceof TemuOpsError)) {
      console.error('[ERR]', req.method, req.url.split('?')[0], '::', e.name, e.message);
      if (e.stack && process.env.DEBUG === '1') console.error(e.stack);
    }
    fail(res, e instanceof TemuOpsError ? e : new TemuOpsError('server', 'ERR', e.message));
  } finally {
    if (process.env.LOG !== '0') {
      const ms = Date.now() - started;
      const line = [
        new Date().toISOString().slice(11, 19),
        String(req.method).padEnd(4),
        String(req.url.split('?')[0]).padEnd(20),
        `${ms}ms`.padStart(7),
        account ? `account=${account}` : '',
      ].filter(Boolean).join(' ');
      console.log(line);
    }
  }
});

server.listen(PORT, HOST, () => {
  console.log(`[bridge] listening on http://${HOST}:${PORT}`);
  console.log(`[bridge] runtime=${DATA_DIR}`);
  console.log(`[bridge] token=${TOKEN ? 'enabled' : 'DISABLED (debug only)'}`);
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    console.log(`\n[bridge] ${signal}, shutting down…`);
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 2000);
  });
}

process.on('unhandledRejection', (reason) => {
  console.error('[bridge] unhandledRejection:', reason && reason.message ? reason.message : reason);
});

module.exports = { server };