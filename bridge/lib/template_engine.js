/**
 * 类目模板依赖引擎
 * =================
 * 把 `category/template/query` 返回的模板变成可驱动表单的状态机。
 *
 * 三种依赖：
 *   1. showCondition                     —— 条件显示（面料纹理=光面 → 面料克重必填）
 *   2. templatePropertyValueParentList   —— 值依赖（父值不同，可选项不同）
 *   3. values[].parentVids               —— 值级限制（服务端最后一道校验）
 *
 * 用法：
 *   const { TemplateEngine } = require('./template_engine');
 *   const eng = new TemplateEngine(tmpl);
 *
 *   // 渲染表单
 *   for (const p of eng.visibleProps()) {
 *     const options = eng.optionsFor(p);     // 下拉选项
 *   }
 *
 *   // 用户选择
 *   let sel = {};
 *   sel = eng.onSelect(sel, 6926, 161198);   // 面料纹理 = 光面
 *   // → 面料克重自动出现且必填
 *
 *   // 数值型
 *   sel['num_6930'] = '100';
 *
 *   // 校验 + 组装
 *   const missing = eng.validate(sel);
 *   const reqs = eng.buildPropertyReqs(sel);
 *
 * selected 的 key：
 *   下拉型 → refPid            例: { 6926: 161198 }
 *   数值型 → 'num_' + refPid   例: { num_6930: '100' }
 *
 * 为什么用 refPid 而不是 pid：
 *   同一个 pid 可能对应多个属性（pid=2054 既可以是「面料纹理1」也可以是「面料纹理2」），
 *   refPid 才是唯一标识。
 */

/** 属性值类型 */
const VALUE_TYPE = {
  SELECT: 0,   // 下拉选择
  NUMBER: 1,   // 数值输入
};

/** 控件类型 */
const CONTROL_TYPE = {
  NUMBER_ONLY: 0,      // 纯数值（面料克重）
  SELECT: 1,           // 纯下拉
  COLOR: 3,            // 颜色（规格）
  SELECT_WITH_NUMBER: 16,  // 下拉 + 数值（成分比例）
};

class TemplateEngine {
  /**
   * @param {object} template  category/template/query 的 result
   */
  constructor(template) {
    this.raw = template || {};
    this.templateId = this.raw.templateId;
    this.props = this.raw.properties || [];

    /** refPid → 属性 */
    this.byRefPid = {};
    /** templatePid → 属性 */
    this.byTplPid = {};
    /** pid → 属性数组（一对多）*/
    this.byPid = {};

    for (const p of this.props) {
      this.byRefPid[p.refPid] = p;
      this.byTplPid[p.templatePid] = p;
      (this.byPid[p.pid] = this.byPid[p.pid] || []).push(p);
    }
  }

  // ==================== 可见性 ====================

  /**
   * 是否是「规格」而非「属性」
   * 颜色 / 尺码这类带 parentSpecId 的字段走 productSpecPropertyReqs，
   * 不能放进 productPropertyReqs（否则服务端会把颜色当成属性校验）
   */
  isSpec(prop) {
    return !!prop.parentSpecId;
  }

  /** 只看属性（排除规格）*/
  get attrProps() {
    return this.props.filter((p) => !this.isSpec(p));
  }

  /** 只看规格*/
  get specProps() {
    return this.props.filter((p) => this.isSpec(p));
  }

  /**
   * 是否需要额外填数值
   * controlType=16 是「下拉 + 数值」组合（如成分比例：材质 + 百分比）
   */
  needsNumberInput(prop) {
    return prop.controlType === CONTROL_TYPE.SELECT_WITH_NUMBER;
  }

  /** 数值部分的 key */
  inputKey(prop) {
    return 'input_' + prop.refPid;
  }

  /**
   * 该属性在当前选择下是否显示
   * showCondition: [{ parentRefPid, parentVids:[...] }]，多个条件是 AND
   */
  isVisible(prop, selected = {}) {
    const conds = prop.showCondition;
    if (!conds || !conds.length) return true;
    return conds.every((c) => {
      const v = selected[c.parentRefPid];
      return v !== undefined && v !== null && c.parentVids.includes(v);
    });
  }

