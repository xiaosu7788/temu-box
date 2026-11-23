/**
 * 区域切换模块（精简版）
 * ======================
 * 只负责一件事：在 GLO / US / EU 三个域之间切换，拿到目标域的会话。
 *
 * ⚠️ 菜单 dr 归类、站点表不在这里 —— 那些属于业务模块（如商品列表）。
 *
 * 用法：
 *   const { RegionSwitcher } = require('./region_switch');
 *
 *   const rs = new RegionSwitcher();
 *   await rs.init();                     // 登录源域 GLO
 *   const us = await rs.switchTo('us');  // 切到美国区
 *   await us.userInfo();                 // 用目标域会话
 *
 * 后端：纯协议，无浏览器依赖
 *   1. POST /bg/swift/api/auth/obtainCode          → code（带 Anti-Content）
 *   2. GET  /auth/authentication?code=...          → 落地，拿 api_uid
 *   3. POST /bg/swift/api/auth/loginBySellerCode   → seller_temp
 *        body {"code":"...","codeFromDr":1}        codeFromDr 必填且是数字
 *        header Referer = 落地 URL，Anti-Content 必带
 */
const fs = require('fs');
const path = require('path');
const https = require('https');

const ANTI_PROJ = process.env.TEMU_OPS_ANTI || path.join(__dirname, '..', 'runtime', 'anti_content');
const DATA_DIR = process.env.TEMU_OPS_DATA || path.join(__dirname, '..', 'runtime');

// ==================== 区域定义 ====================
const REGIONS = {
  glo: { host: 'agentseller.temu.com', code: 1, label: '全球', envDr: 'us' },
  eu: { host: 'agentseller-eu.temu.com', code: 2, label: '欧区', envDr: 'eu' },
  us: { host: 'agentseller-us.temu.com', code: 3, label: '美国', envDr: 'udp' },
};

const agent = new https.Agent({ keepAlive: true, maxSockets: 8, keepAliveMsecs: 30000, timeout: 30000 });
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36 Edg/152.0.0.0';

// ==================== 工具 ====================
function raw(host, method, p, headers, body, timeoutMs) {
  return new Promise(resolve => {
    const data = body === undefined || body === null ? null
      : (typeof body === 'string' ? body : JSON.stringify(body));
    const h = Object.assign({
      'User-Agent': UA,
      'Accept': 'application/json, text/plain, */*',
      'Accept-Language': 'zh-CN,zh;q=0.9',
    }, headers || {});
    if (data !== null) {
      h['Content-Type'] = h['Content-Type'] || 'application/json';
      h['Content-Length'] = String(Buffer.byteLength(data));
    }
    const r = https.request({ hostname: host, port: 443, path: p, method, headers: h, agent, timeout: timeoutMs || 25000 }, res => {
      const cs = [];
      res.on('data', c => cs.push(c));
      res.on('end', () => resolve({
        status: res.statusCode, location: res.headers.location || null,
        setCookie: res.headers['set-cookie'] || [], text: Buffer.concat(cs).toString('utf8'),
      }));
    });
    r.on('error', e => resolve({ status: 0, err: e.message, setCookie: [], location: null, text: '' }));
    r.on('timeout', () => { r.destroy(); resolve({ status: 0, err: 'timeout', setCookie: [], location: null, text: '' }); });
    if (data !== null) r.write(data);
    r.end();
  });
}

const codeOf = j => (!j || typeof j !== 'object') ? undefined : (j.errorCode !== undefined ? j.errorCode : j.error_code);
const parse = t => { try { return JSON.parse(t); } catch (e) { return { raw: String(t).slice(0, 300) }; } };

