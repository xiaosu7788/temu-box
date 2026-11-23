/**
 * Temu 运营系统统一入口
 * ======================
 * 解决的核心问题：**会话唯一性**
 *
 *   ✗ 错误用法（会互相顶号）
 *     const g = new GoodsList();   await g.init();
 *     const o = new OrderList();   await o.init();     // ← 把 g 顶掉
 *     const k = new KefuOffline(); await k.init();     // ← 把 o 也顶掉
 *
 *   ✓ 正确用法
 *     const ops = new TemuOps();
 *     await ops.login();          // 全系统只登录一次
 *     await ops.goods.pageQuery();
 *     await ops.orders.query();
 *     await ops.kefu.selfServiceTools();
 *
 * 所有模块共享同一个 GloClient / RegionSwitcher。
 *
 * 用法：
 *   const { TemuOps } = require('./temu_ops');
 *
 *   const ops = new TemuOps();
 *   await ops.login();
 *
 *   // 商品
 *   const list = await ops.goods.pageQuery({ page: 1, pageSize: 20, siteIds: ['美国'] });
 *   const detail = await ops.goods.detail(list.result.pageItems[0].productId);
 *
 *   // 订单
 *   await ops.orders.query({ region: '加拿大' });
 *   await ops.orders.compareAllRegions();
 *
 *   // 客服自助工具
 *   await ops.kefu.selfServiceTools();
 *   await ops.kefu.submit({ skcId: 123, toolName: '商品下架', reason: 2001, dryRun: true });
 *
 *   // 区域切换
 *   await ops.switchRegion('us');
 *   await ops.backToGlo();
 */
const path = require('path');

const ERR_SESSION = new Set(['40001', '401', '403']);

class TemuOpsError extends Error {
  constructor(stage, code, msg, raw) {
    super(`[${stage}] ${msg || code}`);
    this.name = 'TemuOpsError';
    this.stage = stage;
    this.code = code;
    this.raw = raw;
  }
}

class TemuOps {
  constructor(opts = {}) {
    this.opts = opts;
    this.glo = null;
    this._goods = null;
    this._orders = null;
    this._kefu = null;
    this._region = null;
    this._loginAt = null;
    this._reLoginCount = 0;
  }

