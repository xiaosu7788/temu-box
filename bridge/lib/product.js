/**
 * 商品创建模块
 * =============
 * 类目模板 → 表单 → 请求体 → 草稿 → 创建。
 *
 * 三层结构：
 *   ① 模板层   categoryTemplate(catId) / siteList() / warehouseList() / freightTemplates()
 *   ② 表单层   用 TemplateEngine 驱动（见 template_engine.js）
 *   ③ 提交层   buildBody(form, tmpl, env) 纯函数 + createDraft → saveDraft → create
 *
 * 用法：
 *   const ops = new TemuOps();
 *   await ops.login();
 *
 *   // 1. 拉模板（带 24h 缓存）
 *   const tmpl = await ops.product.categoryTemplate(39276);
 *   const eng  = ops.product.engine(tmpl);
 *
 *   // 2. 渲染表单
 *   for (const field of eng.describe(selected)) { ... }
 *
 *   // 3. 用户选择 → 校验 → 组装属性
 *   selected = eng.onSelect(selected, 6926, 161198);
 *   const missing = eng.validate(selected);
 *   const propReqs = eng.buildPropertyReqs(selected);
 *
 *   // 4. 店铺环境（缓存 1h）
 *   const env = await ops.product.storeEnv(100);
 *
 *   // 5. 建草稿 → 组装 → 创建
 *   const draftId = await ops.product.createDraft(CAT_PATH, 100);
 *   const body = ops.product.buildBody({ ...form, draftId }, tmpl, env, eng);
 *   const r = await ops.product.create(body);
 */
const fs = require('fs');
const path = require('path');

const DATA_DIR = process.env.TEMU_OPS_DATA || path.join(__dirname, '..', 'runtime');
const CACHE_DIR = path.join(DATA_DIR, 'cache');

/** 缓存 TTL */
const TTL = {
  TEMPLATE: 24 * 3600 * 1000,   // 类目模板 24h
  SITE: 24 * 3600 * 1000,       // 站点 24h
  ENV: 3600 * 1000,             // 店铺环境（仓库/运费模板）1h
  REGION: 24 * 3600 * 1000,     // 地区 24h
};

/** 发货时效（秒）*/
const SHIPMENT_LIMIT = {
  '1个工作日': 86400,
  '2个工作日': 172800,
  '3个工作日': 259200,
  '5个工作日': 432000,
  '7个工作日': 604800,
  '9个工作日': 777600,
  '10个工作日': 864000,
};

/**
 * 类目配置项（category/query 的 configItems）
 * 数值来自前端枚举表，勿随意改
 */
const CONFIG_ITEM = {
  GuideFile: 5,                     // 电子说明书
  MatchNetContentRequired: 9,       // 净含量必填
  ProductNameExample: 21,
  SensitiveCategoryRestrict: 22,    // 敏感属性（返回 matchSensitiveConfigList）
  ProductNameMaxLen: 23,            // 商品名最大长度
  productSpecMaxLength: 24,         // 规格名最大长度
  NetWeightRequired: 25,
  ManufacturingAddressRequired: 26, // 制造地点必填
  NumberOfPiecesNewRequired: 27,    // 多件件数必填
};

/** 一次拿全的 configItems */
const CONFIG_ITEM_ALL = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 19, 21, 22, 23, 24, 25, 26, 27];

/** 单位换算（用户输入 → 实际存储值）*/
const UNIT = {
  // 重量：存毫克
  weight: { g: 1000, kg: 1000000, mg: 1, oz: 28349.5, lb: 453592 },
  // 长度：存毫米
  length: { cm: 10, mm: 1, m: 1000, in: 25.4, ft: 304.8 },
};

/** 女装/内衣常用默认值（可被 form 覆盖）*/
const DEFAULTS = {
  currencyType: 'CNY',
  semiLanguageStrategy: 2,
  personalizationSwitch: 0,
  countryShortName: 'CN',
  region2Id: 43000000000016,          // 江苏省
  // 撑起「体积重量」的默认值（内衣类）
  volume: { len: 100, width: 90, height: 80, inputLen: '10', inputHeight: '8', inputWidth: '9', inputUnit: 'cm' },
  weight: { value: 200000, inputValue: '200', inputUnit: 'g' },
};

/** 英文名最小长度（半托管硬性要求）*/
const EN_NAME_MIN = 20;

/**
 * SKU 分类（productSkuMultiPackReq.skuClassification）
 * 对应 UI「SKU分类」下拉
 *   1 单品        —— 仅 1 件（1 个碗 / 1 对耳环）
 *   2 同款多件装  —— 多件同规格同种类（2 个相同杯子）← 已实测（12 件套）
 *   3 混合套装    —— 多件不同品类/规格（1 个杯子 + 1 个勺子）← 未实测数值，按 UI 顺序推定
 */
const SKU_CLASSIFICATION = {
  单品: 1,
  同款多件装: 2,
  混合套装: 3,
};

/**
 * 敏感属性类型（productSkuSensitiveAttrReq.sensitiveTypes）
 * ⚠️ 中文标签来自 UI（图4），数值未逐一实测，用前先小批量验证
 */
const SENSITIVE_TYPES = [
  { value: 1, label: '纯电' },
  { value: 2, label: '内电' },
  { value: 3, label: '液体' },
  { value: 4, label: '粉末' },
  { value: 5, label: '磁性' },
  { value: 6, label: '膏体' },
  { value: 7, label: '刀具' },
];

/** 详情图文楼层 key */
const DECORATION_KEY = 'DecImage';

/** 尺码表模板的类型（contentVO.generalSizeType）*/
const GENERAL_SIZE_TYPE = {
  1: '均码',
  2: '常规',
  3: 'Tall',
};

/**
 * 新建尺码表（createSizeSpec）的默认参数
 * 取值来自官网「选择尺码表模板」弹窗点确认时的原始请求
 */
const SIZE_SPEC_DEFAULTS = {
  localSizeSource: 2,          // 2 = 官网默认
  generalSizeType: 1,          // 1=均码 2=常规 3=Tall（实际以蓝本为准）
  reuseable: false,            // 新建出来的表不进列表，只能本商品用
  // 不参与自动选中的测量项（官网原样）
  unEnbleAutoSelectedList: [10001, 10002, 10004, 10005, 10006, 10008, 10009, 10010, 10024, 10011],
};

/**
 * 风控头默认值
 *
 * sizespec/createSizeSpec 这类写接口会校验 anti-content 是否存在，
 * 缺失直接返回 40001 Invalid Login State。实测平台只校验"非空"，不校验内容。
 */
const ANTI_CONTENT_DEFAULT = '1';

/** 内存缓存 */
const memCache = new Map();

function cacheGet(key, ns) {
  // ⚠️ bridge 补丁：memCache 是进程级共享，必须按账号隔离，否则多用户并发会互相读到对方的
  //    站点/仓库/类目模板（历史故障：切账号后提示「该站点没有绑定发货仓」）
  const k = ns ? ns + '|' + key : key;
  const hit = memCache.get(k);
  if (!hit) return null;
  if (Date.now() - hit.ts > hit.ttl) { memCache.delete(k); return null; }
  return hit.data;
  // 注：原实现在过期分支里 delete(key)，加了命名空间后要 delete(k)
}

function cacheSet(key, data, ttl, ns) {
  const k = ns ? ns + '|' + key : key;
  memCache.set(k, { data, ts: Date.now(), ttl });
  return data;
}

/** 把 category path 展开成 cat1Id..cat10Id */
function flattenCatPath(catPath) {
  const arr = Array.isArray(catPath) ? catPath : String(catPath).split(/[>,/]/).map(Number);
  const out = {};
  for (let i = 1; i <= 10; i++) out['cat' + i + 'Id'] = arr[i - 1] || 0;
  return out;
}

/** 取最深一级作为 catId */
function leafCatId(catPath) {
  const arr = Array.isArray(catPath) ? catPath : String(catPath).split(/[>,/]/).map(Number);
  const valid = arr.filter((x) => x && Number.isFinite(Number(x)));
  return Number(valid[valid.length - 1]);
}

class Product {
  constructor(glo, opts = {}) {
    this.glo = glo;
    // 缓存命名空间：一个账号一份，避免多用户并发串数据
    this.ns = String(opts.account || '').trim().toLowerCase() || '';
  }

  // ==================== ① 模板层 ====================

