/**
 * 客服「自助工具 → 商品下架」模块
 * ================================
 * 链路（与 UI 对应）：
 *   商品列表 → 右上角客服 → 联系官方客服 → 自助工具 → 商品下架
 *   → 发商品（填 SKC + 下架原因）→ 申请下架
 *
 * ★ 关键认知：「申请下架」不是独立业务接口，
 *   而是往客服会话发一条 contentType=7 的聊天消息，
 *   真正的下架动作由客服机器人（服务端）执行。
 *
 * 四步：
 *   1. POST /bg/cute/api/merchantService/chat/queryMessage                    只读  读会话（找工具卡片）
 *   2. POST /api/kiana/.../chat/reception/queryProductSkcBasicInfo           只读  查商品名/图
 *   3. POST /api/kiana/.../chat/reception/queryPreInterceptForToolSubmit     只读  拦截检查
 *   4. POST /bg/cute/api/merchantService/chat/sendMessage                    写    真正提交
 *
 * 用法：
 *   const { KefuOffline } = require('./kefu_offline');
 *   const k = new KefuOffline();
 *   await k.init();
 *
 *   await k.messages();               // 列会话消息
 *   await k.toolCard();               // 取最新工具卡片（含下架原因枚举）
 *   await k.queryProduct('SKC', 49589818098);   // 查商品
 *   await k.preIntercept(49589818098);          // 拦截检查
 *   await k.submit({ skcId: 49589818098, reason: 2001 });   // ★ 真实提交
 */
const path = require('path');

const CHAT = '/bg/cute/api/merchantService/chat/';
const REC = '/api/kiana/marvel-supplier/api/ultraman/chat/reception/';

/** contentType 枚举 */
const CT = {
  Text: 1, Picture: 2, Video: 3, Card: 4, Problem: 5,
  ProductInfoQuery: 6,   // 自助工具卡片
  ToolSubmit: 7,         // 工具提交
  UserFeedback: 8, TransferHuman: 9, QuickTool: 10,
  RobotReply: 12,        // 机器人终态回执（HTML）
  TimeTag: 100, RobotFaq: 101, RobotGuess: 102, RobotReply2: 103,
};

/** dataType 枚举 */
const DT = { SKC: 1, SKU: 2, PurchaseOrder: 3, GoodsSkc: 4, SPU: 5, Stock: 6 };

/** 需要填 detailedReason 的下架原因（「其他」）*/
const REASON_NEED_INPUT = 2008;