  // ==================== 登录 ====================
  /**
   * 登录（全系统只调一次）
   * @param {object} opts { force: 强制重新登录 }
   *
   * 流程：
   *   1. 自动续期 KJ 会话（有账号密码且存量会话失效时）
   *   2. GloClient 走 KJ → GLO 换码
   */
  async login(opts = {}) {
    if (this.glo && !opts.force) {
      const alive = await this._alive();
      if (alive) return this;
    }

    const account = this.opts.account || process.env.KJ_ACCOUNT || process.env.TEMU_ACCOUNT;
    const password = this.opts.password || process.env.KJ_PASSWORD || process.env.TEMU_PASSWORD;

    // 先尝试用存量 KJ 会话（必须带上 account，否则会读到别的账号的会话）
    let needKjLogin = opts.forceKj || false;
    if (!needKjLogin) {
      try {
        const { GloClient } = require('./glo_client');
        const probe = new GloClient({ account });
        probe.fetchMallInfoSync && probe.fetchMallInfoSync();
        await probe.fetchMallInfo();
      } catch (e) {
        if (/登录过期|40001|未登录|无 KJ|无 session/.test(e.message)) needKjLogin = true;
      }
    }

    // KJ 会话失效 → 账号密码重建
    if (needKjLogin || opts.forceKj) {
      if (!account || !password) {
        throw new TemuOpsError('login', 'KJ_EXPIRED',
          'KJ 会话已过期，需要账号密码。请设置 KJ_ACCOUNT / KJ_PASSWORD 环境变量，或 new TemuOps({ account, password })');
      }
      const kj = require('./kj_local');
      try {
        await kj.login(account, password, { force: !!opts.forceKj });
      } catch (e) {
        if (/54001|验证码/.test(e.message)) {
          throw new TemuOpsError('login', '54001', '需要图形验证码，请等待冷却（20~25 分钟）');
        }
        throw new TemuOpsError('login', 'KJ_LOGIN', e.message);
      }
    }

    const { GloClient } = require('./glo_client');
    const glo = new GloClient({ account, mallId: this.opts.mallId || null });
    await glo.fetchMallInfo(this.opts.mallId);

    // obtainCode 返回 code 字符串，失败会抛异常
    let code;
    for (let i = 0; i < 3; i++) {
      try { code = await glo.obtainCode(); break; } catch (e) {
        if (/54001|验证码/.test(e.message)) {
          throw new TemuOpsError('login', '54001', '需要图形验证码，请等待冷却（20~25 分钟）');
        }
        // KJ 会话刚过期的情况：自动重建一次
        if (/登录过期|40001/.test(e.message) && (account && password) && i === 0) {
          const kj = require('./kj_local');
          await kj.login(account, password, {});
          const g2 = new GloClient({ account, mallId: this.opts.mallId || null });
          await g2.fetchMallInfo(this.opts.mallId);
          code = await g2.obtainCode();
          Object.assign(glo, { kjJar: g2.kjJar, mallId: g2.mallId, uniqueId: g2.uniqueId, accountId: g2.accountId });
          break;
        }
        if (i === 2) throw new TemuOpsError('login', 'OBTAIN', e.message);
        await new Promise(s => setTimeout(s, 1500));
      }
    }
    if (!code) throw new TemuOpsError('login', 'OBTAIN', 'obtainCode 未返回 code');

    await glo.landPage(code);
    const lb = await glo.loginByCode(code);
    if (lb && String(lb.code) !== '1000000') {
      throw new TemuOpsError('login', lb.code, (lb.body && (lb.body.errorMsg || lb.body.error_msg)) || '换码登录失败', lb);
    }

    this.glo = glo;
    this._loginAt = Date.now();
    this._goods = null;
    this._orders = null;
    this._kefu = null;
    this._region = null;      // 区域会话随新登录重建
    return this;
  }

  /** 会话是否有效 */
  async _alive() {
    if (!this.glo) return false;
    try {
      const r = await this.glo.api('POST', '/api/seller/auth/userInfo', {});
      return String(r.code) === '1000000';
    } catch (e) { return false; }
  }

  /** 确保会话有效（遇到 40001 自动重登一次）*/
  async ensure() {
    if (await this._alive()) return true;
    if (this._reLoginCount >= 2) {
      throw new TemuOpsError('ensure', 'SESSION', '会话失效且重登次数已达上限');
    }
    this._reLoginCount++;
    await this.login({ force: true });
    return true;
  }

  /** 会话状态 */
  async health() {
    const alive = await this._alive();
    let userInfo = null;
    if (alive) {
      try {
        const r = await this.glo.api('POST', '/api/seller/auth/userInfo', {});
        const u = r.body.result || {};
        userInfo = {
          accountId: u.accountId,
          accountType: u.accountType,
          mallList: (u.mallList || []).map(m => ({
            mallId: m.mallId, mallName: m.mallName, mallMode: m.mallMode, mallStatus: m.mallStatus,
          })),
        };
      } catch (e) { }
    }
    return {
      alive,
      loginAt: this._loginAt,
      uptimeMs: this._loginAt ? Date.now() - this._loginAt : 0,
      mallId: this.glo ? this.glo.mallId : null,
      mallName: this.glo ? this.glo.mallName : null,
      // 登录时从 KJ userInfo 抓到的店铺列表（同一账号可能挂多个店铺）
      mallList: this.glo ? this.glo.mallList || [] : [],
      userInfo,
      modules: {
        goods: !!this._goods,
        orders: !!this._orders,
        kefu: !!this._kefu,
        region: !!this._region,
      },
    };
  }

  // ==================== 业务模块（共享会话）====================
  /** 认证模块（与 goods/orders/kefu/region 同级，对齐 temu_api 风格）*/
  get auth() {
    const self = this;
    return {
      login: (force) => self.login({ force }),
      health: () => self.health(),
      isAlive: async () => await self._alive(),
      ping: async () => ({ pong: true, ts: Date.now() }),
    };
  }