  /** 当前应显示的属性列表 */
  visibleProps(selected = {}) {
    return this.props.filter((p) => this.isVisible(p, selected));
  }

  /**
   * 当前应显示且必填的属性（不含规格）
   * 注意：数值型属性没有 values，不能按「有选项」过滤
   * controlType=16（下拉+数值）需要既有选项又要填值
   */
  requiredProps(selected = {}) {
    return this.visibleProps(selected).filter((p) => {
      if (this.isSpec(p) || !p.required) return false;
      // 纯数值型：无选项要求
      if (p.propertyValueType === VALUE_TYPE.NUMBER) return true;
      // 下拉型（含 controlType=16）：必须有可选项才算可填
      return this.optionsFor(p, selected).length > 0;
    });
  }

  // ==================== 选项 ====================

  /**
   * 某属性在当前选择下的可选值
   * 依次应用：templatePropertyValueParentList 分组 → values[].parentVids
   */
  optionsFor(prop, selected = {}) {
    // 数值型没有选项
    if (prop.propertyValueType === VALUE_TYPE.NUMBER) return [];

    let list = prop.values || [];
    const parent = prop.parentTemplatePid ? this.byTplPid[prop.parentTemplatePid] : null;
    const parentVid = parent ? selected[parent.refPid] : undefined;

    // 值依赖分组
    const groups = prop.templatePropertyValueParentList;
    if (groups && groups.length) {
      const g = groups.find((x) => (x.parentVidList || []).includes(parentVid)) || groups[0];
      const allowed = new Set(g ? g.vidList : []);
      list = list.filter((v) => allowed.has(v.vid));
    }

    // 值级 parentVids
    if (parent) {
      list = list.filter((v) => !v.parentVids || !v.parentVids.length || v.parentVids.includes(parentVid));
    }

    return list;
  }

  // ==================== 交互 ====================

  /**
   * 选中某个值，并清理因此失效的下游选择
   * @param {object} selected  当前选择（不会被修改，返回新对象）
   * @param {number} refPid    父属性的 refPid
   * @param {*} value          下拉传 vid；数值传字符串/数字
   */
  onSelect(selected, refPid, value) {
    const prop = this.byRefPid[refPid];
    const next = Object.assign({}, selected);

    if (prop && prop.propertyValueType === VALUE_TYPE.NUMBER) {
      next['num_' + refPid] = value;
    } else {
      next[refPid] = value;
    }

    return this._prune(next);
  }

  /** 取消选择 */
  onClear(selected, refPid) {
    const next = Object.assign({}, selected);
    delete next[refPid];
    delete next['num_' + refPid];
    return this._prune(next);
  }

  /**
   * 清理：不可见属性的值删掉；下游选择若不在新可选项里也删掉
   */
  _prune(state) {
    let changed = true;
    let guard = 0;

    while (changed && guard++ < 20) {
      changed = false;
      for (const p of this.props) {
        const key = p.propertyValueType === VALUE_TYPE.NUMBER ? 'num_' + p.refPid : p.refPid;
        const ikey = this.inputKey(p);

        // 不可见 → 清空（含数值部分）
        if (!this.isVisible(p, state)) {
          if (key in state) { delete state[key]; changed = true; }
          if (ikey in state) { delete state[ikey]; changed = true; }
          continue;
        }

        // 可见但当前值已不在可选项内 → 清空（数值型跳过）
        if (p.propertyValueType === VALUE_TYPE.SELECT && state[p.refPid] !== undefined) {
          const opts = this.optionsFor(p, state);
          if (!opts.some((v) => v.vid === state[p.refPid])) {
            delete state[p.refPid];
            if (ikey in state) delete state[ikey];
            changed = true;
          }
        }
      }
    }
    return state;
  }

  // ==================== 校验 ====================

  /**
   * 返回缺失的必填项名称
   * 注意：required=true 但可选项为 0 的属性会跳过（例如里料纹理=无里料时的里衬成分）
   */
  validate(selected = {}) {
    const missing = [];
    for (const p of this.requiredProps(selected)) {
      const key = p.propertyValueType === VALUE_TYPE.NUMBER ? 'num_' + p.refPid : p.refPid;
      const v = selected[key];
      if (v === undefined || v === null || v === '') { missing.push(p.name); continue; }
      // 成分比例这类还要校验数值部分
      if (this.needsNumberInput(p)) {
        const n = selected[this.inputKey(p)];
        if (n === undefined || n === null || n === '') missing.push(p.name + '(数值)');
      }
    }
    return missing;
  }

