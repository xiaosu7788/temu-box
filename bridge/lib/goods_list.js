/**
 * 商品列表模块
 * =============
 * 业务层：站点表、菜单 dr、商品列表、商品详情/图片。
 *
 * 区域切换本身不在这里 —— 需要跨域时调 region_switch。
 *
 * 用法：
 *   const { GoodsList } = require('./goods_list');
 *
 *   const gl = new GoodsList();
 *   await gl.init();                       // 登录 GLO
 *
 *   await gl.sites();                      // 站点表（缓存）
 *   await gl.siteId('美国');                // 100
 *   await gl.availableSites();             // 本店已发布的站点
 *
 *   await gl.pageQuery({ siteIds: ['美国'] });
 *   await gl.detail(productId);            // 含全部图片
 *   await gl.canSwitchRegion();            // 该菜单是否支持切区域
 */
const path = require('path');
const fs = require('fs');

const DATA_DIR = process.env.TEMU_OPS_DATA || path.join(__dirname, '..', 'runtime');
const SITE_CACHE = path.join(DATA_DIR, 'site_table.json');

/** 站点名 → siteId 的常用映射（完整的从 site/query 拉）*/
const SITE_ALIAS = {
  '美国': 100, 'US': 100, 'usa': 100,
  '加拿大': 101, 'CA': 101,
  '英国': 102, 'UK': 102,
  '澳大利亚': 103, 'AU': 103,
  '新西兰': 104,
  '德国': 105, 'DE': 105,
  '法国': 106, 'FR': 106,
  '意大利': 107, 'IT': 107,
  '荷兰': 108, 'NL': 108,
  '西班牙': 109, 'ES': 109,
  '墨西哥': 110, 'MX': 110,
  '葡萄牙': 111,
  '波兰': 112,
  '日本': 118, 'JP': 118,
  '韩国': 119, 'KR': 119,
  '新加坡': 121, 'SG': 121,
  '马来西亚': 126, 'MY': 126,
  '菲律宾': 127, 'PH': 127,
  '台湾': 128, 'TW': 128,
  '泰国': 129, 'TH': 129,
  '巴西': 132, 'BR': 132,
};

class GoodsList {
  constructor(opts = {}) {
    this.opts = opts;
    this.glo = null;
    this._sites = null;
    this._menu = null;
  }

  /** 登录 GLO（复用已有的 GloClient）*/
  async init(gloClient) {
    if (gloClient) { this.glo = gloClient; return this; }
    const { GloClient } = require('./glo_client');
    this.glo = new GloClient();
    await this.glo.fetchMallInfo();
    const c = await this.glo.obtainCode();
    await this.glo.landPage(c);
    await this.glo.loginByCode(c);
    return this;
  }

  // ==================== 站点表 ====================
  /**
   * 全站点表（104 个），带文件缓存
   * @param {boolean} force 强制刷新
   */
  async sites(force) {
    if (this._sites && !force) return this._sites;

    if (!force && fs.existsSync(SITE_CACHE)) {
      try {
        const d = JSON.parse(fs.readFileSync(SITE_CACHE, 'utf8'));
        if (Date.now() - d.at < 24 * 3600e3) { this._sites = d.list; return this._sites; }
      } catch (e) { }
    }

    const r = await this.glo.api('POST', '/visage-agent-seller/config/common/site/query', {});
    if (String(r.code) !== '1000000') throw new Error('site/query 失败: ' + r.code);
    const list = ((r.body.result || {}).siteBaseList) || [];

    this._sites = list;
    try {
      if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
      fs.writeFileSync(SITE_CACHE, JSON.stringify({ at: Date.now(), list }, null, 2));
    } catch (e) { }
    return list;
  }

  /** 站点名/别名 → siteId */
  async siteId(name) {
    if (typeof name === 'number') return name;
    const alias = SITE_ALIAS[name];
    if (alias) return alias;
    const list = await this.sites();
    const hit = list.find(s => s.siteName === name || s.siteName === name + '站'
      || String(s.siteId) === String(name));
    return hit ? hit.siteId : null;
  }

