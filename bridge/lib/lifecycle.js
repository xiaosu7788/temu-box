/**
 * 上新生命周期管理
 * =================
 * 菜单：商品管理 → 上新生命周期管理
 * 路由：/newon/product-select
 * 微应用：temu-sca-newon（/newon-entry）
 *
 * 服务：robin（/api/kiana/mms/robin/*）
 */
const path = require('path');

/** 生命周期状态 */
const SELECT_STATUS = {
  9: { name: '待选品', desc: '已完成价格校验，等待选品' },
  10: { name: '待处理', desc: '已完成价格校验' },
  12: { name: '已加入站点', desc: '已发布到站点，可下架' },
  13: { name: '待价格校验', desc: '尚未完成价格校验' },
};

/** 快捷筛选（querySupplierQuickFilterCount 的 type）*/
const QUICK_FILTER = {
  0: '全部',
  1: '待选品',
  2: '待寄样',
  3: '待质检',
  4: '待上架',
  6: '待核价',
  7: '待审核',
  11: '已终止',
  16: '待确认信息',
  19: '其他',
  20: '待处理',
  21: '待优化',
  22: '进行中',
};

/** 时间轴字段（skcList[].statusTime）*/
const STATUS_TIME_FIELDS = [
  'createdTime',              // 创建
  'selectedTime',             // 选中
  'priceVerificationTime',    // 价格校验完成
  'samplePostTime',           // 寄样
  'samplePostingFinishedTime',// 寄样完成
  'qcCompletedTime',          // 质检完成
  'addedToSiteTime',          // 加入站点
  'unPublishedTime',          // 下架
  'firstPurchaseTime',        // 首单
  'terminatedTime',           // 终止
];

const R = '/api/kiana/mms/robin';

class Lifecycle {
  constructor(glo) {
    this.glo = glo;
  }

  _call(p, body) {
    return this.glo.api('POST', p, body);
  }

  /**
   * 商品列表（上新生命周期管理）
   *
   * @param {object} opts
   *   pageNum   页码（不是 pageNumber！）默认 1
   *   pageSize  每页条数，默认 20
   *   ...       其它筛选参数原样透传
   * @returns { code, total, items, aggregation, raw }
   */
  async list(opts = {}) {
    const body = { pageNum: opts.pageNum || opts.page || 1, pageSize: opts.pageSize || 20 };
    for (const [k, v] of Object.entries(opts)) {
      if (['page', 'pageNum', 'pageSize'].includes(k)) continue;
      if (v === undefined || v === null) continue;
      body[k] = v;
    }
    const r = await this._call(R + '/searchForSemiSupplier', body);
    const res = (r.body && r.body.result) || {};
    return {
      code: r.code,
      ok: String(r.code) === '1000000',
      total: res.total,
      items: res.dataList || [],
      productIdNoStatusTotal: res.productIdNoStatusTotal,
      aggregation: (res.productSkcStatusAggregation || []).map(a => ({
        selectStatus: a.selectStatus,
        statusName: (SELECT_STATUS[a.selectStatus] || {}).name || '未知',
        count: a.count,
      })),
      raw: r.body,
    };
  }

  /** 全链托管（另一种供应商类型），参数同 list */
  async listChain(opts = {}) {
    const body = { pageNum: opts.pageNum || 1, pageSize: opts.pageSize || 20 };
    for (const [k, v] of Object.entries(opts)) {
      if (['page', 'pageNum', 'pageSize'].includes(k)) continue;
      if (v === undefined || v === null) continue;
      body[k] = v;
    }
    const r = await this._call(R + '/searchForChainSupplier', body);
    const res = (r.body && r.body.result) || {};
    return {
      code: r.code,
      ok: String(r.code) === '1000000',
      total: res.total,
      items: res.dataList || [],
      aggregation: res.productSkcStatusAggregation || [],
      msg: (r.body && r.body.errorMsg) || ('' + String(r.code) === '2000000' ? '该账号不是全托管，无法查询全链供应商列表' : ''),
      raw: r.body,
    };
  }