  // ==================== 组装 ====================

  /**
   * 组装 productPropertyReqs
   * 只包含当前可见、有值、且不是规格（颜色/尺码）的属性
   */
  buildPropertyReqs(selected = {}) {
    const out = [];

    for (const p of this.props) {
      if (this.isSpec(p)) continue;
      if (!this.isVisible(p, selected)) continue;

      if (p.propertyValueType === VALUE_TYPE.NUMBER) {
        const num = selected['num_' + p.refPid];
        if (num === undefined || num === null || num === '') continue;
        out.push({
          templatePid: p.templatePid,
          pid: p.pid,
          refPid: p.refPid,
          propName: p.name,
          vid: 0,
          propValue: String(num),
          valueUnit: (p.valueUnit || [''])[0],
          valueExtendInfo: '',
          numberInputValue: '',
        });
        continue;
      }

      const vid = selected[p.refPid];
      if (vid === undefined || vid === null) continue;
      const v = (p.values || []).find((x) => x.vid === vid);
      if (!v) continue;

      // controlType=16（成分比例）：需要额外数值
      let numberInputValue = '';
      if (this.needsNumberInput(p)) {
        const raw = selected[this.inputKey(p)];
        const num = raw === undefined || raw === '' ? p.maxValue : Number(raw);
        const prec = p.valuePrecision || 2;
        numberInputValue = Number(num).toFixed(prec);
      }

      out.push({
        templatePid: p.templatePid,
        pid: p.pid,
        refPid: p.refPid,
        propName: p.name,
        vid: v.vid,
        propValue: v.value,
        valueUnit: (p.valueUnit || [''])[0],
        valueExtendInfo: v.extendInfo || '',
        numberInputValue,
      });
    }

    return out;
  }

  // ==================== 规格（颜色 / 尺码）====================

  /** 颜色属性（pid=13）*/
  get colorProp() {
    return (this.byPid[13] || [])[0] || null;
  }

  /** 尺码属性（pid=14）*/
  get sizeProp() {
    return (this.byPid[14] || [])[0] || null;
  }

  /** 颜色可选值 */
  colorOptions() {
    return this.colorProp ? (this.colorProp.values || []) : [];
  }

  /** 尺码可选值 */
  sizeOptions() {
    return this.sizeProp ? (this.sizeProp.values || []) : [];
  }

  /**
   * 颜色按色系分组（对应 UI 的两级下拉：色系 → 具体颜色）
   * 模板里 values[].group = { id, name }，name 就是「白色系」「红色系」这类
   * @returns {Array<{ groupId, groupName, colors:[{vid,value,extendInfo}] }>}
   */
  colorGroups() {
    const map = new Map();
    for (const v of this.colorOptions()) {
      const g = v.group || {};
      const gid = g.id ?? 0;
      const gname = g.name || '其他';
      if (!map.has(gid)) map.set(gid, { groupId: gid, groupName: gname, colors: [] });
      map.get(gid).colors.push({
        vid: v.vid,
        value: v.value,
        extendInfo: v.extendInfo || null,
      });
    }
    return [...map.values()].sort((a, b) => a.groupId - b.groupId);
  }

  /**
   * 尺码按尺码体系分组（对应 UI 的「欧美尺码 / 亚洲尺码」）
   * 模板里 values[].group = { id, name }，name 是「中国码」「美国码」这类
   * @returns {Array<{ groupId, groupName, sizes:[{vid,value,specId}] }>}
   */
  sizeGroups() {
    const map = new Map();
    for (const v of this.sizeOptions()) {
      const g = v.group || {};
      const gid = g.id ?? 0;
      const gname = g.name || '其他';
      if (!map.has(gid)) map.set(gid, { groupId: gid, groupName: gname, sizes: [] });
      map.get(gid).sizes.push({
        vid: v.vid,
        value: v.value,
        specId: v.specId,
      });
    }
    return [...map.values()].sort((a, b) => a.groupId - b.groupId);
  }