class CookieJar {
  constructor(init) { this.map = Object.assign({}, init || {}); }
  str() { return Object.entries(this.map).map(([k, v]) => `${k}=${v}`).join('; '); }
  set(k, v) { this.map[k] = v; }
  eat(list) {
    for (const c of (list || [])) {
      const p = String(c).split(';')[0];
      const i = p.indexOf('=');
      if (i > 0) this.map[p.slice(0, i).trim()] = p.slice(i + 1).trim();
    }
    return this;
  }
  dump() { return { ...this.map }; }
  static from(x) {
    if (!x) return new CookieJar();
    if (x instanceof CookieJar) return x;
    if (typeof x.dump === 'function') { try { return new CookieJar(x.dump()); } catch (e) { } }
    if (x.map && typeof x.map === 'object') return new CookieJar(x.map);
    return new CookieJar(x);
  }
}

// ==================== 目标域会话 ====================
class RegionSession {
  constructor(region, host, jar, meta) {
    this.region = region;
    this.host = host;
    this.jar = CookieJar.from(jar);
    this.meta = meta || {};
  }

  /** 该域 DR 标签（/drogon-api/env）*/
  async envDr() {
    const r = await raw(this.host, 'GET', '/drogon-api/env?initiator=boost', {}, null, 15000);
    try { return JSON.parse(r.text).result.dr; } catch (e) { return null; }
  }

  /** 通用请求 */
  async api(p, body, opts = {}) {
    const hdr = {
      Origin: `https://${this.host}`,
      Referer: opts.referer || `https://${this.host}/`,
      'mallid': String(this.meta.mallId || ''),
    };
    hdr.Cookie = this.jar.str();
    const r = await raw(this.host, opts.method || 'POST', p, hdr, body === undefined ? {} : body, opts.timeoutMs);
    this.jar.eat(r.setCookie);
    const j = parse(r.text);
    return { status: r.status, code: codeOf(j), ok: String(codeOf(j)) === '1000000', body: j, raw: r.text };
  }

  /** 会话是否有效（用 userInfo 探）*/
  async alive() {
    const r = await this.api('/api/seller/auth/userInfo', {});
    return r.ok;
  }

  /** 用户信息（业务模块通用）*/
  userInfo() { return this.api('/api/seller/auth/userInfo', {}); }
}

// ==================== 主类 ====================
class RegionSwitcher {
  constructor(opts = {}) {
    this.opts = opts;
    this.glo = null;
    this.mallId = null;
    this.uniqueId = null;
    this.accountId = null;
    this.sessions = {};          // region -> RegionSession
  }

  // ---------- 初始化 ----------
  /** 登录源域（GLO）*/
  /**
   * 初始化
   * @param {GloClient} [gloClient] 复用已有会话（运营系统必须传，否则会顶号）
   */
  async init(gloClient) {
    if (gloClient) {
      this.glo = gloClient;
    } else {
      const { GloClient } = require('./glo_client');
      this.glo = new GloClient();
      await this.glo.fetchMallInfo();
      const c = await this.glo.obtainCode();
      await this.glo.landPage(c);
      await this.glo.loginByCode(c);
    }

    this.mallId = this.glo.mallId;
    this.uniqueId = this.glo.uniqueId;
    this.accountId = this.glo.accountId;

    this.sessions.glo = new RegionSession('glo', REGIONS.glo.host, this.glo.gloJar, {
      mallId: this.mallId,
    });
    this._persist();
    return this;
  }

  /** 从文件恢复（并校验）*/
  async restoreAndVerify() {
    if (!this.restore()) return false;
    const g = this.sessions.glo;
    if (!g) return false;
    const ok = await g.alive().catch(() => false);
    if (!ok) return false;
    return true;
  }

  restore() {
    const f = path.join(DATA_DIR, 'region_sessions.json');
    if (!fs.existsSync(f)) return false;
    try {
      const d = JSON.parse(fs.readFileSync(f, 'utf8'));
      if (Date.now() - d.at > 6 * 3600e3) return false;
      this.mallId = d.mallId; this.uniqueId = d.uniqueId; this.accountId = d.accountId;
      for (const [k, v] of Object.entries(d.sessions || {})) {
        this.sessions[k] = new RegionSession(k, v.host, v.cookies, { mallId: this.mallId });
      }
      return true;
    } catch (e) { return false; }
  }

