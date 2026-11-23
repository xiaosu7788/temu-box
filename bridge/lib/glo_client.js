/**
 * GLO 卖家中心客户端
 * ===================
 * KJ 登录态 → obtainCode → 落地页 → loginByCode → 调 visage 接口
 *
 * 用法:
 *   const { GloClient } = require('./glo_client');
 *   const glo = new GloClient();
 *   await glo.login({ account, password });      // 内部走 KJ 换码
 *   const list = await glo.pageQuery({ bindSiteIds: [100] });  // 美国站
 */
const fs = require('fs');
const path = require('path');
const https = require('https');

const ANTI_PROJ = process.env.TEMU_OPS_ANTI || path.join(__dirname, '..', 'runtime', 'anti_content');
const KJ_HOST = 'seller.kuajingmaihuo.com';
const GLO_HOST = 'agentseller.temu.com';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36 Edg/152.0.0.0';
const DATA_DIR = process.env.TEMU_OPS_DATA || path.join(__dirname, '..', 'runtime');
if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });

const agent = new https.Agent({ keepAlive: true, maxSockets: 8, keepAliveMsecs: 30000, timeout: 30000 });

/** 底层请求 */
function raw(host, method, pathname, headers, body, timeoutMs) {
  return new Promise(resolve => {
    const t0 = Date.now();
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
    const r = https.request({ hostname: host, port: 443, path: pathname, method, headers: h, agent, timeout: timeoutMs || 25000 }, res => {
      const cs = [];
      res.on('data', c => cs.push(c));
      res.on('end', () => resolve({
        status: res.statusCode,
        setCookie: res.headers['set-cookie'] || [],
        text: Buffer.concat(cs).toString('utf8'),
        ms: Date.now() - t0,
      }));
    });
    r.on('error', e => resolve({ status: 0, setCookie: [], text: '', err: e.message, ms: Date.now() - t0 }));
    r.on('timeout', () => { r.destroy(); resolve({ status: 0, setCookie: [], text: '', err: 'timeout', ms: Date.now() - t0 }); });
    if (data !== null) r.write(data);
    r.end();
  });
}

class Jar {
  constructor(init) { this.map = Object.assign({}, init || {}); }
  str() { return Object.entries(this.map).map(([k, v]) => `${k}=${v}`).join('; '); }
  set(k, v) { this.map[k] = v; }
  eat(sc) {
    for (const raw2 of (sc || [])) {
      const p = String(raw2).split(';')[0];
      const i = p.indexOf('=');
      if (i > 0) this.map[p.slice(0, i).trim()] = p.slice(i + 1).trim();
    }
  }
  dump() { return { ...this.map }; }
}

const codeOf = j => (!j || typeof j !== 'object') ? undefined : (j.errorCode !== undefined ? j.errorCode : j.error_code);
const parse = t => { try { return JSON.parse(t); } catch (e) { return { raw: String(t).slice(0, 400) }; } };

class GloClient {
  constructor(opts = {}) {
    // bridge 补丁：按账号读会话文件（原实现取「最新 session」，多账号必串号）
    this.account = String(opts.account || '').trim();
    const kj = opts.kjCookies || GloClient.loadKjCookies(this.account);
    this.kjJar = new Jar(kj);
    // 可直接喂 cookie（浏览器 F12 复制的一整串），跳过登录流程：
    //   new GloClient({ gloCookies: GloClient.parseCookies(str), mallId })
    //   或环境变量 TEMU_GLO_COOKIE + TEMU_MALL_ID
    const rawCookie = opts.gloCookies || GloClient.parseCookies(process.env.TEMU_GLO_COOKIE || '');
    this.gloJar = new Jar(rawCookie);
    this.mallId = opts.mallId || Number(process.env.TEMU_MALL_ID || 0) || null;
    this.uniqueId = opts.uniqueId || null;
    this.accountId = opts.accountId || null;
    // bridge 补丁：按账号分文件，否则多账号会互相覆盖 GLO 会话
    if (this.account) {
      const h = require('crypto').createHash('sha1')
        .update(this.account.toLowerCase()).digest('hex').slice(0, 16);
      this.sessionFile = path.join(DATA_DIR, 'glo_session_' + h + '.json');
    } else {
      this.sessionFile = path.join(DATA_DIR, 'glo_session.json');
    }
  }

  /** "a=1; b=2" → { a:'1', b:'2' } */
  static parseCookies(str) {
    const out = {};
    for (const seg of String(str || '').split(';')) {
      const i = seg.indexOf('=');
      if (i > 0) out[seg.slice(0, i).trim()] = seg.slice(i + 1).trim();
    }
    return Object.keys(out).length ? out : null;
  }