  /** 全部商品（自动翻页）*/
  async all(opts = {}) {
    const pageSize = Math.min(opts.pageSize || 100, 100);
    const out = [];
    let total = null;
    for (let p = 1; p <= 20; p++) {
      const r = await this.list(Object.assign({}, opts, { pageNum: p, pageSize }));
      if (!r.ok) break;
      if (total === null) total = r.total;
      out.push(...r.items);
      if (r.items.length < pageSize) break;
      if (total && out.length >= total) break;
    }
    return { total, count: out.length, items: out };
  }

  /** 快捷筛选计数（顶部 tab 的数字）*/
  async quickFilterCount(extra = {}) {
    const r = await this._call(R + '/querySupplierQuickFilterCount', extra);
    const res = (r.body && r.body.result) || {};
    return {
      ok: String(r.code) === '1000000',
      list: (res.countList || []).map(x => ({
        type: x.type,
        name: QUICK_FILTER[x.type] || '未知',
        count: x.count,
      })),
      raw: r.body,
    };
  }

  /** 待办计数 */
  async todoCount(extra = {}) {
    const r = await this._call(R + '/querySupplierTodoCount', extra);
    const res = (r.body && r.body.result) || {};
    return {
      ok: String(r.code) === '1000000',
      total: res.total,
      list: res.todoStatusAggregationList || [],
      raw: r.body,
    };
  }

  /** 全站点基础信息（美国站 100、加拿大 101 …）*/
  async sites() {
    const r = await this._call(R + '/common/queryAllSiteBasicInfo', {});
    const res = (r.body && r.body.result) || {};
    return { ok: String(r.code) === '1000000', list: res.siteBasicList || [], raw: r.body };
  }

  /**
   * 商品审核驳回原因
   * ⚠️ 参数是 goodsId（不是 productId）
   */
  async auditRejectInfo(goodsId) {
    const body = typeof goodsId === 'object' ? goodsId : { goodsId };
    const r = await this._call(R + '/queryGoodsAuditRejectInfo', body);
    const res = (r.body && r.body.result) || {};
    return {
      ok: String(r.code) === '1000000',
      list: res.goodsAuditRejectInfoList || [],
      raw: r.body,
    };
  }

  /**
   * 违规信息
   * ⚠️ 参数是 goodsId
   *
   * 实测返回：
   *   violationInfoVO: [{ violationReasonForMerchantSide, goodsViolationInfoVO }]
   */
  async violationInfo(goodsId) {
    const body = typeof goodsId === 'object' ? goodsId : { goodsId };
    const r = await this._call(R + '/queryViolationInfo', body);
    const res = (r.body && r.body.result) || {};
    return {
      ok: String(r.code) === '1000000',
      list: res.violationInfoVO || [],
      raw: r.body,
    };
  }

  /**
   * 处罚信息
   * ⚠️ 参数是 productId
   */
  async punishInfo(productId) {
    const body = typeof productId === 'object' ? productId : { productId };
    const r = await this._call(R + '/queryPunishInfo', body);
    const res = (r.body && r.body.result) || {};
    return {
      ok: String(r.code) === '1000000',
      total: res.total,
      list: res.punishInfoList || [],
      raw: r.body,
    };
  }