  _persist() {
    try {
      if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
      const out = { at: Date.now(), mallId: this.mallId, uniqueId: this.uniqueId, accountId: this.accountId, sessions: {} };
      for (const [k, s] of Object.entries(this.sessions)) out.sessions[k] = { host: s.host, cookies: s.jar.dump() };
      fs.writeFileSync(path.join(DATA_DIR, 'region_sessions.json'), JSON.stringify(out, null, 2));
    } catch (e) { }
  }

  // ---------- 区域信息（纯配置，无业务）----------
  /** 当前区域 */
  current() {
    const k = this._currentRegion || 'glo';
    const r = REGIONS[k];
    return {
      region: k, host: r.host, label: r.label, code: r.code, envDr: r.envDr,
      hasSession: !!this.sessions[k],
    };
  }

  /** 可选区域列表 */
  static regions() {
    return Object.entries(REGIONS).map(([k, v]) => ({
      region: k, host: v.host, label: v.label, code: v.code, envDr: v.envDr,
    }));
  }

  listRegions() { return RegionSwitcher.regions(); }

  // ---------- 切换 ----------
  /**
   * 切到目标区域
   * @param {'glo'|'us'|'eu'} region
   * @param {object} opts { path }
   */
  async switchTo(region, opts = {}) {
    const r = REGIONS[region];
    if (!r) throw new Error(`未知区域: ${region}（可选 ${Object.keys(REGIONS).join(' / ')}）`);
    if (region === 'glo') { this._currentRegion = 'glo'; return this.sessions.glo; }

    const targetPath = opts.path || '/main/act/data';
    const targetUrl = `https://${r.host}${targetPath}`;

    const code = await this._obtainCode(targetUrl, r.code);
    if (!code) throw new Error('obtainCode 失败');

    const sess = await this._switchByHttp(region, r, targetUrl, code);

    this._currentRegion = region;
    this._persist();
    return sess;
  }

  /** 取会话（有则校验，无则切换）*/
  async session(region, opts = {}) {
    if (region === 'glo' && !this.sessions.glo) await this.init();
    if (this.sessions[region] && await this.sessions[region].alive().catch(() => false)) {
      this._currentRegion = region;
      return this.sessions[region];
    }
    return await this.switchTo(region, opts);
  }

  // ---------- 内部 ----------
  async _antiContent() {
    const mod = require(path.join(ANTI_PROJ, 'anti_content.js'));
    const gen = await mod.createAntiContent({ serverTime: Date.now() });
    try { gen.getCookies(); } catch (e) { }
    return gen.generate();
  }

  async _obtainCode(targetUrl, targetDr) {
    const src = this.sessions.glo;
    if (!src) throw new Error('未初始化，请先 init()');
    const ac = await this._antiContent();
    const hdr = {
      'Anti-Content': ac,
      Origin: 'https://agentseller.temu.com',
      Referer: 'https://agentseller.temu.com/main/act/data',
      'mallid': String(this.mallId),
    };
    hdr.Cookie = src.jar.str();
    const r = await raw('agentseller.temu.com', 'POST', '/bg/swift/api/auth/obtainCode', hdr,
      { redirectUrl: targetUrl, targetDr }, 20000);
    return (parse(r.text).result || {}).code || null;
  }

  _landingUrl(host, targetUrl, code) {
    const q = new URLSearchParams({
      cover: 'true',
      uId: String(this.accountId),
      uniqueId: String(this.uniqueId),
      code,
      codeFromDr: '1',
      redirectUrl: targetUrl,
    });
    return `https://${host}/auth/authentication?${q.toString()}`;
  }