class KefuOffline {
  constructor(opts = {}) {
    this.opts = opts;
    this.glo = null;
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

  /** 客服族请求（客服面板挂在主容器，Referer 用站点根）*/
  async _call(p, body) {
    return await this.glo.api('POST', p, body === undefined ? {} : body, {
      referer: '/',
    }).catch(e => ({ ok: false, err: e.message }));
  }

  // ==================== 0. 自助工具列表 ====================
  /**
   * 自助工具清单（4 个）
   * POST /api/kiana/marvel-supplier/api/ultraman/chat/reception/querySelfServiceTools
   *
   * 实测返回：
   *   商品发布到站点           toolId=2406230000016 dataIdType=1(SKC) problemType=26
   *   商品下架                toolId=2406230000031 dataIdType=1(SKC) problemType=27
   *   价格刷新                toolId=2406280000031 dataIdType=2(SKU) problemType=42
   *   商品站点/信息异常查询     toolId=2508210000009 dataIdType=5(SPU) problemType=-1
   */
  async selfServiceTools(opts = {}) {
    // 实测该接口偶尔返回空 list（尤其刚调完 connectSession 之后），加重试
    const retries = opts.retries !== undefined ? opts.retries : 3;
    let last = { ok: false, err: '未知错误', list: [] };

    for (let i = 0; i <= retries; i++) {
      const r = await this._call(REC + 'querySelfServiceTools', {});
      if (!r || r.err) {
        last = { ok: false, err: (r && r.err) || '请求失败', list: [] };
      } else {
        const res = r.body && r.body.result !== undefined ? r.body.result : r.body;
        const list = (res && res.list) || [];
        if (list.length) {
          return {
            ok: true,
            list: list.map(t => ({
              toolName: t.toolName,
              toolId: t.toolId,
              dataIdType: t.dataIdType,
              toolType: t.toolType,
              ableUse: t.ableUse,
              onlyFullyHosted: t.onlyFullyHosted,
              feedbackProblemType: t.feedbackProblemType,
              feedbackRemark: t.feedbackRemark,
              optionTitle: t.toolSubmitOptionTitle || null,
              optionItemList: t.toolSubmitOptionItemList || null,
              popupVO: t.toolSubmitPopupVO || null,
              rateLimitMs: t.toolRateLimitVO ? t.toolRateLimitVO.intervalMs : null,
              successTip: t.partSuccessBackupTipMsg || null,
              failTip: t.imMsgLengthLimitTipMsg || null,
              raw: t,
            })),
          };
        }
        last = { ok: true, err: '工具列表为空', list: [] };
      }
      if (i < retries) await new Promise((s) => setTimeout(s, 2000));
    }
    return last;
  }

  /** 按名字取工具 */
  async toolByName(name) {
    const r = await this.selfServiceTools();
    if (!r.ok) return null;
    return r.list.find(t => t.toolName === name || t.toolName.includes(name)) || null;
  }

  // ==================== 1. 会话消息 ====================
  /**
   * 读客服会话消息
   * @param {object} opts { msgId, direction, limit }
   */
  async messages(opts = {}) {
    const body = {
      msgId: opts.msgId !== undefined ? opts.msgId : null,
      direction: opts.direction !== undefined ? opts.direction : 2,
      limit: Math.min(opts.limit || 50, 50),   // 服务端上限 50，超了返回 result=null
    };

    let r = null;
    for (let i = 0; i < 3; i++) {
      r = await this._call(CHAT + 'queryMessage', body);
      const res = r && r.body ? r.body.result : null;
      if (res && Array.isArray(res.messageList)) break;
      await new Promise(s => setTimeout(s, 600));
    }

    if (!r) return { ok: false, err: '无响应', list: [] };
    if (r.err) return { ok: false, err: r.err, list: [] };

    const res = r.body ? r.body.result : null;
    if (!res || !Array.isArray(res.messageList)) {
      return {
        ok: false,
        list: [],
        err: 'result 为空（可能尚未建立客服会话，需先在页面点开「联系官方客服」）',
        code: r.code,
        raw: r.body,
      };
    }
    return { ok: true, list: res.messageList, total: res.messageList.length, raw: res };
  }

  /**
   * 取自助工具卡片（contentType=6）
   *
   * ⚠️ 卡片会被后续聊天消息挤出「最近 50 条」窗口（queryMessage 的 limit 上限就是 50），
   *    所以这里会自动向前翻页（direction=1）直到找到为止。
   *
   * 实测（2026-11 上游验证）：某账号最新 50 条 0 张卡片，往前翻 1 页能翻到 6 张；
   * 翻 1 页约覆盖 40 天历史。
   *
   * @param {object} opts
   *   toolId   只找这个工具的卡片（多个工具混在一起时用）
   *   maxPages 最多往前翻几页，默认 10（每页 50 条）
   */
  async toolCard(opts = {}) {
    const wantToolId =
      opts.toolId !== undefined && opts.toolId !== null ? String(opts.toolId) : null;
    const maxPages = opts.maxPages || 10;

    let msgId = null;
    const seen = new Set();

    for (let page = 0; page < maxPages; page++) {
      const body = { msgId, direction: page === 0 ? 2 : 1, limit: 50 };
      const r = await this._call(CHAT + 'queryMessage', body);
      if (!r || r.err) return { ok: false, err: (r && r.err) || '读取会话失败' };

      const res = r.body ? r.body.result : null;
      if (!res || !Array.isArray(res.messageList)) {
        return {
          ok: false,
          err: 'result 为空（可能尚未建立客服会话，需先在页面点开「联系官方客服」）',
          code: r.code,
        };
      }

      const list = res.messageList;
      if (!list.length) break;

      // 从新到旧扫这一页
      for (const msg of list) {
        if (msg.contentType !== CT.ProductInfoQuery) continue;
        let card;
        try { card = JSON.parse(msg.content); } catch (e) { continue; }
        if (!card || !card.toolId) continue;
        if (wantToolId && String(card.toolId) !== wantToolId) continue;

        return {
          ok: true,
          msgId: msg.msgId,
          toolId: card.toolId,
          dataType: card.dataType,
          dataTypeList: card.dataTypeList,
          optionTitle: card.optionTitle,
          optionItemList: card.optionItemList || [],
          popUpItem: card.popUpItem || null,
          page,
          // 卡片消息时间：提交前用它判断「上下文是否还新鲜」
          timestamp: msg.timestamp,
          card,
        };
      }

      // 找下一条翻页锚点（本页最早的 msgId）
      const oldest = list.reduce(
        (min, x) => (Number(x.msgId) < Number(min.msgId) ? x : min),
        list[0],
      );
      if (seen.has(String(oldest.msgId))) break;
      seen.add(String(oldest.msgId));
      msgId = oldest.msgId;

      if (list.length < 50) break; // 到底了
    }

    return {
      ok: false,
      err: wantToolId
        ? `未找到 toolId=${wantToolId} 的工具卡片（已往前翻 ${maxPages} 页）。请在页面点开该自助工具，让机器人推送卡片`
        : `未找到自助工具卡片（contentType=6，已往前翻 ${maxPages} 页）。请在页面点开「联系官方客服 → 自助工具」`,
    };
  }

  /**
   * 列出每个工具的最新卡片（按 toolId 去重，每个取最新一张）
   * 提交前想按工具挑 parentMsgId、或做卡片缓存时用
   */
  async allToolCards(opts = {}) {
    const maxPages = opts.maxPages || 10;
    const found = {};
    let msgId = null;
    const seen = new Set();

    for (let page = 0; page < maxPages; page++) {
      const body = { msgId, direction: page === 0 ? 2 : 1, limit: 50 };
      const r = await this._call(CHAT + 'queryMessage', body);
      if (!r || r.err) break;
      const res = r.body ? r.body.result : null;
      if (!res || !Array.isArray(res.messageList)) break;

      const list = res.messageList;
      if (!list.length) break;

      for (const msg of list) {
        if (msg.contentType !== CT.ProductInfoQuery) continue;
        let card;
        try { card = JSON.parse(msg.content); } catch (e) { continue; }
        if (!card || !card.toolId) continue;
        const key = String(card.toolId);
        if (!found[key]) {
          found[key] = {
            msgId: msg.msgId,
            toolId: card.toolId,
            dataType: card.dataType,
            optionTitle: card.optionTitle || null,
            optionItemList: card.optionItemList || [],
            popUpItem: card.popUpItem || null,
            card,
          };
        }
      }

      const oldest = list.reduce(
        (min, x) => (Number(x.msgId) < Number(min.msgId) ? x : min),
        list[0],
      );
      if (seen.has(String(oldest.msgId))) break;
      seen.add(String(oldest.msgId));
      msgId = oldest.msgId;
      if (list.length < 50) break;
    }

    return Object.values(found);
  }

  // ==================== 2. 查商品 ====================
  /**
   * 查商品基本信息
   * @param {'SKC'|'SKU'|'SPU'} type
   * @param {number|string} id
   * @param {number} toolId 默认取最新卡片
   *
   * 实测三个接口：
   *   SKC → queryProductSkcBasicInfo          {productSkcId, toolId}
   *   SKU → queryProductSkuAndBuyerBasicInfo  {productSkuId, toolId}
   *   SPU → queryProductAndAlgoGroupInfo      {productId, toolId}   ← 注意参数叫 productId
   */
  async queryProduct(type, id, toolId) {
    let tid = toolId;
    if (!tid) {
      const c = await this.toolCard();
      if (!c.ok) return { ok: false, err: c.err };
      tid = c.toolId;
    }

    const T = String(type).toUpperCase();
    let p, body;
    if (T === 'SPU' || type === DT.SPU || type === 5) {
      p = REC + 'queryProductAndAlgoGroupInfo';
      body = { productId: Number(id), toolId: Number(tid) };
    } else if (T === 'SKU' || type === DT.SKU || type === 2) {
      p = REC + 'queryProductSkuAndBuyerBasicInfo';
      body = { productSkuId: Number(id), toolId: Number(tid) };
    } else {
      p = REC + 'queryProductSkcBasicInfo';
      body = { productSkcId: Number(id), toolId: Number(tid) };
    }

    const r = await this._call(p, body);
    if (!r || r.err) return { ok: false, err: r && r.err };
    const res = r.body && r.body.result !== undefined ? r.body.result : r.body;
    // 网关错误（如 {"error_code":40001}）没有 success 字段，不能只看 success
    const code = r.code;
    const success = String(code) === '1000000' || (r.body && r.body.success === true);
    return {
      ok: success,
      code,
      msg: (r.body && (r.body.errorMsg || r.body.error_msg)) || '',
      productName: res && res.productName,
      productPicture: res && res.productPicture,
      productSkcId: res && res.productSkcId,
      productId: res && res.productId,
      algoProductId: res && res.algoProductId,
      isSemi: res && res.isSemi,
      isCloth: res && res.isCloth,
      hasBondedOrAbroadStock: res && res.hasBondedOrAbroadStock,
      raw: res,
    };
  }

  // ==================== 3. 拦截检查 ====================
  /**
   * 提交前拦截检查
   * @returns { interceptCode, interceptMsg, ok }  interceptCode=0 才放行
   */
  async preIntercept(dataId, toolId) {
    let tid = toolId;
    if (!tid) {
      const c = await this.toolCard();
      if (!c.ok) return { ok: false, err: c.err };
      tid = c.toolId;
    }
    const r = await this._call(REC + 'queryPreInterceptForToolSubmit', {
      toolId: Number(tid), dataId: String(dataId),
    });
    if (!r || r.err) return { ok: false, err: r && r.err };
    const res = r.body && r.body.result !== undefined ? r.body.result : r.body;
    return {
      ok: true,
      interceptCode: res && res.interceptCode,
      interceptMsg: res && res.interceptMsg,
      passed: res && res.interceptCode === 0,
      raw: res,
    };
  }

  // ==================== 4. 提交（写）====================
  /**
   * 组装 contentType=7 的 content（字段顺序与线上一致）
   */
  buildContent(prod, dataType, id, toolId, card, reasonCode, detail) {
    const opt = (card.optionItemList || []).find(o => o.code === Number(reasonCode));
    return JSON.stringify({
      name: prod && prod.productName,
      img: prod && prod.productPicture,
      dataId: String(id),
      dataType: dataType,
      toolId: Number(toolId),
      count: null,
      optionTitle: card.optionTitle,
      optionInfo: opt
        ? {
          code: opt.code,
          desc: opt.desc,
          detailedReason: opt.code === REASON_NEED_INPUT ? (detail || '') : null,
        }
        : null,
    });
  }

  /**
   * ★ 真实提交下架申请（不可逆）
   * @param {object} opts
   *   skcId / skuId     商品 ID（二选一）
   *   reason            下架原因 code（2000~2008）
   *   detail            仅 2008 需要
   *   toolId / parentMsgId  可覆盖
   *   dryRun            只组装不发送
   */
  async submit(opts = {}) {
    // 0. 选工具（支持按 toolName / toolId / 默认下架卡片）
    let spec = null;   // { toolId, optionTitle, optionItemList }
    if (opts.toolName || opts.toolId) {
      const all = await this.selfServiceTools();
      if (!all.ok) return { ok: false, stage: 'selfServiceTools', err: all.err };
      spec = opts.toolName
        ? all.list.find(t => t.toolName === opts.toolName || t.toolName.includes(opts.toolName))
        : all.list.find(t => String(t.toolId) === String(opts.toolId));
      if (!spec) return { ok: false, stage: 'tool', err: '未找到该工具' };
    }

    // 1. 工具卡片（主要为了 msgId）
    //    按目标 toolId 过滤 + 自动翻页：卡片常被后续消息挤出最近 50 条窗口
    const c = await this.toolCard({ toolId: spec ? spec.toolId : opts.toolId });
    if (!c.ok) return { ok: false, stage: 'toolCard', err: c.err };

    const toolId = spec ? spec.toolId : (opts.toolId || c.toolId);
    const parentMsgId = opts.parentMsgId !== undefined ? Number(opts.parentMsgId) : c.msgId;

    // 选项来源：优先工具定义，其次卡片
    const optTitle = spec ? spec.optionTitle : c.optionTitle;
    const optList = spec ? spec.optionItemList : c.optionItemList;
    const cardLike = { optionTitle: optTitle, optionItemList: optList };

    // 数据类型：SPU > SKU > SKC
    let dataType, id, typeName;
    if (opts.spuId !== undefined && opts.spuId !== null) {
      dataType = DT.SPU; id = opts.spuId; typeName = 'SPU';
    } else if (opts.skuId !== undefined && opts.skuId !== null) {
      dataType = DT.SKU; id = opts.skuId; typeName = 'SKU';
    } else {
      dataType = DT.SKC; id = opts.skcId; typeName = 'SKC';
    }
    if (id === undefined || id === null) return { ok: false, stage: 'args', err: '需要 skcId / skuId / spuId' };

    // 2. 查商品（服务端硬校验 name/img 非空）
    const prod = opts._product || await this.queryProduct(dataType, id, toolId);
    if (!prod.ok) return { ok: false, stage: 'queryProduct', err: prod.msg || '查商品失败', raw: prod };
    if (!prod.productName || !prod.productPicture) {
      return {
        ok: false, stage: 'queryProduct',
        err: '商品信息不完整（name/img 为空），服务端会返回 40002 Message content is incomplete',
      };
    }

    // 3. 拦截检查（★ 有状态：同一商品只有第一次返回 interceptCode=0，
    //    重复调会返回 1「该商品上次咨询结果正在返回中，请勿重复咨询」）
    //    所以允许外部传入已算好的结果，避免重复消费。
    let ic = opts.intercept;
    if (!ic) {
      ic = await this.preIntercept(id, toolId);
      if (!ic.ok) return { ok: false, stage: 'preIntercept', err: ic.err };
    }
    if (!ic.passed) {
      const hint = typeof ic.interceptMsg === 'object' && ic.interceptMsg
        ? [ic.interceptMsg.hint, ic.interceptMsg.subHint].filter(Boolean).join(' / ')
        : String(ic.interceptMsg || ic.interceptCode);
      return { ok: false, stage: 'preIntercept', err: '被拦截: ' + hint, raw: ic };
    }

    // 4. 组装
    const content = this.buildContent(prod, dataType, id, toolId, cardLike,
      opts.reason !== undefined ? opts.reason : 2001, opts.detail);
    const body = { parentMsgId, contentType: CT.ToolSubmit, content };

    // ★ 安全默认：只有显式传 dryRun:false 才真发，其余一律预览
    const dryRun = opts.dryRun !== false;
    if (dryRun) {
      return {
        ok: true, dryRun: true, preview: body, product: prod,
        tool: { toolId, toolName: spec ? spec.toolName : '商品下架(卡片)' },
        parentMsgId,
      };
    }

    // 5. 发送
    const r = await this._call(CHAT + 'sendMessage', body);
    if (!r || r.err) return { ok: false, stage: 'sendMessage', err: r && r.err };
    const res = r.body && r.body.result !== undefined ? r.body.result : r.body;
    const success = r.body ? r.body.success !== false : false;

    return {
      ok: success,
      code: r.code,
      msg: (r.body && (r.body.errorMsg || r.body.error_msg)) || '',
      msgId: res && (res.msgId || res.messageId),
      timestamp: res && res.timestamp,
      sent: body,
      product: prod,
    };
  }

  /**
   * ★ 建立 / 恢复客服会话
   *
   * 会话闲置 2~3 小时会变成 off：此时发文本只有自己的回显，发 QuickTool 也拿不到卡片。
   * 调用本接口重建会话（拿到 receptionId），机器人恢复应答。
   */
  async connectSession(opts = {}) {
    const body = {
      consultantBizAccountType:
        opts.consultantBizAccountType !== undefined ? opts.consultantBizAccountType : 1,
      platSource: opts.platSource || 1001,
      sendRobotHotFaqMsg: opts.sendRobotHotFaqMsg !== false,
    };
    const r = await this._call(REC + 'responseChatWithCustomerSupportV2', body);
    const res = (r.body && r.body.result) || {};
    return {
      ok: String(r.code) === '1000000',
      code: r.code,
      receptionId: res.receptionId,
      resultCode: res.resultCode,
      waitCount: res.waitCount,
      raw: r.body,
    };
  }

  /**
   * ★ 唤醒工具卡片（正确方式：QuickTool）
   *
   * 两步缺一不可：
   *   1. connectSession() 确保会话在线（off 时机器人不响应任何消息）
   *   2. sendMessage contentType=10 + { desc, id: toolId, scene: 1 }
   *
   * ⚠️ 发纯文本（contentType=1）不会唤醒卡片 —— 上游实测 7 种说法全部无响应。
   *
   * @param {string} toolName 例如 '商品发布到站点' / '商品下架' / '价格刷新'
   * @param {object} opts { toolId, timeoutMs, autoConnect }
   * @returns { ok, msgId, toolId, content, waitedMs }  msgId 是卡片 msgId（可直接当 parentMsgId）
   */
  async wakeTool(toolName, opts = {}) {
    const timeoutMs = opts.timeoutMs || 30000;
    if (!toolName) return { ok: false, stage: 'args', err: '需要 toolName' };

    let toolId = opts.toolId;
    if (!toolId) {
      const spec = await this.toolByName(toolName);
      if (spec && spec.toolId) toolId = spec.toolId;
    }
    if (!toolId) return { ok: false, stage: 'tool', err: '未找到该工具: ' + toolName };

    // 1. 确保会话在线（off 时发什么都不会有卡片）
    let connected = null;
    if (opts.autoConnect !== false) {
      connected = await this.connectSession({ sendRobotHotFaqMsg: opts.sendRobotHotFaqMsg !== false });
      await new Promise((s) => setTimeout(s, 800));
    }

    // 2. 取最新 msgId 作为 parentMsgId
    const m = await this.messages({ limit: 50 });
    if (!m.ok) return { ok: false, err: m.err };
    const list = (m.list || []).slice().sort((a, b) => a.timestamp - b.timestamp);
    if (!list.length) return { ok: false, err: '会话为空' };
    const lastId = list[list.length - 1].msgId;

    // 3. 发 QuickTool（contentType=10，content.id 是 toolId）
    const r = await this._call(CHAT + 'sendMessage', {
      parentMsgId: lastId,
      contentType: CT.QuickTool,
      content: JSON.stringify({ desc: toolName, id: toolId, scene: 1 }),
    });
    if (!r || r.err) return { ok: false, stage: 'send', err: (r && r.err) || '发送失败' };
    if (r.body && r.body.success === false) {
      return { ok: false, stage: 'send', err: r.body.errorMsg || '发送失败' };
    }

    // 4. 等机器人推卡片（每 2 秒看一次）
    const t0 = Date.now();
    while (Date.now() - t0 < timeoutMs) {
      await new Promise((s) => setTimeout(s, 2000));
      const mm = await this.messages({ limit: 50 });
      if (!mm.ok) continue;
      const fresh = (mm.list || []).filter(
        (x) => Number(x.msgId) > Number(lastId) && x.contentType === CT.ProductInfoQuery,
      );
      for (const c of fresh.slice().reverse()) {
        let card;
        try { card = JSON.parse(c.content); } catch (e) { continue; }
        if (!card || !card.toolId) continue;
        return {
          ok: true,
          msgId: c.msgId,
          toolId: card.toolId,
          content: card.content,
          optionTitle: card.optionTitle,
          optionItemList: card.optionItemList || [],
          card,
          receptionId: connected ? connected.receptionId : undefined,
          waitedMs: Date.now() - t0,
        };
      }
    }
    return {
      ok: false,
      stage: 'wait',
      err: `QuickTool 已发送但 ${timeoutMs}ms 内未收到卡片（会话可能仍不在线，或该工具当前不可用）`,
    };
  }

  /**
   * 确保有可用的工具卡片：先找现成的（会翻页），找不到就发文本唤醒
   *
   * @param {string} toolName
   * @param {object} opts { toolId, maxAgeMs 卡片最大年龄（0 = 不限制）, timeoutMs, maxPages }
   */
  async ensureToolCard(toolName, opts = {}) {
    let toolId = opts.toolId || null;
    if (!toolId && toolName) {
      const spec = await this.toolByName(toolName);
      if (spec && spec.toolId) toolId = spec.toolId;
    }

    const existing = await this.toolCard({ toolId, maxPages: opts.maxPages });
    if (existing.ok) {
      // 没有时间戳 = 无法判断新鲜度，按过期处理（宁可多唤醒一次，也别让提交被降级）
      const fresh = opts.maxAgeMs && existing.timestamp
        && Date.now() - Number(existing.timestamp) < opts.maxAgeMs;
      if (!opts.maxAgeMs || fresh) return { ...existing, source: 'find' };
    }

    if (!toolName) return existing;
    const woken = await this.wakeTool(toolName, opts);
    return woken.ok ? { ...woken, source: 'wake' } : woken;
  }

  /**
   * 查提交后的机器人回执
   * contentType=12 → 终态（HTML，含"该商品已下架"等）
   * contentType=1  → 「加速等待序列」是降级信号；「系统忙碌」是执行失败
   */
  async reply(sinceMsgId) {
    const m = await this.messages({ limit: 50 });
    if (!m.ok) return { ok: false, err: m.err };
    const after = m.list.filter(x => !sinceMsgId || Number(x.msgId) > Number(sinceMsgId));
    const robot = after.filter(x => x.contentType === CT.RobotReply || x.contentType === CT.Text);
    return {
      ok: true,
      replies: robot.map(x => ({
        contentType: x.contentType,
        msgId: x.msgId,
        content: String(x.content || '').replace(/<[^>]+>/g, '').slice(0, 200),
      })),
    };
  }
}

module.exports = { KefuOffline, CT, DT, REASON_NEED_INPUT, CHAT, REC };

// ==================== CLI ====================
if (require.main === module) {
  (async () => {
    const args = {};
    for (const a of process.argv.slice(2)) {
      const m = /^--([a-z-]+)(?:=(.*))?$/.exec(a);
      if (m) args[m[1]] = m[2] === undefined ? true : m[2];
    }

    const k = new KefuOffline();
    await k.init();
    console.log('已登录 | mall:', k.glo.mallName, k.glo.mallId, '\n');

    // 0. 工具清单
    const all = await k.selfServiceTools();
    if (all.ok) {
      console.log(`自助工具 ${all.list.length} 个:`);
      for (const t of all.list) {
        const dtn = { 1: 'SKC', 2: 'SKU', 5: 'SPU' }[t.dataIdType] || t.dataIdType;
        console.log(`  ${t.toolName.padEnd(18)} toolId=${t.toolId}  数据类型=${dtn}` +
          (t.optionItemList ? `  选项=${t.optionItemList.length}个(${t.optionTitle})` : '  (无选项)') +
          (t.rateLimitMs ? `  限流=${t.rateLimitMs}ms` : ''));
      }
      console.log('');
    }

    if (args.tools) return;

    // 1. 会话 + 卡片
    const m = await k.messages({ limit: 50 });
    if (!m.ok) { console.log('读会话失败:', m.err); return; }
    const dist = {};
    for (const x of m.list) dist[x.contentType] = (dist[x.contentType] || 0) + 1;
    console.log(`会话消息 ${m.total} 条，contentType 分布: ${JSON.stringify(dist)}`);

    const c = await k.toolCard();
    if (!c.ok) { console.log('\n未找到工具卡片:', c.err); return; }
    console.log(`\n工具卡片 msgId=${c.msgId} toolId=${c.toolId} dataType=${c.dataType}`);
    console.log('  popUpItem:', JSON.stringify(c.popUpItem));
    console.log(`  ${c.optionTitle}:`);
    for (const o of c.optionItemList) {
      console.log(`    ${o.code}  ${o.desc}${o.needInput ? '  (需填具体原因)' : ''}`);
    }

    const id = args.skc || args.sku || args.spu;
    if (!id) {
      console.log('\n（只读。加 --skc=<ID> / --sku=<ID> / --spu=<ID> 查商品+拦截；加 --submit 真实提交）');
      return;
    }

    const type = args.spu ? 'SPU' : (args.sku ? 'SKU' : 'SKC');
    const toolName = args.tool;   // 如 --tool=商品发布到站点

    // 工具对应的 toolId / 选项
    let spec = null;
    if (toolName && all.ok) {
      spec = all.list.find(t => t.toolName === toolName || t.toolName.includes(toolName));
    }

    console.log(`\n[2] 查商品 ${type} id=${id}` + (spec ? `  (工具: ${spec.toolName})` : ''));
    const p = await k.queryProduct(type, id, spec ? spec.toolId : undefined);
    console.log('  ok=' + p.ok, 'name=' + String(p.productName).slice(0, 40));
    console.log('  img=' + String(p.productPicture).slice(0, 70));

    // 注意：拦截检查有状态，只调一次，结果传给 submit 复用
    console.log(`\n[3] 拦截检查`);
    const ic = await k.preIntercept(id, spec ? spec.toolId : undefined);
    const hint = typeof ic.interceptMsg === 'object' && ic.interceptMsg
      ? [ic.interceptMsg.hint, ic.interceptMsg.subHint].filter(Boolean).join(' / ')
      : String(ic.interceptMsg || '');
    console.log('  interceptCode=' + ic.interceptCode, 'passed=' + ic.passed, hint);

    const res = await k.submit({
      skcId: args.skc ? args.skc : undefined,
      skuId: args.sku ? args.sku : undefined,
      spuId: args.spu ? args.spu : undefined,
      toolName: toolName || undefined,
      reason: args.reason || 2001,
      detail: args.detail,
      intercept: ic,          // ← 复用上面那次检查，不再重复调
      dryRun: !args.submit,
      _product: p,
    });
    if (res.dryRun) {
      console.log('\n[4] 未加 --submit，预览将发送的 body:');
      console.log('  POST ' + CHAT + 'sendMessage');
      console.log('  ' + JSON.stringify(res.preview));
    } else {
      console.log('\n[4] 提交结果:',
        'ok=' + res.ok,
        'stage=' + (res.stage || '-'),
        'code=' + res.code,
        res.msg || res.err || '');
      if (res.msgId) console.log('     msgId=' + res.msgId);
    }
  })().catch(e => { console.error('ERR:', e.message); process.exit(1); });
}
