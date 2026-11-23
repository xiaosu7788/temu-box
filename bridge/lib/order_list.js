/**
 * 订单列表模块
 * =============
 * 逆向自 https://agentseller.temu.com/mmsos/orders.html
 *
 * ⚠️ 页面上有两个「区域」概念，别搞混：
 *
 *   A. 区域切换器（顶部 全球 / 美国 / 欧区）
 *      · 来源：菜单接口里「订单列表」的 dr 数组 = ['udp','eu','us']
 *      · 本质：跨域换码跳转（agentseller / -us / -eu 三个域名）
 *      · 订单查询请求体里【没有】dr 字段 —— 区域由当前域名决定
 *      · 切域名见 region_switch.js
 *
 *   B. 市场国家筛选（搜索表单里的国家下拉）
 *      · 来源：orderListPageConfig.regionList
 *      · 参数：regionId（16 个国家，不含美国/欧洲）
 *      · 本模块的 regions() / query({regionId}) 就是这一层
 *
 * 核心接口：
 *   · 订单查询   POST /kirogi/bg/mms/recentOrderList
 *   · 页面配置   POST /kirogi/bg/mms/orderListPageConfig  → regionList
 *
 * 用法：
 *   const { OrderList } = require('./order_list');
 *   const ol = new OrderList();
 *   await ol.init();
 *
 *   await ol.regions();                 // 16 个市场国家 [{regionId, regionName}]
 *   await ol.query({ regionId: 37 });   // 加拿大
 *   await ol.menuDr();                  // 区域切换器选项（全球/美国/欧区）
 *   await ol.query({ pageSize: 100 });
 */
const path = require('path');
const fs = require('fs');

const DATA_DIR = process.env.TEMU_OPS_DATA || path.join(__dirname, '..', 'runtime');

/** 订单列表默认请求体（实测抓包）*/
const DEFAULT_BODY = {
  fulfillmentMode: 0,      // 0=半托管
  pageNumber: 1,           // ⚠️ 是 pageNumber，不是 page
  pageSize: 20,
  queryType: 2,
  sortType: 1,             // 1=最新成交订单在上
  timeZone: 'UTC+8',
  parentAfterSalesTag: 0,
  needBuySignService: 0,
  sellerNoteLabelList: [],
};

class OrderList {
  constructor(opts = {}) {
    this.opts = opts;
    this.glo = null;
    this._regions = null;
    this._config = null;
    this._rs = null;          // RegionSwitcher（跨域查询时懒加载）
    this._sessions = {};      // region -> session
  }

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

  // ==================== 跨域查询 ====================
  /**
   * 拿到某区域的会话（glo / us / eu）
   * 首次会拉起 headless 浏览器完成切换，之后复用
   */
  async session(region) {
    if (region === 'glo') {
      if (!this.glo) await this.init();
      return this.glo;
    }
    if (this._sessions[region]) return this._sessions[region];

    if (!this._rs) {
      const { RegionSwitcher } = require('./region_switch');
      this._rs = new RegionSwitcher();
      if (!(await this._rs.restoreAndVerify())) await this._rs.init();
    }
    const sess = await this._rs.switchTo(region);
    this._sessions[region] = sess;
    return sess;
  }