  // ==================== 1. KJ 侧拿 cookie ====================
  /**
   * 从 KJ 服务的会话文件读取 cookie
   * @param {string} [account] 指定账号时按哈希精确取，避免多账号互相串号
   */
  static loadKjCookies(account) {
    const dir = path.join(__dirname, '..', 'runtime');
    if (account) {
      // 与 kj_local.sessionFile 同一套哈希：sha1(account).slice(0,16)
      const h = require('crypto').createHash('sha1')
        .update(String(account).trim().toLowerCase()).digest('hex').slice(0, 16);
      const exact = path.join(dir, 'session_' + h + '.json');
      try {
        if (fs.existsSync(exact)) {
          const s = JSON.parse(fs.readFileSync(exact, 'utf8'));
          if (s && s.cookies) return s.cookies;
        }
      } catch (e) { /* 读坏了就走下面的兜底 */ }
    }
    const files = fs.readdirSync(dir).filter(f => f.startsWith('session_') && f.endsWith('.json'));
    if (!files.length) return null;
    let best = null, bestAt = 0;
    for (const f of files) {
      try {
        const s = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
        if (s.at > bestAt) { bestAt = s.at; best = s; }
      } catch (e) { }
    }
    return best ? best.cookies : null;
  }

  /** KJ userInfo 拿 mallId / uniqueId */
  async fetchMallInfo() {
    const hdrs = { Origin: `https://${KJ_HOST}`, Referer: `https://${KJ_HOST}/` };
    hdrs.Cookie = this.kjJar.str();
    const r = await raw(KJ_HOST, 'POST', '/bg/quiet/api/mms/userInfo', hdrs, {}, 20000);
    const j = parse(r.text);
    const co = ((j.result || {}).companyList || [])[0] || {};
    const m = (co.malInfoList || [])[0] || {};
    this.mallId = m.mallId;
    this.uniqueId = m.uniqueId;
    this.mallName = m.mallName;
    this.isSemiManaged = m.isSemiManagedMall;
    this.accountId = (j.result || {}).userId;
    return m;
  }

  // ==================== 2. 换码 ====================
  async obtainCode() {
    const acMod = require(path.join(ANTI_PROJ, 'anti_content.js'));
    const gen = await acMod.createAntiContent({ serverTime: Date.now() });
    // 用 KJ 的 _nano_fp 注入，保证 token 与请求 cookie 同源
    try { gen.getCookies(); } catch (e) { }
    const ac = gen.generate();

    const ocHdrs = {
      Origin: `https://${KJ_HOST}`,
      Referer: `https://${KJ_HOST}/`,
      'anti-content': ac,
    };
    ocHdrs.Cookie = this.kjJar.str();
    const r = await raw(KJ_HOST, 'POST', '/bg/quiet/api/auth/obtainCode', ocHdrs,
      { redirectUrl: `https://${GLO_HOST}/auth/authentication` }, 20000);
    const j = parse(r.text);
    const code = (j.result || {}).code;
    if (!code) throw new Error('obtainCode 失败: HTTP' + r.status + ' ' + r.text.slice(0, 200));
    return code;
  }

  async landPage(code) {
    const q = new URLSearchParams({
      redirectUrl: `https://${GLO_HOST}/`,
      uniqueId: this.uniqueId,
      asCode: code,
      sessionType: '0',
      mallId: String(this.mallId),
    });
    const r = await raw(GLO_HOST, 'GET', '/auth/authentication?' + q.toString(), {
      Accept: 'text/html,application/xhtml+xml,*/*;q=0.8',
      Referer: `https://${GLO_HOST}/`,
      'sec-fetch-mode': 'navigate',
      'sec-fetch-site': 'none',
      'sec-fetch-dest': 'document',
      'Upgrade-Insecure-Requests': '1',
    }, null, 20000);
    this.gloJar.eat(r.setCookie);
    return r;
  }

  async loginByCode(code) {
    const lbHdrs = {
      Origin: `https://${GLO_HOST}`,
      Referer: `https://${GLO_HOST}/`,
      'mallid': String(this.mallId),
    };
    lbHdrs.Cookie = this.gloJar.str();
    const r = await raw(GLO_HOST, 'POST', '/api/seller/auth/loginByCode', lbHdrs,
      { code, confirm: false, targetMallId: this.mallId, targetUniqueId: this.uniqueId }, 20000);
    this.gloJar.eat(r.setCookie);
    const j = parse(r.text);
    return { status: r.status, code: codeOf(j), body: j, jar: this.gloJar.dump() };
  }