  /** 按 dr 分组站点 */
  async sitesByDr() {
    const list = await this.sites();
    const out = {};
    for (const s of list) {
      const dr = s.dr || 'unknown';
      (out[dr] = out[dr] || []).push({ siteId: s.siteId, name: s.siteName });
    }
    return out;
  }

  /**
   * 本店实际发布到的站点（用 pageQuery 逐个探，有 total>0 才算）
   * @param {number[]} [candidates] 要探测的 siteId 列表，默认全部
   */
  async availableSites(candidates) {
    const list = candidates || (await this.sites()).map(s => s.siteId);
    const out = [];
    for (const id of list) {
      try {
        const r = await this.pageQuery({ siteIds: [id], page: 1, pageSize: 1 });
        const total = (r.result || {}).total || 0;
        if (total > 0) out.push({ siteId: id, total });
      } catch (e) { }
    }
    return out;
  }

  // ==================== 菜单 dr ====================
  /** 拉菜单（缓存）*/
  async menu(force) {
    if (this._menu && !force) return this._menu;
    const r = await this.glo.api('POST', '/api/seller/auth/menu', { showGlobalMenu: true });
    if (String(r.code) !== '1000000') throw new Error('menu 失败: ' + r.code);
    this._menu = (r.body.result || {}).menuList || [];
    return this._menu;
  }

  /** 取某菜单的条目 */
  async menuItem(name) {
    const menus = await this.menu();
    return menus.find(m => (m.menuName || m.name) === name) || null;
  }

  /**
   * 「商品列表」这个菜单支持的区域 —— 决定页面是否出现切换器
   * @returns { dr: string[], canSwitch: boolean }
   */
  async canSwitchRegion() {
    const item = await this.menuItem('商品列表');
    if (!item) return { dr: [], canSwitch: false, found: false };
    const dr = item.dr || [];
    return {
      found: true,
      dr,
      canSwitch: dr.length >= 2,
      route: item.menuRoute,
      config: item.customizedConfig,
    };
  }

  /**
   * 列出所有支持区域切换的菜单（业务侧想知道哪些模块能用切换器时用）
   */
  async menusWithRegionSwitch() {
    const menus = await this.menu();
    return menus
      .filter(m => (m.dr || []).length >= 2)
      .map(m => ({ name: m.menuName || m.name, dr: m.dr, route: m.menuRoute }));
  }

  // ==================== 商品列表 ====================
  /**
   * 商品列表（美国区增量参数可读）
   * @param {object} opts { page, pageSize, siteIds:[名字或ID], bindSiteIds:[ID], raw }
   */
  async pageQuery(opts = {}) {
    const body = { page: opts.page || 1, pageSize: opts.pageSize || 20 };

    // 站点筛选：支持传名字
    let ids = opts.bindSiteIds;
    if (!ids && opts.siteIds) {
      ids = [];
      for (const s of opts.siteIds) {
        const id = await this.siteId(s);
        if (id) ids.push(id);
      }
    }
    if (ids && ids.length) body.bindSiteIds = ids;

    // 透传其它参数
    for (const [k, v] of Object.entries(opts)) {
      if (['page', 'pageSize', 'siteIds', 'bindSiteIds'].includes(k)) continue;
      if (v === undefined || v === null) continue;
      body[k] = v;
    }

    const r = await this.glo.api('POST', '/visage-agent-seller/product/skc/pageQuery', body);
    return { code: r.code, ok: String(r.code) === '1000000', result: r.body.result || {}, body: r.body };
  }

  /** 按商品名/ID 找 */
  async find(keyword, opts = {}) {
    const r = await this.pageQuery(Object.assign({ pageSize: 500 }, opts));
    if (!r.ok) return [];
    const items = r.result.pageItems || [];
    const k = String(keyword).toLowerCase();
    return items.filter(x =>
      String(x.productId) === String(keyword) ||
      String(x.productSkcId) === String(keyword) ||
      String(x.productName || '').toLowerCase().includes(k));
  }