  /**
   * 在某区域查订单
   * @param {'glo'|'us'|'eu'} region
   * @param {object} opts 同 query()
   */
  async queryOnRegion(region, opts = {}) {
    // GLO 用 GloClient（api(method, path, body) 签名不同），走现成的 query()
    if (region === 'glo' || !region) {
      const r = await this.query(opts);
      return { ...r, region: 'glo', host: 'agentseller.temu.com' };
    }

    const sess = await this.session(region);
    const body = Object.assign({}, DEFAULT_BODY);

    if (opts.region) {
      const id = await this.regionId(opts.region, region);
      if (id) body.regionId = id;
    }
    if (opts.regionId !== undefined && opts.regionId !== null) body.regionId = opts.regionId;
    for (const [k, v] of Object.entries(opts)) {
      if (k === 'region') continue;
      if (v === undefined || v === null) continue;
      body[k] = v;
    }

    const r = await sess.api('/kirogi/bg/mms/recentOrderList', body);
    const code = r.code;
    const ok = String(code) === '0' || String(code) === '1000000';
    const res = r.body.result || {};

    return {
      ok, code, region,
      host: sess.host || 'agentseller.temu.com',
      msg: r.body.errorMsg || r.body.error_msg || '',
      total: res.totalItemNum !== undefined ? res.totalItemNum : null,
      pageItems: res.pageItems || [],
      mallInfoMap: res.mallInfoMap || null,
      requestBody: body,
      raw: r.body,
    };
  }

  /**
   * 三个区域一起查，对比总数
   * @returns {{glo, us, eu}} 各区域结果
   */
  async compareAllRegions(opts = {}) {
    const out = {};
    for (const region of ['glo', 'us', 'eu']) {
      try {
        const r = await this.queryOnRegion(region, opts);
        out[region] = { ok: r.ok, code: r.code, total: r.total, host: r.host, msg: r.msg };
      } catch (e) {
        out[region] = { ok: false, err: e.message };
      }
    }
    return out;
  }

  /**
   * 某区域的市场国家列表
   * 实测：glo=16 个, us=1 个, eu=55 个（各不相同）
   */
  async regionsOn(region) {
    if (region === 'glo' || !region) {
      const cfg = await this.config();
      return {
        regionList: cfg.regionList || [],
        wareHouseList: cfg.wareHouseList || [],
        timeZoneList: cfg.timeZoneList || [],
        code: 0,
      };
    }
    const sess = await this.session(region);
    const r = await sess.api('/kirogi/bg/mms/orderListPageConfig', { queryType: 2, fulfillmentMode: 0 });
    const cfg = r.body.result || {};
    return {
      regionList: cfg.regionList || [],
      wareHouseList: cfg.wareHouseList || [],
      timeZoneList: cfg.timeZoneList || [],
      code: r.code,
    };
  }

  // ==================== 页面配置 ====================
  /**
   * 订单列表页配置（含 regionList / 仓库 / 时区）
   * POST /kirogi/bg/mms/orderListPageConfig
   */
  async config(opts = {}) {
    if (this._config && !opts.force) return this._config;
    const r = await this.glo.api('POST', '/kirogi/bg/mms/orderListPageConfig', {
      queryType: opts.queryType !== undefined ? opts.queryType : 2,
      fulfillmentMode: opts.fulfillmentMode !== undefined ? opts.fulfillmentMode : 0,
    });
    if (String(r.code) !== '0' && String(r.code) !== '1000000') {
      throw new Error('orderListPageConfig 失败: ' + r.code);
    }
    this._config = r.body.result || {};
    return this._config;
  }

  /**
   * 市场国家筛选列表（B 层，16 个国家）
   * ⚠️ 这不是顶部的全球/美国/欧区切换器，那是菜单 dr（见 menuDr()）
   * @returns {Array<{regionId:number, regionName:string}>}
   */
  async regions(force) {
    if (this._regions && !force) return this._regions;
    const cfg = await this.config({ force });
    this._regions = cfg.regionList || [];
    return this._regions;
  }

  /**
   * 区域切换器选项（A 层：全球 / 美国 / 欧区）
   * 从菜单接口读「订单列表」这一项的 dr 数组
   * @returns {{ dr: string[], canSwitch: boolean, options: Array<{dr,label,host}> }}
   */
  async menuDr() {
    const LABEL = { us: '全球', eu: '欧区', udp: '美国' };
    const HOST = {
      us: 'agentseller.temu.com',
      eu: 'agentseller-eu.temu.com',
      udp: 'agentseller-us.temu.com',
    };
    const r = await this.glo.api('POST', '/api/seller/auth/menu', { showGlobalMenu: true });
    if (String(r.code) !== '1000000') return { dr: [], canSwitch: false, options: [], code: r.code };
    const menus = (r.body.result || {}).menuList || [];
    const item = menus.find(m => (m.menuName || m.name) === '订单列表');
    const dr = item ? (item.dr || []) : [];
    return {
      dr,
      canSwitch: dr.length >= 2,
      route: item ? item.menuRoute : null,
      options: dr.map(d => ({ dr: d, label: LABEL[d] || d, host: HOST[d] || null })),
    };
  }