  /** 完整换码流程 */
  async login(opts = {}) {
    if (!this.kjJar.str() && opts.account && opts.password) {
      // 通过 KJ 服务登录拿 cookie
      const kj = require('./kj_local');
      const s = await kj.login(opts.account, opts.password);
      // bridge 补丁：刚登录的就是 opts.account，按它精确读，不能取「最新」
      const ck = GloClient.loadKjCookies(opts.account || this.account);
      if (ck) this.kjJar = new Jar(ck);
    }
    if (!this.kjJar.str()) {
      // bridge 补丁：必须带 account，无参会取「最新」的 session 文件导致串号
      const ck = GloClient.loadKjCookies(this.account);
      if (ck) this.kjJar = new Jar(ck);
    }
    if (!this.kjJar.str()) throw new Error('无 KJ 会话 cookie');

    await this.fetchMallInfo();
    const code = await this.obtainCode();
    await this.landPage(code);
    const r = await this.loginByCode(code);

    const out = {
      at: Date.now(),
      kjCookies: this.kjJar.dump(),
      gloCookies: this.gloJar.dump(),
      mallId: this.mallId,
      uniqueId: this.uniqueId,
      mallName: this.mallName,
      accountId: this.accountId,
      loginByCode: { status: r.status, code: r.code },
    };
    fs.writeFileSync(this.sessionFile, JSON.stringify(out, null, 2));
    return out;
  }

  // ==================== 3. 业务接口 ====================
  async api(method, pathname, body, opts = {}) {
    const apiHdrs = {
      Origin: `https://${GLO_HOST}`,
      Referer: opts.referer || `https://${GLO_HOST}/goods/list`,
      'mallid': String(this.mallId),
    };
    // 写接口（如 sizespec/createSizeSpec）会校验风控头，缺了直接 40001 Invalid Login State
    if (opts.antiContent) apiHdrs['anti-content'] = opts.antiContent;
    apiHdrs.Cookie = this.gloJar.str();
    const r = await raw(GLO_HOST, method, pathname, apiHdrs, body, 30000);
    this.gloJar.eat(r.setCookie);
    const j = parse(r.text);
    return { status: r.status, ms: r.ms, code: codeOf(j), body: j, raw: r.text };
  }

  /** 商品列表（行粒度 = SKC）*/
  async pageQuery(opts = {}) {
    const body = Object.assign({ page: 1, pageSize: 20 }, opts);
    return this.api('POST', '/visage-agent-seller/product/skc/pageQuery', body);
  }

  /**
   * 商品详情
   * ⚠️ 服务端**没有独立的详情接口** —— pageQuery 单条就是详情级数据（82 字段）。
   *    本方法按 productId 精确筛选后返回单条。
   *    如服务端支持 productIds 精确查询则走精确路径，否则分页扫描。
   */
  async productDetail(productId, opts = {}) {
    // 先试精确筛选
    const tryBody = Object.assign({ page: 1, pageSize: 20, productIds: [productId] }, opts.filter || {});
    const r = await this.pageQuery(tryBody);
    if (String(r.code) === '1000000') {
      const items = (r.body.result || {}).pageItems || [];
      const hit = items.find(x => String(x.productId) === String(productId));
      if (hit) return { found: true, exact: true, item: hit, total: (r.body.result || {}).total };
      // 服务端忽略 productIds → 需要扫描
      if (items.length && String(items[0].productId) !== String(productId)) {
        return await this.scanForProduct(productId, opts);
      }
    }
    return await this.scanForProduct(productId, opts);
  }

  /** 分页扫描找指定 productId */
  async scanForProduct(productId, opts = {}) {
    const pageSize = opts.pageSize || 500;
    const maxPages = opts.maxPages || 20;
    const filter = opts.filter || {};
    for (let page = 1; page <= maxPages; page++) {
      const r = await this.pageQuery(Object.assign({ page, pageSize }, filter));
      if (String(r.code) !== '1000000') return { found: false, error: r.code, raw: r.raw };
      const res = r.body.result || {};
      const items = res.pageItems || [];
      const hit = items.find(x => String(x.productId) === String(productId));
      if (hit) return { found: true, exact: false, page, item: hit, total: res.total };
      if (items.length < pageSize) break;
      if (page * pageSize >= (res.total || 0)) break;
    }
    return { found: false, error: 'not_found' };
  }

  /** 按 productSkcId 取单条 */
  async skcDetail(productSkcId, opts = {}) {
    const pageSize = opts.pageSize || 500;
    const filter = opts.filter || {};
    for (let page = 1; page <= (opts.maxPages || 20); page++) {
      const r = await this.pageQuery(Object.assign({ page, pageSize }, filter));
      if (String(r.code) !== '1000000') return { found: false, error: r.code };
      const res = r.body.result || {};
      const items = res.pageItems || [];
      const hit = items.find(x => String(x.productSkcId) === String(productSkcId));
      if (hit) return { found: true, page, item: hit };
      if (items.length < pageSize) break;
      if (page * pageSize >= (res.total || 0)) break;
    }
    return { found: false, error: 'not_found' };
  }

  // ---------- 详情补充接口（实测可用） ----------