  // ---------- HTTP 后端（纯协议，实测可用）----------
  /**
   * 纯 Node 跨域切换
   *
   * 流程（与浏览器一致）：
   *   1. POST /bg/swift/api/auth/obtainCode  → code        （带 Anti-Content）
   *   2. GET  /auth/authentication?code=...  → 落地拿 api_uid
   *   3. POST /bg/swift/api/auth/loginBySellerCode          （★ 关键）
   *        body {"code":"...","codeFromDr":1}  ← codeFromDr 必填，数字
   *        headers: Anti-Content + Referer=落地URL
   *      → 1000000 + Set-Cookie: seller_temp
   */
  async _switchByHttp(region, r, targetUrl, code) {
    const src = this.sessions.glo;
    if (!src) throw new Error('未初始化');

    const ac = await this._antiContent();

    // ---- 步骤 2：落地 ----
    const url = this._landingUrl(r.host, targetUrl, code);
    const u = new URL(url);
    const lr = await raw(r.host, 'GET', u.pathname + u.search, {
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      'Accept-Language': 'zh-CN,zh;q=0.9',
      'Referer': `https://${r.host}/`,
      'Upgrade-Insecure-Requests': '1',
      'sec-fetch-mode': 'navigate',
      'sec-fetch-site': 'none',
      'sec-fetch-dest': 'document',
    }, null, 20000);

    const jar = new CookieJar(src.jar.dump());
    jar.eat(lr.setCookie);

    // ---- 步骤 3：loginBySellerCode ----
    const lb = await raw(r.host, 'POST', '/bg/swift/api/auth/loginBySellerCode', {
      'Anti-Content': ac,
      'Origin': `https://${r.host}`,
      'Referer': url,                 // ★ 落地 URL，不是域名
      'mallid': String(this.mallId || 'undefined'),
      'Accept': 'application/json, text/plain, */*',
      'Accept-Language': 'zh-CN,zh;q=0.9',
      'Cookie': jar.str(),
    }, { code, codeFromDr: 1 }, 20000);   // ★ codeFromDr 是数字 1，必填

    jar.eat(lb.setCookie);

    const j = parse(lb.text);
    const ok = String(codeOf(j)) === '1000000';
    const gotTemp = lb.setCookie.some(c => String(c).startsWith('seller_temp'));

    if (!ok || !gotTemp) {
      const msg = j.errorMsg || j.error_msg || ('code=' + codeOf(j));
      if (this.opts.throwOnFail) throw new Error('loginBySellerCode 失败: ' + msg);
    }

    jar.set('mallid', String(this.mallId));
    this.sessions[region] = new RegionSession(region, r.host, jar, {
      mallId: this.mallId, via: 'http',
    });
    return this.sessions[region];
  }

}

module.exports = { RegionSwitcher, RegionSession, CookieJar, REGIONS };

// ==================== CLI ====================
if (require.main === module) {
  (async () => {
    const cmd = process.argv[2] || 'regions';
    const rs = new RegionSwitcher();

    if (cmd === 'regions') {
      console.log('=== 可选区域 ===');
      for (const r of RegionSwitcher.regions()) {
        console.log(`  ${r.region.padEnd(4)} ${r.label.padEnd(6)} ${r.host.padEnd(28)} code=${r.code} envDr=${r.envDr}`);
      }
      console.log('\n用法: node region_switch.js <regions|status|switch> [区域]');
      return;
    }

    if (!(await rs.restoreAndVerify())) await rs.init();
    console.log('源域:', rs.current().host, '| mall:', rs.mallId);

    if (cmd === 'status') {
      for (const r of rs.listRegions()) {
        const s = rs.sessions[r.region];
        let alive = '-';
        if (s) alive = (await s.alive().catch(() => false)) ? '有效' : '失效';
        console.log(`  ${r.region.padEnd(4)} ${r.host.padEnd(28)} 会话=${s ? alive : '无'}`);
      }
      return;
    }

    if (cmd === 'switch') {
      const region = process.argv[3] || 'us';
      const t0 = Date.now();
      const s = await rs.switchTo(region);
      console.log(`  切换 → ${s.host} (${Date.now() - t0}ms)`);
      console.log('  域 DR :', await s.envDr());
      const u = await s.userInfo();
      console.log('  userInfo: code=' + u.code + ' ok=' + u.ok +
        ' accountId=' + ((u.body.result || {}).accountId || ''));
      return;
    }
  })().catch(e => { console.error('ERR:', e.message); process.exit(1); });
}