  /** 区域名 → regionId（可指定区域，各区域的国家表不同）*/
  async regionId(name, region) {
    if (typeof name === 'number') return name;
    let list;
    if (region && region !== 'glo') {
      const r = await this.regionsOn(region);
      list = r.regionList;
    } else {
      list = await this.regions();
    }
    const hit = list.find(x => x.regionName === name || x.regionName === name + '站');
    return hit ? hit.regionId : null;
  }

  /** 仓库列表 */
  async warehouses() {
    const cfg = await this.config();
    return cfg.wareHouseList || [];
  }

  /** 时区列表 */
  async timeZones() {
    const cfg = await this.config();
    return cfg.timeZoneList || [];
  }

  // ==================== 订单查询 ====================
  /**
   * 查询订单
   * @param {object} opts
   *   regionId    市场国家 ID（B 层筛选，37=加拿大）
   *   region      同上，支持传国家名
   *   pageNumber  页码（默认 1）
   *   pageSize    每页条数（默认 20）
   *   sortType    1=最新成交在上
   *   timeZone    'UTC+8' | 'UTC-8' | 'UTC-5'
   *   fulfillmentMode 0=半托管
   *
   * ⚠️ A 层区域（全球/美国/欧区）不在参数里，
   *    要切那是换域名（region_switch.js），然后在新域上查
   */
  async query(opts = {}) {
    const body = Object.assign({}, DEFAULT_BODY);

    // 区域：支持传名字
    if (opts.region) {
      const id = await this.regionId(opts.region);
      if (id) body.regionId = id;
    }
    if (opts.regionId !== undefined && opts.regionId !== null) body.regionId = opts.regionId;

    // 其它字段透传（★ 必须过滤 undefined，否则会把默认值覆盖成 undefined，
    //    JSON 序列化时该字段直接消失 → 服务端报 1001）
    for (const [k, v] of Object.entries(opts)) {
      if (k === 'region') continue;
      if (v === undefined || v === null) continue;
      body[k] = v;
    }

    const r = await this.glo.api('POST', '/kirogi/bg/mms/recentOrderList', body);
    const code = r.code;
    const ok = String(code) === '0' || String(code) === '1000000';
    const res = r.body.result || {};

    return {
      ok,
      code,
      msg: r.body.errorMsg || r.body.error_msg || '',
      total: res.totalItemNum !== undefined ? res.totalItemNum : null,
      pageItems: res.pageItems || [],
      mallInfoMap: res.mallInfoMap || null,
      requestBody: body,
      raw: r.body,
    };
  }

  /** 逐页拉全部订单 */
  async all(opts = {}) {
    const pageSize = opts.pageSize || 100;
    let page = 1, all = [], total = null;
    while (page <= (opts.maxPages || 50)) {
      const r = await this.query(Object.assign({}, opts, { pageNumber: page, pageSize }));
      if (!r.ok) return { ok: false, code: r.code, msg: r.msg, items: all };
      total = r.total;
      all = all.concat(r.pageItems);
      if (r.pageItems.length < pageSize) break;
      if (total !== null && all.length >= total) break;
      page++;
    }
    return { ok: true, total, items: all, pages: page };
  }

  // ==================== 关联接口 ====================
  /** 异常订单数 */
  async abnormalSum() {
    const r = await this.glo.api('POST', '/api/bg/cw/relate/order/queryAbnormalOrderSum', {});
    return r.body.result || {};
  }