  /**
   * 类目搜索（对应新建页「搜索分类」输入框）
   * 返回多条匹配，按路径展示
   * @param {string} keyword 关键词，如「情趣」
   * @param {object} opts { isEn: 用英文名搜, querySource: 默认 'mms_create_good' }
   * @returns {Array<{ catPath:number[], names:string[], leafCatId:number, isLeaf:boolean }>}
   */
  async searchCategories(keyword, opts = {}) {
    if (!keyword) throw new Error('[product.searchCategories] 需要 keyword');

    const body = {
      searchText: String(keyword),
      onlySecondHand: !!opts.onlySecondHand,
      querySource: opts.querySource || 'mms_create_good',
    };
    if (opts.isEn) body.enSearchText = String(keyword);

    const r = await this.glo.api('POST', '/anniston-agent-seller/category/search', body);
    if (String(r.code) !== '1000000') {
      throw new Error(`[product.searchCategories] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    return this._mapCatPaths(((r.body.result || {}).categoryPaths) || []);
  }

  /**
   * 按商品名推荐类目（商品名失焦时 UI 调的就是这个）
   * 通常只返回 1 条最匹配的，比 searchCategories 更准
   * @param {string} name 中文商品名
   * @param {object} opts { enName }
   */
  async suggestCategory(name, opts = {}) {
    const body = {};
    if (name) body.productNameZh = name;
    if (opts.enName) body.productNameEn = opts.enName;
    if (!body.productNameZh && !body.productNameEn) {
      throw new Error('[product.suggestCategory] 需要 name 或 opts.enName');
    }

    const r = await this.glo.api('POST', '/anniston-agent-seller/category/querySuggestion', body);
    if (String(r.code) !== '1000000') {
      throw new Error(`[product.suggestCategory] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    return this._mapCatPaths(((r.body.result || {}).categoryPaths) || []);
  }

  /**
   * 常用推荐类目（新建页顶部那 5 个）
   * @returns {Array<{ catPath:number[], names:string[], leafCatId:number, isLeaf:boolean }>}
   */
  async categoryHints() {
    const r = await this.glo.api('POST', '/visage-agent-seller/product/queryCatHints', {});
    if (String(r.code) !== '1000000') {
      throw new Error(`[product.categoryHints] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    // 结构是 { cat1:{catId,catName}, cat2:{...}, ... }，转成统一格式
    const hints = ((r.body.result || {}).catHints) || [];
    return hints.map((h) => {
      const catPath = [];
      const names = [];
      for (let i = 1; i <= 10; i++) {
        const n = h['cat' + i];
        if (n && n.catId) { catPath.push(n.catId); names.push(n.catName); }
      }
      return {
        catPath,
        names,
        leafCatId: catPath[catPath.length - 1] || null,
        isLeaf: true,
      };
    });
  }

  /**
   * 组装详情图文（goodsLayerDecorationReqs）
   *
   * 服务端结构（从已有商品 product/query 反查确认）：
   *   { lang, floorId, key:'DecImage', type:'image', priority,
   *     contentList: [{ imgUrl, height, width, text, textModuleDetails }] }
   *
   * 支持多楼层（实测最多见到 3 层），priority 从 0 递增。
   * 「装修页加文字」就是给 contentList[].text 赋值 + 调整 textModuleDetails。
   *
   * @param {Array<string|object>} items  URL 字符串，或 { url, text, textModuleDetails }
   * @param {string} fallback 没有给详情图时用首张轮播图
   */
  _buildDecoration(items, fallback) {
    const list = (items && items.length) ? items : (fallback ? [fallback] : []);
    const base = Date.now();

    return list.map((item, i) => {
      const node = typeof item === 'string' ? { url: item } : (item || {});
      return {
        lang: node.lang || 'zh',
        floorId: base + i * 32,
        key: DECORATION_KEY,
        type: 'image',
        priority: i,
        contentList: [Object.assign(
          {
            imgUrl: node.url,
            height: node.height || 1800,
            width: node.width || 1350,
          },
          // 只有真的写了文字才带 text / textModuleDetails：
          // 带了 text 却把 align 留空，product/add 会报 "Font Alignment Mode cannot be empty"
          node.text
            ? {
                text: node.text,
                textModuleDetails: {
                  fontSize: node.fontSize || 24,
                  fontColor: node.fontColor || '#000000',
                  fontFamily: node.fontFamily || 'PingFang SC',
                  align: node.align || 'center',
                  backgroundColor: node.backgroundColor || 'transparent',
                },
              }
            : {}
        )],
      };
    });
  }

  /** 把 categoryPaths（catNNodeVO）转统一格式 */
  _mapCatPaths(paths) {    return paths.map((p) => {
      const catPath = [];
      const names = [];
      let leaf = null;
      for (let i = 1; i <= 10; i++) {
        const node = p['cat' + i + 'NodeVO'];
        if (node && node.catId) {
          catPath.push(node.catId);
          names.push(node.catName);
          leaf = node;
        }
      }
      return {
        catPath,
        names,
        leafCatId: leaf ? leaf.catId : null,
        isLeaf: leaf ? !!leaf.isLeaf : false,
      };
    });
  }

  /**
   * 发货仓列表（带中文名称）
   * 商品接口只回 warehouseId，名称要从这里查
   * @param {object} opts { siteId: 只取覆盖该站点的仓 }
   * @returns {Array<{ warehouseId, warehouseName, siteIds:number[], siteNames:string[] }>}
   */
  async sendWarehouses(opts = {}) {
    const r = await this.glo.api('POST', '/visage-agent-seller/supplier/sendWarehouse/list', {});
    if (String(r.code) !== '1000000') {
      throw new Error(`[product.sendWarehouses] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    let list = ((r.body.result || {}).sendWarehouseList) || [];
    if (opts.siteId) {
      list = list.filter((w) => (w.coverSites || []).some((s) => String(s.siteId) === String(opts.siteId)));
    }
    return list.map((w) => ({
      warehouseId: w.warehouseId,
      warehouseName: w.warehouseName,
      siteIds: (w.coverSites || []).map((s) => s.siteId),
      siteNames: (w.coverSites || []).map((s) => s.siteName),
    }));
  }

  /**
   * 反查类目路径（catId → 完整层级名）
   * @param {number|number[]} catIds
   */
  async categoryPath(catIds) {
    const ids = Array.isArray(catIds) ? catIds : [catIds];
    const r = await this.glo.api('POST', '/anniston-agent-seller/category/path/list', {
      catIds: ids.map(Number),
      showCatEnName: true,
    });
    if (String(r.code) !== '1000000') {
      throw new Error(`[product.categoryPath] ${(r.body && r.body.errorMsg) || r.code}`);
    }

    const list = ((r.body.result || {}).queryCatPathRespList) || [];
    return list.map((item) => {
      const vo = item.categoryPathVO || {};
      const catPath = [];
      const names = [];
      const enNames = [];
      for (let i = 1; i <= 10; i++) {
        const node = vo['cat' + i + 'NodeVO'];
        if (node && node.catId) {
          catPath.push(node.catId);
          names.push(node.catName);
          enNames.push(node.catEnName || '');
        }
      }
      return {
        requestCatId: item.requestCatId,
        catPath,
        names,
        enNames,
        displayName: names.join(' > '),
      };
    });
  }

  /**
   * 子类目（逐级展开类目树）
   * @param {number} parentCatId 0 = 一级类目
   */
  async categoryChildren(parentCatId = 0) {
    const r = await this.glo.api('POST', '/anniston-agent-seller/category/children/list', {
      parentCatId: Number(parentCatId),
    });
    if (String(r.code) !== '1000000') {
      throw new Error(`[product.categoryChildren] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    const nodes = ((r.body.result || {}).categoryNodeVOS) || [];
    return nodes.map((n) => ({
      catId: n.catId,
      catName: n.catName,
      catEnName: n.catEnName,
      catLevel: n.catLevel,
      parentCatId: n.parentCatId,
      isLeaf: !!n.isLeaf,
      isHidden: !!n.isHidden,
    }));
  }

  /**
   * 类目属性模板（缓存 24h）
   * @param {number} catId
   */
  async categoryTemplate(catId, opts = {}) {
    const key = 'tmpl:' + catId;
    if (!opts.refresh) {
      const hit = cacheGet(key, this.ns);
      if (hit) return hit;
    }
    const r = await this.glo.api('POST', '/anniston-agent-seller/category/template/query', { catId: Number(catId) });
    if (String(r.code) !== '1000000') {
      throw new Error(`[product.categoryTemplate] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    return cacheSet(key, r.body.result, TTL.TEMPLATE, this.ns);
  }

  /** 用模板建引擎 */
  engine(template) {
    const { TemplateEngine } = require('./template_engine');
    return new TemplateEngine(template);
  }

  /**
   * 类目配置（决定哪些字段必填）
   *
   * 返回 29 个配置项，关键字段：
   *   matchSensitiveConfigList          敏感属性可选列表（null = 本类目不需要填）
   *   productTitleMaxLength             商品名长度上限
   *   productSpecMaxLength              规格名长度上限
   *   matchEnglishNameRequired          英文名是否必填
   *   needGuideFile                     是否需要电子说明书
   *   isSupportPersonalization          是否支持定制
   *   matchSkuAccessoriesRequired       SKU 配件是否必填
   *   matchNetContentRequired           净含量是否必填
   *   matchManufacturingLocationRequired 制造地点是否必填
   *
   * @param {number} leafCatId
   * @param {object} opts { configItems, bindSiteIds }
   */
  async categoryConfig(leafCatId, opts = {}) {
    // ⚠️ bridge 补丁：原实现没有缓存，8 项必填校验每个商品都要多打一个请求。
    //    类目配置一天内基本不变，按「类目 + 绑定站点」缓存。
    const siteNs = (opts.bindSiteIds && opts.bindSiteIds.length) ? opts.bindSiteIds.join(',') : '0';
    const cfgKey = 'cfg:' + leafCatId + ':' + siteNs;
    if (!opts.refresh) {
      const cached = cacheGet(cfgKey, this.ns);
      if (cached) return cached;
    }
    const r = await this.glo.api('POST', '/visage-agent-seller/config/common/category/query', {
      leafCatId: Number(leafCatId),
      configItems: opts.configItems || CONFIG_ITEM_ALL,
      bindSiteIds: opts.bindSiteIds || [],
      productProperties: opts.productProperties || [],
    });
    if (String(r.code) !== '1000000') {
      throw new Error(`[product.categoryConfig] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    return cacheSet(cfgKey, (r.body.result) || {}, TTL.TEMPLATE, this.ns);
  }

  /**
   * 敏感属性配置
   * 返回 null 表示本类目不需要填敏感属性（绝大多数类目都是 null）
   * @param {number} leafCatId
   * @param {number} siteId
   * @returns {Array|null} sensitiveList 的可选值
   */
  async sensitiveConfig(leafCatId, siteId = 100) {
    const cfg = await this.categoryConfig(leafCatId, {
      configItems: [CONFIG_ITEM.SensitiveCategoryRestrict],
      bindSiteIds: [siteId],
    });
    const list = cfg.matchSensitiveConfigList;
    return list && list.length ? list : null;
  }

  /** 站点列表（缓存 24h）*/
  async siteList(opts = {}) {
    if (!opts.refresh) {
      const hit = cacheGet('sites', this.ns);
      if (hit) return hit;
    }
    const r = await this.glo.api('POST', '/visage-agent-seller/config/common/site/query', {});
    if (String(r.code) !== '1000000') {
      throw new Error(`[product.siteList] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    return cacheSet('sites', r.body.result, TTL.SITE, this.ns);
  }

  /**
   * 地区配置（商品产地 / 制造地点）
   * 一次拿全：[1]=产地国家 [2]=通用国家 [3]=中国省份 [4]=免产地证明 [5]=制造地点
   */
  async regions(opts = {}) {
    if (!opts.refresh) {
      const hit = cacheGet('regions', this.ns);
      if (hit) return hit;
    }
    const r = await this.glo.api('POST', '/visage-agent-seller/config/common/region/query', {
      configItems: [1, 2, 3, 4, 5],
    });
    if (String(r.code) !== '1000000') {
      throw new Error(`[product.regions] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    return cacheSet('regions', r.body.result, TTL.REGION, this.ns);
  }

  /** 仓库列表（按站点）*/
  async warehouseList(siteId, opts = {}) {
    const key = 'wh:' + (siteId || 0);
    if (!opts.refresh) {
      const hit = cacheGet(key, this.ns);
      if (hit) return hit;
    }
    const r = await this.glo.api(
      'POST',
      '/darwin-mms/api/kiana/ghost/btg/route/queryWarehouseGoodsRouteTemplateList',
      {}
    );
    if (String(r.code) !== '1000000') {
      throw new Error(`[product.warehouseList] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    const list = ((r.body.result || {}).templateList) || [];
    const cfg = list[0] && list[0].goodsRouteTemplateInfoDTO;
    const siteCfgs = (cfg && cfg.templateConfig && cfg.templateConfig.templateConfig) || [];
    const hit = siteCfgs.find((x) => String(x.siteId) === String(siteId)) || siteCfgs[0] || {};
    const result = {
      templateId: list[0] && list[0].templateId,
      templateName: cfg && cfg.templateName,
      warehouseIds: hit.warehouseIdList || [],
      all: siteCfgs,
    };
    return cacheSet(key, result, TTL.ENV, this.ns);
  }

  /**
   * 运费模板
   * ⚠️ 必须带 siteIds，否则返回空
   */
  async freightTemplates(siteIds, opts = {}) {
    const ids = Array.isArray(siteIds) ? siteIds : [siteIds];
    const key = 'freight:' + ids.join(',');
    if (!opts.refresh) {
      const hit = cacheGet(key, this.ns);
      if (hit) return hit;
    }
    const r = await this.glo.api('POST', '/visage-agent-seller/freight/template/list', { siteIds: ids.map(Number) });
    if (String(r.code) !== '1000000') {
      throw new Error(`[product.freightTemplates] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    const list = ((r.body.result || {}).freightTmplSimpleList) || [];
    return cacheSet(key, list, TTL.ENV, this.ns);
  }

  /** 尺码规格配置 */
  async sizeSpecConfig(catId) {
    const r = await this.glo.api('POST', '/anniston-agent-seller/sizespec/querySizeSpecConfig', { catId: Number(catId) });
    return {
      ok: String(r.code) === '1000000',
      data: (r.body && r.body.result) || null,
    };
  }

  /**
   * 该站点在该类目下允许的尺码组 id
   *
   * 官网前端拉「尺码表」列表时会带 groupId；不带则是"不分组/别的组"的模板，
   * 提交 product/add 就会报 6000012「不合法的尺码模板id」。
   *   siteId=100 → 6（US），来源 sizeSpecConfig.groupIdConfigResp.siteId2GroupIdsMap
   */
  async siteSizeGroupId(catId, siteId) {
    const cfg = await this.sizeSpecConfig(catId);
    const map = ((cfg.data || {}).groupIdConfigResp || {}).siteId2GroupIdsMap || {};
    const ids = map[String(siteId)] || [];
    return ids.length ? Number(ids[0]) : null;
  }

  /**
   * 尺码体系配置（官网「尺码规格」下拉的数据源之一）
   *
   * 返回 sizeTypeConfigs：按 sizeType 分组的尺码 specId 列表
   * 实测 39276 → 10 组（sizeType 1/2/3/4/5/9/10/11/14/100）
   *
   * ⚠️ 接口不给 sizeType 的中文名，映射在前端常量里。
   * ⚠️ 这不是「尺码表模板列表」—— 模板是用户保存的、带数量（如「中国码(29)」），
   *    对应字段 sizeTemplateIds，目前没找到列表接口。
   *
   * @returns {{ sizeTypes: Array<{sizeType:number, specIds:number[], subTypes:any}>, raw:object }}
   */
  async sizeTypeConfig() {
    const r = await this.glo.api('POST', '/anniston-agent-seller/config/common/size/query', {
      configItems: [2],
    });
    if (String(r.code) !== '1000000') {
      throw new Error(`[product.sizeTypeConfig] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    const res = (r.body.result) || {};
    const sizeTypes = (res.sizeTypeConfigs || []).map((t) => ({
      sizeType: t.sizeType,
      specIds: t.specIds || [],
      subTypes: t.sizeSubTypeConfigs || null,
    }));
    return { sizeTypes, raw: res };
  }

  /** 尺码排序权重（specId → 排序值）*/
  async sizeOrderConfig() {
    const r = await this.glo.api('POST', '/anniston-agent-seller/config/common/size/query', {
      configItems: [1],
    });
    if (String(r.code) !== '1000000') {
      throw new Error(`[product.sizeOrderConfig] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    return ((r.body.result || {}).sizeOrderConfig) || {};
  }

  /**
   * 尺码表模板列表（对应官网「尺码表 → 添加尺码表」以及尺寸规格下拉）
   *
   * 返回项的 businessId 就是 product/add 里 sizeTemplateIds 要填的值。
   *
   * @param {number} catId
   * @param {object} opts { offset, pageSize }
   * @returns {{ total:number, list:Array<{
   *   businessId, name, classId, generalSizeType, sizeTypeName,
   *   localSizeSource, elements, sizes, rowCount, updatedAt
   * }> }}
   */
  async sizeTemplateList(catId, opts = {}) {
    const body = {
      catId: Number(catId),
      offset: opts.offset || 0,
      pageSize: opts.pageSize || 100,
    };
    // 官网「尺码表」下拉调用时会带这两个参数（前端 Ga.x 的原样）：
    //   groupId       尺码组（站点决定，如 siteId=100 → 6=US），不传会拿到不分组/别的组的模板
    //   shoeSizeType  鞋类尺码类型，服装类传 undefined
    if (opts.groupId) body.groupId = Number(opts.groupId);
    if (opts.shoeSizeType !== undefined && opts.shoeSizeType !== null) {
      body.shoeSizeType = opts.shoeSizeType;
    }
    // 尺码表「蓝本」列表。返回的 businessId 只能当蓝本：
    // product/add 不接受它，必须先用 createSizeSpec 基于它生成新表（见 createSizeSpec）
    const r = await this.glo.api('POST', '/anniston-agent-seller/sizespec/rollPageQueryData', body);
    if (String(r.code) !== '1000000') {
      throw new Error(`[product.sizeTemplateList] ${(r.body && r.body.errorMsg) || r.code}`);
    }

    const res = (r.body.result) || {};
    const raw = res.sizeSpecDataVOList || [];

    const list = raw.map((t) => {
      const cv = t.contentVO || {};
      const meta = cv.meta || {};
      const records = cv.records || [];
      const sizes = records
        .map((rec) => (rec.values && rec.values['1']) || null)
        .filter(Boolean);

      return {
        businessId: t.businessId,          // ← 填 sizeTemplateIds
        name: t.name,
        classId: t.classId,
        generalSizeType: cv.generalSizeType,
        sizeTypeName: GENERAL_SIZE_TYPE[cv.generalSizeType] || null,
        localSizeSource: cv.localSizeSource,
        // 测量项（胸围/腰围/衣长…）
        elements: (meta.elements || []).map((e) => ({
          id: e.id, name: e.name, unit: e.unit,
        })),
        // 该模板包含的尺码值（one-size / 2XL …）
        sizes,
        rowCount: records.length,
        updatedAt: t.updatedAt,
        contentVO: cv,                     // createSizeSpec 的 content 直接用这个
      };
    });

    return { total: res.totalCount || list.length, list };
  }

  /**
   * 校验 sizeTemplateIds 是否合法（6000012 自查）
   *
   * 三种成因，逐层查：
   *   ① 模板不属于这个叶子类目   → `bad`
   *   ② 模板 generalSizeType 与所选尺码类型不符 → `typeBad`（需传 generalSizeType）
   *   ③ 模板的 sizes 覆盖不到所选尺码值 → `sizeBad`（需传 sizeValues）
   *
   * @param {object} opts
   *   catId            叶子类目 id（必须与 catPath 末级一致）
   *   sizeTemplateIds  准备提交的模板 id 数组
   *   generalSizeType  可选，期望类型（1=均码 2=常规 3=Tall）
   *   sizeValues       可选，本次提交的尺码值（['M','L'] / ['one-size']）
   * @returns {{ok, bad, typeBad, sizeBad, available, suggestion, reason}}
   */
  /**
   * 新建尺码表（官网「选择尺码表模板」弹窗点「确认」时走的接口）
   *
   * ⚠️ product/add 不接受模板列表里的 businessId（那是"蓝本"），
   *    必须先用本方法基于蓝本生成一张新表，用返回的 businessId 提交。
   *
   * @param {object} opts
   *   catId              叶子类目 id
   *   classId            模板的 classId（列表项里有）
   *   name               表名（一般沿用蓝本名）
   *   content            蓝本的 contentVO（meta/records/bodyMeta/bodyRecords）
   *   generalSizeType    1=均码 2=常规 3=Tall
   *   localSizeSource    2（官网默认）
   *   antiContent        风控头；实测只校验"非空"，默认填 '1'
   * @returns {{ ok:boolean, businessId?:number, msg?:string, raw?:object }}
   */
  async createSizeSpec(opts = {}) {
    const catId = Number(opts.catId);
    const classId = Number(opts.classId);
    if (!catId) return { ok: false, businessId: null, msg: '需要 catId（叶子类目）' };
    if (!classId) return { ok: false, businessId: null, msg: '需要 classId（尺码表蓝本的 classId）' };

    const cv = opts.content || {};
    const records = cv.records || [];
    if (!records.length) {
      return { ok: false, businessId: null, msg: 'content.records 为空：请把蓝本的 contentVO 传进来' };
    }

    const body = {
      content: {
        meta: cv.meta || { elements: [], groups: [] },
        records,
        bodyMeta: cv.bodyMeta || { elements: [], groups: [] },
        bodyRecords: cv.bodyRecords || [],
        localSizeSource: opts.localSizeSource || cv.localSizeSource || SIZE_SPEC_DEFAULTS.localSizeSource,
        generalSizeType: opts.generalSizeType || cv.generalSizeType || SIZE_SPEC_DEFAULTS.generalSizeType,
      },
      catId,
      classId,
      name: opts.name || 'size',
      reusable: false,
      ext: {
        manualGroupIdList: [],
        isDoubleSize: false,
        unEnbleAutoSelectedList: opts.unEnbleAutoSelectedList || SIZE_SPEC_DEFAULTS.unEnbleAutoSelectedList,
      },
      productCreateTime: null,
    };

    const r = await this.glo.api('POST', '/anniston-agent-seller/sizespec/createSizeSpec', body, {
      antiContent: opts.antiContent || ANTI_CONTENT_DEFAULT,
      referer: opts.referer,
    });
    const ok = String(r.code) === '1000000';
    return {
      ok,
      businessId: ok ? ((r.body.result || {}).businessId || null) : null,
      classId: ok ? ((r.body.result || {}).classId || classId) : null,
      code: ok ? '1000000' : String(r.code),
      msg: ok ? '' : ((r.body && r.body.errorMsg) || String(r.code)),
      raw: r.body,
    };
  }

  /**
   * 基于某个尺码表蓝本新建一张表（拉列表 → 匹配 → createSizeSpec）
   * @returns { ok, businessId, template }
   */
  async createSizeSpecFromTemplate(catId, templateId, opts = {}) {
    const { list } = await this.sizeTemplateList(catId, {
      groupId: opts.groupId,
      shoeSizeType: opts.shoeSizeType,
    });
    const tpl = list.find((t) => String(t.businessId) === String(templateId));
    if (!tpl) {
      return {
        ok: false,
        msg: `蓝本 ${templateId} 不在 catId=${catId} 的列表里（现有 ${list.map((t) => t.businessId).join(', ') || '无'}）`,
        available: list,
      };
    }
    const made = await this.createSizeSpec({
      catId,
      classId: tpl.classId,
      name: opts.name || tpl.name,
      content: tpl.contentVO,
      generalSizeType: tpl.generalSizeType,
      antiContent: opts.antiContent,
      referer: opts.referer,
    });
    return Object.assign({ template: tpl }, made);
  }

  async validateSizeTemplate(opts = {}) {
    const catId = Number(opts.catId);
    const ids = (opts.sizeTemplateIds || []).map(String);
    const sizeValues = (opts.sizeValues || []).map(String).filter(Boolean);
    const { list } = await this.sizeTemplateList(catId, {
      groupId: opts.groupId,
      shoeSizeType: opts.shoeSizeType,
    });

    const byId = new Map(list.map((t) => [String(t.businessId), t]));
    // trustedIds：本次刚 createSizeSpec 出来的表，不在列表里但一定合法
    const trusted = (opts.trustedIds || []).map(String);
    const bad = ids.filter((id) => !byId.has(id) && !trusted.includes(id));
    const reason = [];

    if (bad.length) {
      reason.push(
        `模板 ${bad.join(', ')} 不在 catId=${catId} 的模板列表里` +
        `（该 catId 现有 ${list.length} 个：${list.map((t) => t.businessId).join(', ') || '无'}）`
      );
    }

    // ② 类型比对（给了才查）
    let typeBad = [];
    if (opts.generalSizeType) {
      typeBad = ids.filter((id) => byId.has(id) && byId.get(id).generalSizeType !== opts.generalSizeType);
      if (typeBad.length) {
        reason.push(
          `模板 ${typeBad.join(', ')} 的类型与期望不符` +
          `（期望 generalSizeType=${opts.generalSizeType}，实际 ${typeBad.map((id) => byId.get(id).generalSizeType).join(', ')}）`
        );
      }
    }

    // ③ 尺码覆盖（给了 sizeValues 才查）
    let sizeBad = [];
    if (sizeValues.length) {
      for (const id of ids) {
        const t = byId.get(id);
        if (!t) continue;
        const have = new Set((t.sizes || []).map(String));
        const missing = sizeValues.filter((v) => !have.has(v));
        if (missing.length) sizeBad.push({ businessId: Number(id), missing, sizes: t.sizes || [] });
      }
      if (sizeBad.length) {
        reason.push(
          '模板尺码覆盖不到所选尺码：' +
          sizeBad.map((x) => `${x.businessId} 缺 ${x.missing.join('/')}（模板内置 ${x.sizes.join('/') || '无'}）`).join('；')
        );
      }
    }

    const candidates = list.filter((t) => {
      if (opts.generalSizeType && t.generalSizeType !== opts.generalSizeType) return false;
      if (sizeValues.length) {
        const have = new Set((t.sizes || []).map(String));
        if (sizeValues.some((v) => !have.has(v))) return false;
      }
      return true;
    });

    return {
      ok: !bad.length && !typeBad.length && !sizeBad.length,
      bad: bad.map(Number),
      typeBad: typeBad.map(Number),
      sizeBad,
      available: list,
      suggestion: candidates.length ? candidates[0].businessId : (list[0] && list[0].businessId) || null,
      reason: reason.join('；'),
    };
  }

  /**
   * 图片内容要求（每个类目不同）
   * @returns {{ itemRequirement:string, positions:object[], sampleFile:string }}
   */
  async imageTemplate(catId) {
    const r = await this.glo.api('POST', '/anniston-agent-seller/image/upload/template/queryTemplate', { catId: Number(catId) });
    const vo = ((r.body || {}).result || {}).templateVO || {};
    const items = vo.itemList || [];
    return items[0] || null;
  }

  /**
   * 店铺环境打包（站点 + 仓库 + 仓库名称 + 运费模板）
   * @param {number} siteId
   */
  async storeEnv(siteId, opts = {}) {
    const [wh, freight, named] = await Promise.all([
      this.warehouseList(siteId, opts).catch(() => ({ warehouseIds: [] })),
      this.freightTemplates([siteId], opts).catch(() => []),
      this.sendWarehouses({ siteId }).catch(() => []),
    ]);

    // 用带名称的接口补全，拿不到就退回 ID 列表
    const warehouses = named.length
      ? named.filter((w) => wh.warehouseIds.includes(w.warehouseId) || !wh.warehouseIds.length)
      : (wh.warehouseIds || []).map((id) => ({ warehouseId: id, warehouseName: id }));

    return {
      siteId: Number(siteId),
      warehouseIds: wh.warehouseIds || [],
      warehouses,
      freightTemplates: freight,
      freightTemplateId: freight[0] ? freight[0].freightTemplateId : null,
    };
  }

  // ==================== ② 草稿 ====================

  /**
   * 建草稿
   * ⚠️ 必须同时给：siteId / bindSiteIds / catId / catIds / cat1Id..cat10Id / catType
   *    少任何一项都会返回 productDraftId: undefined
   * @param {number[]} catPath  类目路径，如 [27011,28946,39027,39258,39259,39276]
   * @param {number} siteId
   */
  async createDraft(catPath, siteId) {
    const flat = flattenCatPath(catPath);
    const catId = leafCatId(catPath);
    const catIds = Object.keys(flat)
      .map((k) => flat[k])
      .filter((x) => x);

    const r = await this.glo.api('POST', '/visage-agent-seller/product/draft/add', Object.assign({
      siteId: Number(siteId),
      bindSiteIds: [Number(siteId)],
      catId,
      catIds,
      catType: 1,
    }, flat));

    if (String(r.code) !== '1000000') {
      throw new Error(`[product.createDraft] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    const draftId = (r.body.result || {}).productDraftId;
    if (!draftId) throw new Error('[product.createDraft] 未返回 productDraftId，检查分类字段是否完整');
    return draftId;
  }

  /** 保存草稿（校验用，通过时返回 code=1000000）*/
  async saveDraft(body) {
    const r = await this.glo.api('POST', '/visage-agent-seller/product/draft/save', body);
    return {
      ok: String(r.code) === '1000000',
      code: r.code,
      msg: (r.body && r.body.errorMsg) || '',
    };
  }

  /** 查草稿 */
  async queryDraft(productDraftId) {
    const r = await this.glo.api('POST', '/visage-agent-seller/product/draft/query', { productDraftId });
    return {
      ok: String(r.code) === '1000000',
      code: r.code,
      msg: (r.body && r.body.errorMsg) || '',
      data: (r.body && r.body.result) || null,
    };
  }

  /**
   * 草稿列表
   * ⚠️ 参数是 page，不是 pageNum/pageInfo
   * @param {object} opts { page, pageSize }
   */
  async listDrafts(opts = {}) {
    const r = await this.glo.api('POST', '/visage-agent-seller/product/draft/pageQuery', {
      page: opts.page || 1,
      pageSize: opts.pageSize || 50,
    });
    if (String(r.code) !== '1000000') {
      throw new Error(`[product.listDrafts] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    const res = (r.body.result) || {};
    return { total: res.total || 0, list: res.pageItems || [] };
  }

  /**
   * 批量删草稿
   * ⚠️ 字段名是 productDraftIds（复数），单次建议 ≤20
   * @param {number[]} draftIds
   */
  async deleteDrafts(draftIds) {
    const ids = (Array.isArray(draftIds) ? draftIds : [draftIds]).filter(Boolean);
    if (!ids.length) throw new Error('[product.deleteDrafts] 需要 draftIds');

    let ok = 0;
    for (let i = 0; i < ids.length; i += 20) {
      const chunk = ids.slice(i, i + 20);
      const r = await this.glo.api('POST', '/visage-agent-seller/product/draft/batchDelete', {
        productDraftIds: chunk,
      });
      if (String(r.code) === '1000000') ok += chunk.length;
      else throw new Error(`[product.deleteDrafts] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    return { ok, deleted: ok };
  }

  // ==================== ③ 组装 ====================

  /**
   * 按类目配置校验表单，提前拦住必填缺失
   * @param {object} form
   * @param {object} cfg  categoryConfig() 的返回
   * @param {TemplateEngine} eng
   * @returns {string[]} 问题列表（空数组 = 通过）
   */
  validateByConfig(form, cfg, eng = null) {
    const errs = [];

    // 商品名
    if (!form.name) errs.push('缺商品名 name');
    else if (cfg.productTitleMaxLength && form.name.length > cfg.productTitleMaxLength) {
      errs.push(`商品名超长（${form.name.length} > ${cfg.productTitleMaxLength}）`);
    }

    // 英文名：半托管要求 ≥20 字，否则 product/add 报
    // 2000177 "半托管商品英文标题最少需要20个字"
    // ⚠️ 有时会误报成 6000002 "货品类目属性更新"，别被误导
    if (form.enName !== undefined) {
      const enLen = String(form.enName || '').length;
      if (enLen && enLen < EN_NAME_MIN) {
        errs.push(`英文名太短（${enLen} < ${EN_NAME_MIN} 字），product/add 会报 2000177`);
      }
    }
    if (cfg.matchEnglishNameRequired && !form.enName) {
      errs.push('本类目英文名必填（缺 enName）');
    }

    // 电子说明书
    if (cfg.needGuideFile && !(form.guideFiles && form.guideFiles.length)) {
      errs.push('本类目需要电子说明书（缺 guideFiles）');
    }

    // 制造地点
    if (cfg.matchManufacturingLocationRequired && !(form.manufacturing && form.manufacturing.length)) {
      errs.push('本类目制造地点必填（缺 manufacturing）');
    }

    // 净含量
    if (cfg.matchNetContentRequired && Number(form.skuClassification) === 2) {
      const hasNet = (form.netContent && Object.keys(form.netContent).length)
        || (form.totalNetContent && Object.keys(form.totalNetContent).length);
      if (!hasNet) errs.push('本类目净含量必填（多件 SKU 需填 netContent / totalNetContent）');
    }

    // 多件件数
    if (cfg.matchNumberOfPiecesNewRequired && Number(form.skuClassification) === 2 && !Number(form.pieces)) {
      errs.push('本类目多件件数必填（缺 pieces）');
    }

    // 混合套装必须填包装清单
    if (Number(form.skuClassification) === SKU_CLASSIFICATION.混合套装) {
      if (!(form.packList && form.packList.length)) {
        errs.push('SKU分类=混合套装 时必须填包装清单（packList: [{vid,num,unitCode}]）');
      }
    }

    // 敏感属性
    const sens = cfg.matchSensitiveConfigList;
    if (sens && sens.length) {
      if (!form.isSensitive) errs.push('本类目必须标记为敏感商品（isSensitive: true）');
      else if (!(form.sensitiveList && form.sensitiveList.length)) {
        errs.push('本类目敏感属性必填（sensitiveList 为空）');
      }
    }

    // 规格名长度
    if (eng && cfg.productSpecMaxLength) {
      for (const kind of ['color', 'size']) {
        const vid = kind === 'color' ? form.colorVid : form.sizeVid;
        if (vid === undefined) continue;
        const v = eng.findSpec(kind, vid) || (kind === 'color' ? eng.colorOptions() : eng.sizeOptions()).find((x) => x.vid === vid);
        if (v && v.value && v.value.length > cfg.productSpecMaxLength) {
          errs.push(`${kind === 'color' ? '颜色' : '尺码'}名超长: ${v.value}`);
        }
      }
    }

    return errs;
  }

  /**
   * 提交前校验：多语言图片请求
   *
   * 结构：`[{ language: 'es', imgUrlList: ['https://...'] }]`
   * ① 语言必须在 materialMultiLanguages 里勾过，否则服务端不认
   * ② 图片 URL 同样不能带 query
   */
  _assertI18nReqs(reqs, field, langs) {
    const list = (reqs || []).filter(Boolean);
    if (!list.length) return;
    if (!langs || !langs.length) {
      throw new Error(
        `[product.buildBody] 传了 ${field} 但 materialMultiLanguages 为空 —— ` +
        '素材语言没勾选，服务端不会认这些多语言图'
      );
    }
    for (const r of list) {
      if (!r.language) throw new Error(`[product.buildBody] ${field} 有一项缺 language`);
      if (!langs.includes(r.language)) {
        throw new Error(
          `[product.buildBody] ${field} 里的语言 ${r.language} 没在 materialMultiLanguages 里勾选（当前：${langs.join(', ')}）`
        );
      }
      this._assertCleanImageUrls(r.imgUrlList, `${field}[${r.language}].imgUrlList`);
    }
  }

  /**
   * 把「按语言分组的图片」拼成 product/add 需要的字段
   *
   *   buildImageI18nReqs({ common: [a], en: [b], es: [c] })
   *   → { carouselImageUrls: [a], carouselImageI18nReqs: [
   *         { language: 'en', imgUrlList: [b] }, { language: 'es', imgUrlList: [c] } ] }
   *
   * `common` = 不分语言的主图；其余键 = 该语言的替换图。
   * 对应官网表单里的 `carouselImgsI18n: { common, en, es }`。
   */
  buildImageI18nReqs(map = {}) {
    const reqs = Object.entries(map)
      .filter(([k, v]) => k !== 'common' && Array.isArray(v) && v.length)
      .map(([language, imgUrlList]) => ({ language, imgUrlList }));
    return {
      carouselImageUrls: map.common || [],
      carouselImageI18nReqs: reqs,
    };
  }

  /**
   * 提交前校验：图片 URL 不能带 query
   *
   * `?imageView2/...`、`?imageMogr2/...` 是**只用于前端展示**的地址，
   * 提进 product/add 会被 `2000346 Image suffix validation failed` 拒。
   * 要提交"已裁好的干净地址"，得用 material.upload({ crop }) 在上传时裁，
   * 或用 materialImageFrom() 把裁好的字节下载后重传。
   */
  _assertCleanImageUrls(urls, field) {
    for (const u of (urls || [])) {
      if (!u) continue;
      if (String(u).indexOf('?') > -1) {
        throw new Error(
          `[product.buildBody] ${field} 里有带 query 的 URL，提交会被 2000346 拒：${u}\n` +
          '  —— 缩略/裁剪参数只用于展示；提交要用干净地址（上传时裁切，或下载重传）'
        );
      }
    }
  }

  /**
   * 提交前校验：视频必须是转码完成形态（vid + destMaterialUrl）
   *
   * 刚上传完拿到的是 originalMaterialUrl（原片），转码完成后才出
   * destMaterialUrl + firstImageUrl + vid —— 提原片地址过不了。
   */
  _assertVideoReqs(list, field) {
    for (const v of (list || [])) {
      if (!v) continue;
      if (!v.vid) {
        throw new Error(
          `[product.buildBody] ${field} 缺 vid：视频要等转码完成（uploadStatus=3）再取，` +
          '用 material.waitVideoReady(id) 或 material.toVideoReq(素材项)'
        );
      }
      if (!v.videoUrl || !/\.mp4(\?|$)/i.test(String(v.videoUrl))) {
        throw new Error(
          `[product.buildBody] ${field} 的 videoUrl 必须是转码后的 .mp4（destMaterialUrl），当前：${v.videoUrl || '(空)'}`
        );
      }
    }
  }

  /**
   * 组装 product/add 请求体（纯函数，不碰网络）
   *
   * @param {object} form
   *   catPath[]         类目路径（必需）
   *   draftId           草稿 ID（必需）
   *   siteId            站点（必需）
   *   name / enName     中英文名称（必需）
   *   props             productPropertyReqs（由 eng.buildPropertyReqs 生成）
   *   specReqs          productSpecPropertyReqs（eng.buildSpecReq）
   *   colorVid / sizeVid 颜色/尺码 vid
   *   images[]          轮播图 URL（3~10 张，≥5 推荐）
   *   materialImgUrl    素材图
   *   skuPrice          申报价（元）
   *   stock             目标库存
   *   extCode           货号
   *   warehouseIds[]    仓库（默认取 env）
   *   freightTemplateId 运费模板（默认取 env）
   *   shipmentLimit     发货时效秒数或中文键（默认 2个工作日）
   *   sizeTemplateIds[] 尺码表模板
   *   decorationImages  详情图文：[url | {url,text,fontSize,fontColor,align,...}]
   *   volume / weight   体积重量（可省，用默认值）
   *   origin            产地 { countryShortName, region2Id }
   *   manufacturing     制造地点国家 shortName 数组
   *
   * @param {object} tmpl  模板（可省，用 eng 代替）
   * @param {object} env   storeEnv() 的返回
   * @param {TemplateEngine} eng  引擎（给了就用它生成规格，更稳）
   * @param {object} cfg   categoryConfig() 的返回（给了就按类目配置自动补敏感属性等）
   */
  buildBody(form, tmpl, env = {}, eng = null, cfg = null) {
    const siteId = Number(form.siteId || env.siteId || 100);
    const catId = leafCatId(form.catPath);
    const flat = flattenCatPath(form.catPath);
    const images = form.images || [];
    const whIds = form.warehouseIds || env.warehouseIds || [];
    const freightId = form.freightTemplateId || env.freightTemplateId;

    if (!images.length) throw new Error('[product.buildBody] images 至少 1 张');
    if (!whIds.length) throw new Error('[product.buildBody] 没有可用仓库（warehouseIds 为空）');
    if (!freightId) throw new Error('[product.buildBody] 没有可用运费模板');
    // ⚠️ 素材图必须 1:1 ≥800x800，不能拿轮播图（3:4）顶替
    if (!form.materialImgUrl) {
      throw new Error('[product.buildBody] 缺 materialImgUrl（素材图需 1:1 ≥800x800，用 ops.material.materialImage() 取）');
    }

    // ⚠️ 提交的图片 URL 不能带 query（缩略/裁剪参数只用于前端展示，会被 2000346 拒）
    this._assertCleanImageUrls(images, 'images');
    this._assertCleanImageUrls([form.materialImgUrl], 'materialImgUrl');
    if (Array.isArray(form.decorationImages) && form.decorationImages.length) {
      this._assertCleanImageUrls(form.decorationImages, 'decorationImages');
    }
    // ⚠️ 视频必须已经转码完成：要 vid + destMaterialUrl，不能塞 originalMaterialUrl
    // ⚠️ 多语言图片：语言要先在 materialMultiLanguages 里勾过，URL 同样不能带 query
    const langs = form.materialMultiLanguages || [];
    this._assertI18nReqs(form.carouselImageI18nReqs, 'carouselImageI18nReqs', langs);
    this._assertI18nReqs(form.skcCarouselImageI18nReqs, 'productSkcCarouselImageI18nReqs', langs);
    this._assertI18nReqs(form.skuThumbUrlI18nReqs, 'productSkuThumbUrlI18nReqs', langs);
    this._assertVideoReqs(form.carouseVideos, 'carouseVideos');
    this._assertVideoReqs(form.detailVideos, 'detailVideos');

    // ---- 规格 ----
    let specReqs = form.specReqs;
    let mainSkuSpecs;
    let skuSpecs;

    if (!specReqs && eng && form.colorVid && form.sizeVid) {
      specReqs = [
        eng.buildSpecReq('color', form.colorVid),
        eng.buildSpecReq('size', form.sizeVid),
      ];
      mainSkuSpecs = [eng.buildSkuSpec('color', form.colorVid)];
      skuSpecs = [
        eng.buildSkuSpec('color', form.colorVid),
        eng.buildSkuSpec('size', form.sizeVid),
      ];
    } else {
      if (!specReqs) throw new Error('[product.buildBody] 需要 specReqs 或 (eng + colorVid + sizeVid)');
      mainSkuSpecs = form.mainSkuSpecs || [{
        parentSpecId: specReqs[0].parentSpecId,
        parentSpecName: specReqs[0].parentSpecName,
        specId: specReqs[0].specId,
        specName: specReqs[0].specName,
        groupId: specReqs[0].groupId,
        vid: specReqs[0].vid,
      }];
      skuSpecs = form.skuSpecs || specReqs.map((s) => ({
        parentSpecId: s.parentSpecId,
        parentSpecName: s.parentSpecName,
        specId: s.specId,
        specName: s.specName,
        groupId: s.groupId,
        vid: s.vid,
      }));
    }

    // ---- 体积重量 ----
    const vol = Object.assign({}, DEFAULTS.volume, form.volume || {});
    const wei = Object.assign({}, DEFAULTS.weight, form.weight || {});

    // ---- 价格（元 → 分）----
    const price = Math.round(Number(form.skuPrice || 0) * 100);
    if (!price) throw new Error('[product.buildBody] skuPrice 必须大于 0');

    // ---- 库存 ----
    const stock = Number(form.stock || 0);
    if (!stock) throw new Error('[product.buildBody] stock 必须大于 0');

    // ---- 发货时效 ----
    let shipmentLimit = form.shipmentLimit || 172800;
    if (typeof shipmentLimit === 'string') {
      shipmentLimit = SHIPMENT_LIMIT[shipmentLimit];
      if (!shipmentLimit) throw new Error(`[product.buildBody] 未知发货时效: ${form.shipmentLimit}`);
    }

    // ---- 产地 ----
    const origin = Object.assign({
      countryShortName: DEFAULTS.countryShortName,
      region2Id: DEFAULTS.region2Id,
    }, form.origin || {});

    // ---- SKU 分类（1=单品 2=多件）----
    const skuClass = Number(form.skuClassification || 1);
    const pieces = Number(form.pieces || 1);
    let multiPackReq;
    if (skuClass === 2) {
      multiPackReq = {
        skuClassification: 2,
        numberOfPieces: pieces,
        pieceUnitCode: 1,
        // 「共计内含」：套装里的具体件数说明（可选）
        productSkuNetContentReq: form.netContent || {},
        totalNetContent: form.totalNetContent || {},
        individuallyPacked: form.individuallyPacked ? 1 : 0,
        numberOfPiecesNew: pieces,
        pieceNewUnitCode: 1,
      };
    } else {
      // ⚠️ 单品：件数必须为 1，且不能填 individuallyPacked
      multiPackReq = {
        skuClassification: 1,
        numberOfPieces: 1,
        pieceUnitCode: 1,
        numberOfPiecesNew: 1,
        pieceNewUnitCode: 1,
      };
    }

    // ---- 敏感属性：类目配置有列表时自动填 ----
    const cfgSensitive = (cfg && cfg.matchSensitiveConfigList) || null;
    const isSensitive = form.isSensitive !== undefined
      ? !!form.isSensitive
      : !!(cfgSensitive && cfgSensitive.length);
    const sensitiveList = (form.sensitiveList && form.sensitiveList.length)
      ? form.sensitiveList
      : (cfgSensitive || []);

    const body = Object.assign({}, flat, {
      // 分类
      catId,
      catIds: Object.keys(flat).map((k) => flat[k]).filter((x) => x),
      catType: 1,

      // 基本信息
      materialMultiLanguages: form.materialMultiLanguages || [],
      productName: form.name,
      productI18nReqs: form.productI18nReqs || [{ productName: form.enName || form.name, language: 'en' }],

      // 属性
      productPropertyReqs: form.props || [],

      // 规格
      productSpecPropertyReqs: specReqs,

      // SKC / SKU
      productSkcReqs: [{
        previewImgUrls: images.slice(0, 10),
        productSkcCarouselImageI18nReqs: form.skcCarouselImageI18nReqs || [],
        extCode: form.extCode || '',
        mainProductSkuSpecReqs: mainSkuSpecs,
        productSkuReqs: [{
          thumbUrl: images[0],
          productSkuThumbUrlI18nReqs: form.skuThumbUrlI18nReqs || [],
          extCode: form.skuExtCode || '',
          // 参考链接（助力快速上新）
          referenceLink: form.referenceLink || '',
          siteSupplierPrices: [{ siteId, supplierPrice: price }],
          // 制造商建议零售价（可选，单位分）
          productSkuUsSuggestedPriceReq: form.suggestedRetailPrice
            ? { price: Math.round(Number(form.suggestedRetailPrice) * 100), currencyType: form.currencyType || DEFAULTS.currencyType }
            : {},
          currencyType: form.currencyType || DEFAULTS.currencyType,
          productSkuSpecReqs: skuSpecs,
          productSkuId: 0,
          productSkuWhExtAttrReq: {
            productSkuVolumeReq: vol,
            productSkuWeightReq: wei,
            productSkuBarCodeReqs: [],
            productSkuSensitiveAttrReq: {
              isSensitive: isSensitive ? 1 : 0,
              sensitiveTypes: form.sensitiveTypes || [],
              sensitiveList,
              isForce2Normal: !!form.isForce2Normal,
              force2NormalTypes: form.force2NormalTypes || null,
            },
            productSkuSensitiveLimitReq: {},
          },
          productSkuMultiPackReq: multiPackReq,
          // ⚠️ 库存字段是 targetStockAvailable
          // ⚠️ bridge 补丁：上游给每个仓库都填同一个 stock（总库存 600 + 3 个仓 = 每仓 600，
          //    实际总库存变 1800）。这里优先用 form.stockPerWarehouse 按仓拆分，
          //    没有该字段时才退回上游行为。
          productSkuStockQuantityReq: {
            warehouseStockQuantityReqs: whIds.map((w) => ({
              warehouseId: w,
              targetStockAvailable:
                form.stockPerWarehouse && form.stockPerWarehouse[w] != null
                  ? Math.max(0, Math.floor(Number(form.stockPerWarehouse[w]) || 0))
                  : stock,
            })),
          },
          productSkuAccessoriesReq: {
            // 包装清单（混合套装必填）：[{ vid, num, unitCode }]
            productSkuAccessories: form.packList || [],
          },
          productSkuNonAuditExtAttrReq: {},
        }],
        productSkcId: 0,
        colorImageUrl: form.colorImageUrl || images[0],
        isBasePlate: 0,
      }],

      // 图片
      carouselImageUrls: images,
      carouselImageI18nReqs: form.carouselImageI18nReqs || [],
      materialImgUrl: form.materialImgUrl,
      goodsLayerDecorationReqs: form.decorationReqs || this._buildDecoration(
        form.decorationImages || [],
        images[0]
      ),
      goodsLayerDecorationCustomizeI18nReqs: form.decorationI18nReqs || [],

      // 尺码
      sizeTemplateIds: form.sizeTemplateIds || [],
      showSizeTemplateIds: form.showSizeTemplateIds || form.sizeTemplateIds || [],
      // 官网表单里尺码表选的是 productSizeTemplateIds（getValues 会一起 spread 进 payload），
      // add 接口按它校验模板归属；缺这个字段会报 6000012
      productSizeTemplateIds: form.productSizeTemplateIds || form.sizeTemplateIds || [],
      goodsModelReqs: [],

      // 产地 / 包装
      productWhExtAttrReq: {
        outerGoodsUrl: form.outerGoodsUrl || '',
        productOrigin: origin,
      },
      productOuterPackageReq: form.outerPackage || {},
      productOuterPackageImageReqs: [],
      sensitiveTransNormalFileReqs: [],

      // 视频 / 说明书（可选，传空数组即可）
      productCarouseVideoReqList: form.carouseVideos || [],
      productDetailVideoReqList: form.detailVideos || [],
      goodsAdvantageLabelTypes: [],
      productGuideFileNewReqList: form.guideFiles || [],
      productGuideFileI18nReqs: [],
      productOriginCertFileReqs: [],

      // 销售扩展
      productSaleExtAttrReq: {
        bodyShape: form.bodyShape || null,
        manufacturingLocation: {
          region1ShortNames: form.manufacturing || [DEFAULTS.countryShortName],
        },
      },
      productNonAuditExtAttrReq: {
        california65WarningInfoReq: {
          california65WarningType: null,
          california65ChemicalNames: null,
        },
        cosmeticInfoReq: {},
      },

      // 物流
      productShipmentReq: {
        freightTemplateId: freightId,
        shipmentLimitSecond: shipmentLimit,
      },
      // ⚠️ 不能是空数组，必须填真实路由
      productWarehouseRouteReq: {
        targetRouteList: whIds.map((w) => ({ warehouseId: w, siteIdList: [siteId] })),
      },

      // 站点绑定
      productSemiManagedReq: {
        bindSiteIds: [siteId],
        semiLanguageStrategy: form.semiLanguageStrategy || DEFAULTS.semiLanguageStrategy,
      },

      personalizationSwitch: DEFAULTS.personalizationSwitch,
      productDraftId: form.draftId,
    });

    if (form.copyFromProductId) body.copyFromProductId = form.copyFromProductId;

    return body;
  }

  /**
   * 删除商品（软删除）
   * ⚠️ 不是立即消失：服务端标记 removeStatus=1，29 天后自动清除
   * ⚠️ 已在删除流程中的商品再删会报
   *    6000201 The current goods does not support the operation of deletion
   *    （这是正常的，说明已经标记过了）
   *
   * @param {number|number[]} productIds
   * @returns {{ ok:number, failed:Array }}
   */
  async removeProducts(productIds) {
    const ids = (Array.isArray(productIds) ? productIds : [productIds]).filter(Boolean);
    if (!ids.length) throw new Error('[product.removeProducts] 需要 productIds');

    let ok = 0;
    const failed = [];
    for (const productId of ids) {
      const r = await this.glo.api('POST', '/visage-agent-seller/product/remove', { productId });
      const code = String(r.code);
      if (code === '1000000') ok++;
      else failed.push({
        productId,
        code,
        msg: (r.body && r.body.errorMsg) || '',
        // 6000201 = 已在删除流程中，不算真失败
        alreadyRemoving: code === '6000201',
      });
    }
    return { ok, failed, total: ids.length };
  }

  /**
   * 列出待删除商品（removeStatus = 1）
   * 分页拉全后本地过滤，因为 pageQuery 不支持 removeStatus 过滤
   * @param {object} opts { maxPages }
   */
  async listRemoved(opts = {}) {
    const maxPages = opts.maxPages || 5;
    const out = [];
    for (let page = 1; page <= maxPages; page++) {
      const r = await this.glo
        .api('POST', '/visage-agent-seller/product/skc/pageQuery', { page, pageSize: 100 })
        .catch(() => null);
      const items = (r && r.body && r.body.result && r.body.result.pageItems) || [];
      out.push(...items.filter((x) => x.removeStatus === 1));
      if (items.length < 100) break;
    }
    return out.map((x) => ({
      productId: x.productId,
      productName: x.productName,
      removeStatus: x.removeStatus,
      autoDeleteRemainingDays: x.autoDeleteRemainingDays,
      removeStatusUpdatedAtTs: x.removeStatusUpdatedAtTs,
    }));
  }

  /**
   * 单独设置商品的视频（主图视频 / 详情视频）
   *
   * 对应接口：POST /visage-agent-seller/product/image/batchEditProductVideo
   *
   * @param {object} opts
   *   productId    商品 ID
   *   carouselVideos  主图视频 [{vid, coverUrl, videoUrl, width, height}]
   *   detailVideos    详情视频（同结构）
   *   businessType    默认 0
   */
  async setProductVideo(opts = {}) {
    const productId = Number(opts.productId);
    if (!productId) throw new Error('[product.setProductVideo] 需要 productId');

    const toReq = (list) => (list || []).map((v) => ({
      videoType: 1,
      vid: v.vid,
      coverUrl: v.coverUrl || v.firstImageUrl || '',
      videoUrl: v.videoUrl || v.destMaterialUrl || v.originalMaterialUrl || '',
      width: v.width || null,
      height: v.height || null,
    }));

    const req = { productId };
    if (opts.carouselVideos) req.productCarouseVideoReqList = toReq(opts.carouselVideos);
    if (opts.detailVideos) req.productDetailVideoReqList = toReq(opts.detailVideos);

    const r = await this.glo.api('POST', '/visage-agent-seller/product/image/batchEditProductVideo', {
      editProductVideoReqList: [req],
      businessType: opts.businessType || 0,
    });

    return {
      ok: String(r.code) === '1000000',
      code: r.code,
      msg: (r.body && r.body.errorMsg) || '',
      result: (r.body && r.body.result) || null,
    };
  }

  /** 正式创建 */
  async create(body) {
    const r = await this.glo.api('POST', '/visage-agent-seller/product/add', body);
    const ok = String(r.code) === '1000000';
    return {
      ok,
      code: r.code,
      msg: (r.body && r.body.errorMsg) || '',
      productId: ok ? ((r.body.result || {}).productId) : null,
      productSkcList: ok ? ((r.body.result || {}).productSkcList) : null,
    };
  }

  // ==================== 一步到位 ====================

  /**
   * 建草稿 → 组装 → 保存 → 创建
   * @param {object} form     同 buildBody
   * @param {object} opts
   *   template        已拉的模板（省一次请求）
   *   config          已拉的类目配置（省一次请求）
   *   env             店铺环境
   *   dryRun          true=只保存草稿不创建
   *   autoMaterialImg true=没给 materialImgUrl 时，自动从首张轮播图裁 1:1 并上传
   *   skipValidate    true=跳过类目配置校验
   */
  async createProduct(form, opts = {}) {
    const catId = leafCatId(form.catPath);

    // 并法拉模板 + 类目配置 + 店铺环境
    const [tmpl, cfg, env] = await Promise.all([
      opts.template || this.categoryTemplate(catId),
      opts.config || this.categoryConfig(catId, { bindSiteIds: [form.siteId] }).catch(() => ({})),
      opts.env || this.storeEnv(form.siteId),
    ]);

    const eng = this.engine(tmpl);

    // 尺码表：form.sizeTemplateSourceId 给的是「蓝本 id」时，先建一张新表再提交。
    // （product/add 只认 createSizeSpec 生成的新 id，直接提交蓝本会报 6000012）
    let trustedSizeIds = [];
    if (form.sizeTemplateSourceId) {
      // ⚠️ bridge 补丁：同一蓝本在短时间内（如一个批量任务）只需建一次表，
      //    否则 10 个同款不同色会建 10 张一模一样的尺码表，白烧请求额度
      const specKey = 'spec:' + catId + ':' + form.sizeTemplateSourceId;
      let made = cacheGet(specKey, this.ns);
      if (!made) {
        made = await this.createSizeSpecFromTemplate(catId, form.sizeTemplateSourceId, {
          name: form.sizeSpecName,
          antiContent: form.antiContent,
        });
        if (made && made.ok) cacheSet(specKey, made, 5 * 60 * 1000, this.ns);
      }
      if (!made.ok) {
        return {
          ok: false,
          stage: 'createSizeSpec',
          errors: [made.msg],
          msg: made.msg,
          available: made.available || null,
        };
      }
      form = Object.assign({}, form, {
        sizeTemplateIds: [made.businessId],
        showSizeTemplateIds: [made.businessId],
      });
      trustedSizeIds = [String(made.businessId)];
    }

    // 按类目配置校验必填
    if (!opts.skipValidate) {
      const errs = this.validateByConfig(form, cfg, eng);
      if (errs.length) {
        return { ok: false, stage: 'validate', errors: errs, msg: errs.join('; ') };
      }
    }

    // 尺码模板预检（6000012 的三种成因：类目不符 / 类型不符 / 尺码覆盖不到）
    let sizeTemplateCheck = null;
    const sizeTemplateIds = form.sizeTemplateIds || [];
    if (sizeTemplateIds.length && !opts.skipSizeTemplateCheck) {
      // 自动把所选 sizeVids 翻译成尺码值（M / L / one-size），用于比对模板的 sizes
      let sizeValues = form.sizeValues || [];
      if (!sizeValues.length && Array.isArray(form.sizeVids) && form.sizeVids.length) {
        const map = new Map((eng.sizeOptions() || []).map((o) => [Number(o.vid), o.value]));
        sizeValues = form.sizeVids.map((v) => map.get(Number(v))).filter(Boolean);
      }
      const chk = await this.validateSizeTemplate({
        catId,
        sizeTemplateIds,
        generalSizeType: form.generalSizeType,
        sizeValues,
        trustedIds: trustedSizeIds,
      }).catch((e) => ({ ok: true, _err: e.message }));
      sizeTemplateCheck = Object.assign({ ids: sizeTemplateIds }, chk);
      if (!chk.ok) {
        return {
          ok: false,
          stage: 'sizeTemplate',
          errors: [chk.reason],
          msg: chk.reason,
          suggestion: chk.suggestion,
          available: chk.available,
          sizeBad: chk.sizeBad || [],
        };
      }
    }

    // 素材图：没给就自动从首图裁方（UI 也是这么做的，只是它用 canvas）
    let materialImgUrl = form.materialImgUrl;
    if (!materialImgUrl && opts.autoMaterialImg !== false && (form.images || []).length) {
      const { Material } = require('./material');
      const mat = new Material(this.glo);
      const up = await mat.materialImageFrom(form.images[0]);
      materialImgUrl = up.imgUrl;
    }

    const draftId = await this.createDraft(form.catPath, form.siteId);

    const body = this.buildBody(
      Object.assign({}, form, { draftId, materialImgUrl }),
      tmpl, env, eng, cfg
    );

    const saved = await this.saveDraft(body);
    if (!saved.ok) {
      return { ok: false, stage: 'saveDraft', draftId, code: saved.code, msg: saved.msg, body };
    }
    if (opts.dryRun) {
      return { ok: true, stage: 'saveDraft', draftId, materialImgUrl, config: cfg, body, sizeTemplateCheck };
    }

    const created = await this.create(body);
    return Object.assign({ stage: 'create', draftId, materialImgUrl, config: cfg, body, sizeTemplateCheck }, created);
  }
}

/**
 * 清空进程内的产品缓存（sites / regions / 仓库 / 类目模板）
 * ⚠️ bridge 补丁：memCache 是按进程共享的，没有账号维度，
 *    多账号切换会读到上一个账号的站点/仓库。登录成功后调用本函数即可。
 */
function clearCache() {
  memCache.clear();
}

module.exports = {
  Product,
  clearCache,
  SHIPMENT_LIMIT,
  UNIT,
  DEFAULTS,
  CONFIG_ITEM,
  CONFIG_ITEM_ALL,
  EN_NAME_MIN,
  SKU_CLASSIFICATION,
  SENSITIVE_TYPES,
  DECORATION_KEY,
  GENERAL_SIZE_TYPE,
  SIZE_SPEC_DEFAULTS,
  ANTI_CONTENT_DEFAULT,
  flattenCatPath,
  leafCatId,
};