  /**
   * ⭐ 商品完整详情（含全部图片）
   *
   * POST /visage-agent-seller/product/queryForImage  {productId}
   * → 52 个顶层字段 / 约 16.7KB，是"图片编辑页"加载的数据源
   *
   * 包含 pageQuery 里没有的图片：
   *   carouselImageUrls                  轮播图（主图组）
   *   carouselImageI18nVOList            多语言轮播图
   *   carouseVideoVOList / detailVideoVOList   视频
   *   materialImgUrl                     素材图
   *   outerPackageImages                 外包装图
   *   productImageProcessExtVOList       图片处理扩展（imgTag/isOcr）
   *   productSkcList[].previewImgUrls    SKC 预览图
   *   productSkcList[].colorImageUrl     颜色图
   *   productSkcList[].productSkcImageExtList
   *   productSkcList[].productSkuList[].thumbUrl   SKU 图
   *   goodsLayerDecorationVOList[].contentList[].imgUrl  装修图
   *   以及 productI18nList（多语言标题）、productPropertyList 等
   */
  async productDetailFull(productId) {
    return this.api('POST', '/visage-agent-seller/product/queryForImage', { productId });
  }

  /**
   * 只取图片信息（含主图）
   * ⚠️ queryForImage 里没有 mainImageUrl 字段 —— 主图 = carouselImageUrls[0]
   */
  async productImages(productId) {
    const r = await this.productDetailFull(productId);
    if (String(r.code) !== '1000000') return { ok: false, code: r.code, raw: r.raw };
    const d = r.body.result || {};
    const carousel = d.carouselImageUrls || [];
    const skc0 = (d.productSkcList || [])[0] || {};
    const sku0 = (skc0.productSkuList || [])[0] || {};
    return {
      ok: true,
      productId: d.productId,
      /** ⭐ 主图 */
      mainImage: carousel[0] || skc0.colorImageUrl || null,
      /** 完整图片列表（[0] 是主图）*/
      carouselImageUrls: carousel,
      carouselI18n: d.carouselImageI18nVOList || [],
      carouseVideos: d.carouseVideoVOList || [],
      detailVideos: d.detailVideoVOList || [],
      materialImage: d.materialImgUrl || null,
      outerPackageImages: d.outerPackageImages || [],
      guideFilePageImages: d.guideFilePageImages || [],
      imageProcessList: d.productImageProcessExtVOList || [],
      skc: (d.productSkcList || []).map(s => ({
        productSkcId: s.productSkcId,
        previewImgUrls: s.previewImgUrls || [],
        colorImageUrl: s.colorImageUrl || null,
        imageExtList: s.productSkcImageExtList || [],
        skus: (s.productSkuList || []).map(k => ({
          productSkuId: k.productSkuId,
          thumbUrl: k.thumbUrl || null,
        })),
      })),
      decorationImages: (d.goodsLayerDecorationVOList || [])
        .flatMap(x => (x.contentList || []).map(c => c.imgUrl))
        .filter(Boolean),
    };
  }

  /** 图片上传校验 */
  async imageUploadCheck(body) {
    return this.api('POST', '/visage-agent-seller/product/image/imageUploadCheck', body);
  }

  /** 商品素材标签 */
  async productMaterialTag(productId) {
    return this.api('POST', '/visage-agent-seller/product/image/queryProductMaterialTag', { productId });
  }

  /** 在途审核草稿 */
  async inProcessAuditDraft(productId) {
    return this.api('POST', '/visage-agent-seller/product/queryProductInProcessAuditDraft', { productId });
  }

  /** 销量趋势（需 productSkcId）*/
  async salesTrends(productSkcId) {
    return this.api('POST', '/visage-agent-seller/product/skc/sales/trends/query', { productSkcId });
  }

  /** 短链查询（注意：缺失时会按需生成，有副作用）*/
  async shortLinks(productIds) {
    return this.api('POST', '/visage-agent-seller/product/shortLinks/query', { productIds });
  }

  /** 尺寸表编辑次数限制 */
  async sizeTemplateLimit(productId) {
    return this.api('POST', '/visage-agent-seller/product/size/template/editTimes/limit/check', { productId });
  }

  /** 定制模板 */
  async customizationTemplate(productId, productName, leafCat) {
    return this.api('POST', '/visage-agent-seller/product/customization/template/query', { productId, productName, leafCat });
  }

  /** SKU 分类推荐 */
  async skuClassification(productId) {
    return this.api('POST', '/visage-agent-seller/product/sku/skuClassification/recommend/query', { productId });
  }

  /** 价格穿透估算 */
  async pricePenetration(productSkcIds, siteId) {
    return this.api('POST', '/visage-agent-seller/product/sku/penetration/estimate/supplier/price/batchQuery',
      { productSkcIds, siteId });
  }
}

module.exports = { GloClient, Jar, raw, codeOf, parse };