  /**
   * 尺码三层树（对应官网真正的选择器）
   *
   * 官网的三层结构：
   *   ① 尺码标准：欧美尺码 / 亚洲尺码（巴西码等按 group 名单独成组）
   *   ② 类型：常规 / 均码 / Tall
   *   ③ 具体尺码：XXS / XS / … / one-size / Tall XXS / Asian XS …
   *
   * 判定规则（从真实模板数据反推）：
   *   - value 以 "Asian " 开头   → 亚洲尺码
   *   - 否则                    → 欧美尺码
   *   - value 含 "one-size"      → 均码
   *   - value 含 "Tall"          → Tall
   *   - 其余                     → 常规
   *   - group 名不是「中国码」的（如巴西码）→ 用 group 名当标准
   *
   * 实测类目 30096：
   *   中国码 29 个 → 欧美(15: XXS…XXL + one-size + Tall XXS…XXL)
   *                  + 亚洲(14: Asian XS…XXL + Asian One-size + Asian Tall …)
   *   巴西码 21 个 → 巴西码/常规
   *
   * @returns {Object} { [标准]: { [类型]: [{vid, value, specId, groupId, groupName}] } }
   */
  sizeOptionsTree() {
    const tree = {};

    for (const v of this.sizeOptions()) {
      const name = String(v.value || '');
      const g = v.group || {};
      const gname = g.name || '';

      // ① 尺码标准
      let standard;
      if (gname && gname !== '中国码' && !/中国/.test(gname)) {
        // 巴西码 / 美国码 / 欧码 … 直接用 group 名
        standard = gname.endsWith('码') ? gname.replace(/码$/, '尺码') : gname;
      } else {
        standard = /^asian\s/i.test(name) ? '亚洲尺码' : '欧美尺码';
      }

      // ② 类型
      let type;
      if (/one-?size/i.test(name)) type = '均码';
      else if (/^plus\s/i.test(name)) type = 'Plus';
      else if (/\btall\b/i.test(name)) type = 'Tall';
      else type = '常规';

      // ③ 挂载
      tree[standard] = tree[standard] || {};
      tree[standard][type] = tree[standard][type] || [];
      tree[standard][type].push({
        vid: v.vid,
        value: name,
        specId: v.specId,
        groupId: g.id,
        groupName: gname,
      });
    }

    // 类型固定顺序：常规 → 均码 → Plus → Tall
    const ORDER = ['常规', '均码', 'Plus', 'Tall'];
    for (const std of Object.keys(tree)) {
      const sorted = {};
      for (const t of ORDER) if (tree[std][t]) sorted[t] = tree[std][t];
      for (const t of Object.keys(tree[std])) if (!sorted[t]) sorted[t] = tree[std][t];
      tree[std] = sorted;
    }
    return tree;
  }

  /**
   * 按「标准 + 类型」取尺码列表
   * @param {string} standard 欧美尺码 / 亚洲尺码 / 巴西尺码
   * @param {string} type     常规 / 均码 / Tall
   */
  sizeOptionsOf(standard, type) {
    const tree = this.sizeOptionsTree();
    const s = tree[standard];
    if (!s) return [];
    return s[type] || [];
  }

  /**
   * 按名称找颜色 / 尺码（大小写不敏感）
   * @param {'color'|'size'} kind
   * @param {string} name
   */
  findSpec(kind, name) {
    const list = kind === 'color' ? this.colorOptions() : this.sizeOptions();
    const kw = String(name).toLowerCase();
    return list.find((x) => String(x.value).toLowerCase() === kw)
      || list.find((x) => String(x.value).toLowerCase().includes(kw))
      || null;
  }