  /**
   * SKU 下架原因 / 加入站点失败原因
   *
   * ★ 已打通。请求结构（从 chunk 6b882012 的调用点还原）：
   *   {
   *     goodsIdSkuIdPairList: [{ goodsId: <货号>, skuIdList: [<productSkuId>, ...] }],
   *     reasonTypeList: [1]   // 1=下架原因  2=加入站点失败原因
   *   }
   *
   * 返回 null 表示该店没有平台记录的原因（手动下架的商品通常为空）。
   *
   * @param {object|Array} input
   *   传 { goodsId, skuIds } 或 [{ goodsId, skuIds }] 或原始 body
   * @param {number|number[]} reasonTypes 默认 [1, 2]
   */
  async offSaleReasons(input, reasonTypes = [1, 2]) {
    let body;
    if (input && input.goodsIdSkuIdPairList) {
      body = input;                                    // 已是原始结构
    } else {
      const arr = Array.isArray(input) ? input : [input];
      body = {
        goodsIdSkuIdPairList: arr.map(x => ({
          goodsId: Number(x.goodsId),
          skuIdList: (x.skuIds || x.skuIdList || []).map(Number),
        })),
        reasonTypeList: Array.isArray(reasonTypes) ? reasonTypes : [reasonTypes],
      };
    }

    const r = await this._call(R + '/queryGoodsSkuOffSaleReason', body);
    const res = (r.body && r.body.result) || {};
    return {
      ok: String(r.code) === '1000000',
      code: r.code,
      msg: (r.body && r.body.errorMsg) || '',
      offSaleReasonList: res.goodsSkuOffSaleReasonList || [],
      addSiteFailureReasonList: res.addSiteFailureReasonList || [],
      commonReasonList: res.commonReasonList || [],
      raw: r.body,
    };
  }

  /**
   * 便捷：按 productId 查下架原因（自动取 SKU）
   * @param {number} productId
   * @param {object} opts { reasonType: 1|2 }
   */
  async offSaleReasonsByProduct(productId, opts = {}) {
    const item = await this.findByProductId(productId);
    if (!item) return { ok: false, err: '未找到该商品' };

    // 从 goods 接口取 SKU
    let skuIds = [];
    for (let page = 1; page <= 6; page++) {
      const g = await this.glo.api('POST', '/visage-agent-seller/product/skc/pageQuery',
        { page, pageSize: 100 });
      const hit = ((g.body && g.body.result && g.body.result.pageItems) || [])
        .find(x => String(x.productId) === String(productId));
      if (hit) {
        skuIds = (hit.productSkuSummaries || []).map(s => s.productSkuId);
        break;
      }
    }
    if (!skuIds.length) return { ok: false, err: '该商品没有 SKU' };

    return this.offSaleReasons(
      { goodsId: item.goodsId, skuIds },
      opts.reasonType ? [opts.reasonType] : [1, 2],
    );
  }

  /** 按 productId 找（遍历）*/
  async findByProductId(productId) {
    const { items } = await this.all({ pageSize: 100 });
    return items.find(x => String(x.productId) === String(productId)) || null;
  }

  /** 按 skcId 找（遍历）*/
  async findBySkcId(skcId) {
    const { items } = await this.all({ pageSize: 100 });
    for (const p of items) {
      const hit = (p.skcList || []).find(s => String(s.skcId) === String(skcId));
      if (hit) return { product: p, skc: hit, statusName: (SELECT_STATUS[hit.selectStatus] || {}).name };
    }
    return null;
  }

  /** 把商品的 skc 列表整理成易读形式 */
  static flatten(item) {
    const info = item.goodsInfoStatus || {};
    return (item.skcList || []).map(s => ({
      skcId: s.skcId,
      selectStatus: s.selectStatus,
      statusName: (SELECT_STATUS[s.selectStatus] || {}).name || '未知',
      colorName: s.colorName,
      canOnSale: s.canOnSale,
      canOffSale: s.canOffSale,
      previewImgUrlList: s.previewImgUrlList || [],
      statusTime: s.statusTime || {},
      // 已完成的阶段
      stages: STATUS_TIME_FIELDS
        .filter(k => s.statusTime && s.statusTime[k])
        .map(k => ({ key: k, at: s.statusTime[k], date: new Date(s.statusTime[k]).toISOString() })),
    }));
  }
}

module.exports = { Lifecycle, SELECT_STATUS, QUICK_FILTER, STATUS_TIME_FIELDS };