  // ==================== 商品详情（含图片）====================
  /**
   * 完整详情（queryForImage）
   * ⚠️ 主图 = carouselImageUrls[0]
   */
  async detail(productId) {
    const r = await this.glo.api('POST', '/visage-agent-seller/product/queryForImage', { productId });
    if (String(r.code) !== '1000000') return { ok: false, code: r.code, raw: r.raw };

    const d = r.body.result || {};
    const carousel = d.carouselImageUrls || [];
    const skc0 = (d.productSkcList || [])[0] || {};

    return {
      ok: true,
      raw: d,
      productId: d.productId,
      productName: d.productName,
      /** ★ 主图 */
      mainImage: carousel[0] || skc0.colorImageUrl || null,
      carouselImageUrls: carousel,
      images: {
        carousel,
        carouselI18n: d.carouselImageI18nVOList || [],
        material: d.materialImgUrl || null,
        outerPackage: d.outerPackageImages || [],
        guideFile: d.guideFilePageImages || [],
        decoration: (d.goodsLayerDecorationVOList || [])
          .flatMap(x => (x.contentList || []).map(c => c.imgUrl)).filter(Boolean),
      },
      videos: {
        carousel: d.carouseVideoVOList || [],
        detail: d.detailVideoVOList || [],
      },
      skcList: (d.productSkcList || []).map(s => ({
        productSkcId: s.productSkcId,
        previewImgUrls: s.previewImgUrls || [],
        colorImageUrl: s.colorImageUrl || null,
        skus: (s.productSkuList || []).map(k => ({
          productSkuId: k.productSkuId,
          thumbUrl: k.thumbUrl || null,
        })),
      })),
      categories: d.categories,
      i18n: d.productI18nList || [],
    };
  }
}

module.exports = { GoodsList, SITE_ALIAS };

// ==================== CLI ====================
if (require.main === module) {
  (async () => {
    const cmd = process.argv[2] || 'info';
    const gl = new GoodsList();
    await gl.init();
    console.log('已登录 | mall:', gl.glo.mallName, gl.glo.mallId, '\n');

    if (cmd === 'sites') {
      const byDr = await gl.sitesByDr();
      for (const [dr, arr] of Object.entries(byDr)) {
        console.log(`[dr=${dr}] ${arr.length} 个站点`);
        for (const s of arr.slice(0, 8)) console.log(`   ${s.siteId}:${s.name}`);
        if (arr.length > 8) console.log(`   ... 共 ${arr.length} 个`);
      }
      return;
    }

    if (cmd === 'available') {
      console.log('探测本店已发布站点…');
      const av = await gl.availableSites([100, 101, 102, 103, 104, 105, 106, 107, 118]);
      for (const a of av) console.log(`  siteId=${a.siteId} total=${a.total}`);
      return;
    }

    if (cmd === 'switchable') {
      const r = await gl.canSwitchRegion();
      console.log('商品列表菜单:', JSON.stringify(r));
      const all = await gl.menusWithRegionSwitch();
      console.log(`\n支持区域切换的菜单: ${all.length} 个`);
      for (const m of all.slice(0, 10)) console.log(`  ${m.name} dr=[${m.dr.join(',')}]`);
      return;
    }

    if (cmd === 'list') {
      const r = await gl.pageQuery({ page: 1, pageSize: 5, siteIds: ['美国'] });
      console.log(`total=${r.result.total}`);
      for (const x of (r.result.pageItems || [])) {
        console.log(`  ${x.productId}  ${String(x.productName).slice(0, 26)}…`);
      }
      return;
    }

    if (cmd === 'detail') {
      const pid = process.argv[3];
      const d = await gl.detail(pid);
      if (!d.ok) { console.log('失败:', d.code); return; }
      console.log('商品:', d.productName);
      console.log('主图:', d.mainImage);
      console.log('轮播:', d.carouselImageUrls.length, '张');
      console.log('SKC :', d.skcList.length, '个');
      return;
    }

    console.log('用法: node goods_list.js <sites|available|switchable|list|detail [productId]>');
  })().catch(e => { console.error('ERR:', e.message); process.exit(1); });
}