  /**
   * 组装一条规格定义（productSpecPropertyReqs 的一项）
   * @param {'color'|'size'} kind
   * @param {number} vid
   */
  buildSpecReq(kind, vid) {
    const prop = kind === 'color' ? this.colorProp : this.sizeProp;
    if (!prop) throw new Error(`[TemplateEngine] 类目模板里没有 ${kind} 规格`);
    const v = (prop.values || []).find((x) => x.vid === vid);
    if (!v) throw new Error(`[TemplateEngine] ${prop.name} 没有 vid=${vid}`);

    // ⚠️ 模板里的 group 是对象 {id, name}，不是数字
    const gid = v.group && typeof v.group === 'object' ? v.group.id : v.group;
    const gname = v.group && typeof v.group === 'object' ? (v.group.name || '') : '';

    return {
      parentSpecId: prop.parentSpecId || (kind === 'color' ? 1001 : 3001),
      parentSpecName: prop.name,
      specId: v.specId,
      specName: v.value,
      groupId: gid,
      vid: v.vid,
      refPid: prop.refPid,
      pid: prop.pid,
      templatePid: prop.templatePid,
      propName: prop.name,
      propValue: v.value,
      valueUnit: '',
      valueGroupId: gid,
      valueGroupName: gname,
      valueExtendInfo: v.extendInfo || '',
    };
  }

  /**
   * 组装 SKU 规格（productSkuSpecReqs 的一项，只要基础字段）
   * @param {'color'|'size'} kind
   * @param {number} vid
   */
  buildSkuSpec(kind, vid) {
    const prop = kind === 'color' ? this.colorProp : this.sizeProp;
    if (!prop) throw new Error(`[TemplateEngine] 类目模板里没有 ${kind} 规格`);
    const v = (prop.values || []).find((x) => x.vid === vid);
    if (!v) throw new Error(`[TemplateEngine] ${prop.name} 没有 vid=${vid}`);

    const gid = v.group && typeof v.group === 'object' ? v.group.id : v.group;

    return {
      parentSpecId: prop.parentSpecId || (kind === 'color' ? 1001 : 3001),
      parentSpecName: prop.name,
      specId: v.specId,
      specName: v.value,
      groupId: gid,
      vid: v.vid,
    };
  }

  // ==================== 表单描述（给前端渲染）====================

  /**
   * 生成属性表单字段描述（不含颜色/尺码，规格用 colorOptions/sizeOptions 单独渲染）
   * @param {object} selected 当前选择
   */
  describe(selected = {}) {
    return this.visibleProps(selected).filter((p) => !this.isSpec(p)).map((p) => {
      const isNumber = p.propertyValueType === VALUE_TYPE.NUMBER;
      return {
        key: isNumber ? 'num_' + p.refPid : String(p.refPid),
        refPid: p.refPid,
        pid: p.pid,
        templatePid: p.templatePid,
        label: p.name,
        lang2Name: p.lang2Name,
        required: p.required && this.optionsFor(p, selected).length > 0,
        type: isNumber ? 'number' : (this.needsNumberInput(p) ? 'select+number' : 'select'),
        unit: (p.valueUnit || [''])[0],
        options: isNumber ? [] : this.optionsFor(p, selected).map((v) => ({
          label: v.value,
          value: v.vid,
        })),
        value: isNumber ? (selected['num_' + p.refPid] ?? '') : (selected[p.refPid] ?? null),
        // controlType=16 才有：数值部分
        numberInput: this.needsNumberInput(p) ? {
          key: this.inputKey(p),
          title: p.numberInputTitle,
          min: p.minValue,
          max: p.maxValue,
          precision: p.valuePrecision,
          unit: (p.valueUnit || [''])[0],
          value: selected[this.inputKey(p)] ?? '',
        } : null,
      };
    });
  }

  /** 概览（排错用）*/
  summary() {
    const attrs = this.attrProps;
    const cond = attrs.filter((p) => p.showCondition && p.showCondition.length);
    const dep = attrs.filter((p) => p.templatePropertyValueParentList && p.templatePropertyValueParentList.length);
    const valLvl = attrs.filter((p) => (p.values || []).some((v) => v.parentVids && v.parentVids.length));
    return {
      templateId: this.templateId,
      total: this.props.length,
      attrs: attrs.length,
      specs: this.specProps.map((p) => ({ name: p.name, pid: p.pid, parentSpecId: p.parentSpecId })),
      select: attrs.filter((p) => p.propertyValueType === VALUE_TYPE.SELECT).length,
      number: attrs.filter((p) => p.propertyValueType === VALUE_TYPE.NUMBER).length,
      conditional: cond.map((p) => p.name),
      valueDependent: dep.map((p) => p.name),
      valueLevelLimited: valLvl.map((p) => p.name),
    };
  }
}

module.exports = { TemplateEngine, VALUE_TYPE, CONTROL_TYPE };