  /** 商品列表模块 */
  get goods() {
    if (!this._goods) {
      if (!this.glo) throw new TemuOpsError('goods', 'NOT_LOGIN', '请先 login()');
      const { GoodsList } = require('./goods_list');
      this._goods = new GoodsList(this.glo);
      this._goods.glo = this.glo;
    }
    return this._goods;
  }

  /** 订单列表模块 */
  get orders() {
    if (!this._orders) {
      if (!this.glo) throw new TemuOpsError('orders', 'NOT_LOGIN', '请先 login()');
      const { OrderList } = require('./order_list');
      this._orders = new OrderList(this.glo);
      this._orders.glo = this.glo;
    }
    return this._orders;
  }

  /** 客服自助工具模块 */
  get kefu() {
    if (!this._kefu) {
      if (!this.glo) throw new TemuOpsError('kefu', 'NOT_LOGIN', '请先 login()');
      const { KefuOffline } = require('./kefu_offline');
      this._kefu = new KefuOffline(this.glo);
      this._kefu.glo = this.glo;
    }
    return this._kefu;
  }

  /** 区域切换模块 */
  get region() {
    if (!this._region) {
      if (!this.glo) throw new TemuOpsError('region', 'NOT_LOGIN', '请先 login()');
      const { RegionSwitcher } = require('./region_switch');
      this._region = new RegionSwitcher(this.opts);
      this._region.init(this.glo);   // 复用会话，不重新登录
    }
    return this._region;
  }

  // ==================== 区域 ====================
  /** 切到某区域（glo/us/eu）*/
  async switchRegion(region, opts = {}) {
    return await this.region.switchTo(region, opts);
  }

  /** 回全球区 */
  async backToGlo() {
    return await this.region.switchTo('glo');
  }

  /** 当前区域 */
  currentRegion() {
    return this.region.current();
  }

  // ==================== 通用 ====================
  /** 直接打任意 GLO 接口 */
  async api(method, pathname, body, opts = {}) {
    if (!this.glo) throw new TemuOpsError('api', 'NOT_LOGIN', '请先 login()');
    const r = await this.glo.api(method, pathname, body, opts);
    if (ERR_SESSION.has(String(r.code)) && !opts.noRetry) {
      await this.ensure();
      return await this.glo.api(method, pathname, body, Object.assign({}, opts, { noRetry: true }));
    }
    return r;
  }

  /** 关闭（清理资源）*/
  close() {
    this._goods = this._orders = this._kefu = this._region = null;
    this.glo = null;
  }
}

module.exports = { TemuOps, TemuOpsError };

// ==================== CLI 自检 ====================
if (require.main === module) {
  (async () => {
    const ops = new TemuOps();
    const t0 = Date.now();
    await ops.login();
    console.log(`登录完成 (${Date.now() - t0}ms)\n`);

    const h = await ops.health();
    console.log('=== 会话 ===');
    console.log('  alive:', h.alive, '| mallId:', h.mallId);
    console.log('  账号:', h.userInfo && h.userInfo.accountId, '| 店铺:', h.userInfo && h.userInfo.mallList.map(m => m.mallName).join(','));

    console.log('\n=== 模块（共享会话）===');
    const g = await ops.goods.pageQuery({ page: 1, pageSize: 1 });
    console.log('  goods  → code=' + g.code + ' total=' + g.result.total + '   ✅ 会话保持');
    const o = await ops.orders.query({ pageSize: 1 });
    console.log('  orders → code=' + o.code + ' total=' + o.total + '   ✅ 会话保持');
    const k = await ops.kefu.selfServiceTools();
    console.log('  kefu   → ' + k.list.length + ' 个工具          ✅ 会话保持');
    const g2 = await ops.goods.pageQuery({ page: 1, pageSize: 1 });
    console.log('  goods 再查 → code=' + g2.code + '           ✅ 没被顶号');

    console.log('\n=== 区域 ===');
    const us = await ops.switchRegion('us');
    console.log('  切到 us:', us.host, '| envDr=' + await us.envDr());
    const usInfo = await us.userInfo();
    console.log('  us userInfo:', usInfo.code);
  })().catch(e => { console.error('ERR:', e.message); process.exit(1); });
}