  /** 订单包裹列表 */
  async shipmentList(body) {
    return await this.glo.api('POST', '/kirogi/bg/mms/orderShipmentList', body || {});
  }

  /** 订单 SKU 变更记录 */
  async skuChangeRecord(body) {
    return await this.glo.api('POST', '/kirogi/bg/mms/queryOrderSkuChangeRecord', body || {});
  }

  /** 大批量订单列表 */
  async largePage(body) {
    return await this.glo.api('POST', '/kirogi/bg/mms/largePageRecentOrderList', body || {});
  }
}

module.exports = { OrderList, DEFAULT_BODY };

// ==================== CLI ====================
if (require.main === module) {
  (async () => {
    const cmd = process.argv[2] || 'regions';
    const ol = new OrderList();
    await ol.init();
    console.log('已登录 | mall:', ol.glo.mallName, ol.glo.mallId, '\n');

    if (cmd === 'regions') {
      const list = await ol.regions();
      console.log(`=== 可切换区域（${list.length} 个）===`);
      for (const r of list) console.log(`  regionId=${String(r.regionId).padStart(4)}  ${r.regionName}`);
      console.log('\n  时区:', (await ol.timeZones()).join(' / '));
      console.log('  仓库:', (await ol.warehouses()).map(w => w.warehouseName).join(', ') || '(无)');
      return;
    }

    if (cmd === 'config') {
      const cfg = await ol.config();
      console.log('配置字段:', Object.keys(cfg).join(', '));
      return;
    }

    if (cmd === 'query') {
      const regionName = process.argv[3];
      const opts = { pageSize: 10 };
      if (regionName) opts.region = regionName;
      const r = await ol.query(opts);
      console.log('请求体:', JSON.stringify(r.requestBody));
      console.log(`结果: code=${r.code} ${r.msg} total=${r.total} 本页=${r.pageItems.length}`);
      if (r.pageItems[0]) {
        console.log('首条字段:', Object.keys(r.pageItems[0]).join(', '));
      }
      return;
    }

    if (cmd === 'compare') {
      console.log('=== 三区域订单对比 ===');
      const out = await ol.compareAllRegions({ pageSize: 10 });
      for (const [k, v] of Object.entries(out)) {
        const label = { glo: '全球', us: '美国', eu: '欧区' }[k];
        if (v.err) console.log(`  ${label.padEnd(5)} 失败: ${v.err}`);
        else console.log(`  ${label.padEnd(5)} ${String(v.host).padEnd(28)} code=${v.code} total=${v.total}`);
      }
      return;
    }

    if (cmd === 'regions-on') {
      const region = process.argv[3] || 'us';
      const r = await ol.regionsOn(region);
      console.log(`=== ${region} 域的市场国家（${r.regionList.length} 个）===`);
      for (const x of r.regionList.slice(0, 20)) console.log(`  ${String(x.regionId).padStart(4)}  ${x.regionName}`);
      if (r.regionList.length > 20) console.log(`  ... 共 ${r.regionList.length} 个`);
      console.log('\n  仓库:', r.wareHouseList.map(w => w.warehouseName).join(', ') || '无');
      console.log('  时区:', r.timeZoneList.join(' / '));
      return;
    }

    if (cmd === 'dr') {
      const d = await ol.menuDr();
      console.log('=== A 层：区域切换器 ===');
      console.log('  dr:', JSON.stringify(d.dr), '| 可切换:', d.canSwitch);
      for (const o of d.options) console.log(`    ${o.label.padEnd(5)} dr=${o.dr.padEnd(4)} → ${o.host}`);
      return;
    }

    if (cmd === 'abnormal') {
      console.log(await ol.abnormalSum());
      return;
    }

    console.log('用法: node order_list.js <regions|config|query [区域名]|abnormal>');
  })().catch(e => { console.error('ERR:', e.message); process.exit(1); });
}
