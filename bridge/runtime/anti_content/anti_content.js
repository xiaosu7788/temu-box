/**
 * Temu 跨境卖家 anti-content 生成器
 * ===================================
 * 在 Node.js 沙箱中加载风控 SDK 模块 42983，直接调用其 messagePack() 产出 anti-content。
 *
 * 用法：
 *   node anti_content.js                       # 用默认采集开关生成一条
 *   node anti_content.js --serverTime <毫秒>   # 指定服务端时间
 *   node anti_content.js --count 5             # 连续生成 5 条
 *   node anti_content.js --json                # 输出 JSON（含时间戳/长度）
 *
 * 编程接口：
 *   const { createAntiContent } = require('./anti_content');
 *   const gen = await createAntiContent();     // 初始化一次
 *   const ac  = gen.generate();                // 同步产出 anti-content
 *   gen.setServerTime(1730000000000);          // 更新服务端时间
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const MODULE_FILE = path.join(__dirname, '_mod42983.js');

/** Captured Temu login baseline with a 40-character nano fingerprint. */
const DEFAULT_NANO_FP = 'XpmJlpCanqXqlpTyXT_sfvWwQqF4RgKZZ6K9Tz4O';
const _nanoStore = { value: null };

/**
 * pbc 自校验常量说明（2026-09-30 逆向修正，此前实现有误）：
 * AC 结构 = "0aq" + encode(deflate(载荷) + 4B ct)，ct 追加在 deflate 流之后（流外）。
 * ct = u.pbc(dt.toString() + ae.toString())，是「SDK 构建指纹」，随 SDK 构建变化：
 *   · 9/12 构建真实会话 20 条样本 = f559e825
 *   · 9/26 构建真实会话 4 条样本  = aa04d1ad
 *   · 本地 _mod42983.js（9/11 构建）沙箱自算 = ea51a67b（与所加载模块自洽）
 * 旧判读「AC 明文 @200-203 为 ct」是解码器截断伪影——该位置实为载荷内
 * tag11 = pbc(location.href)。ct 必须与所加载 SDK 构建配套，禁止强行注入。
 */

/** 模块 65531 里的采集开关（全部开启） */
const COLLECT_FLAGS = {
  touchEventData: true,
  clickEventData: true,
  focusblurEventData: true,
  changeEventData: true,
  locationInfo: true,
  referrer: true,
  browserSize: true,
  browserInfo: true,
  token: true,
  fingerprint: true,
};

/** 构造浏览器 window 环境（SDK 靠 `W = window` 判定运行环境） */
function buildWindow(opts) {
  const o = Object.assign({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36 Edg/152.0.0.0',
    /**
     * location.href → TLV tag7（packN 会截断到 128 字节后写入）。
     * 默认取带 query 的登录页完整 URL，与真机抓包对齐。
     * 注：2026-10-04 同会话单变量 A/B 实测 tag7 取值不影响风控判定
     * （带 query / 无 query / 伪造域名 三者打 /mms/login 均返回 40002002）。
     */
    url: 'https://seller.kuajingmaihuo.com/settle/seller-login?redirectUrl=https%3A%2F%2Fagentseller-us.temu.com%2F&region=3&sessionType=0',
    width: 1440,
    height: 900,
    language: 'zh-CN',
    hardwareConcurrency: 8,
    deviceMemory: 8,
  }, opts || {});
  const inputCookies = String(o.cookie || '').split(';').map(v => v.trim()).filter(Boolean);
  const suppliedNano = inputCookies.find(v => v.startsWith('_nano_fp='));
  const nanoFp = suppliedNano && suppliedNano.slice('_nano_fp='.length)
    ? suppliedNano.slice('_nano_fp='.length)
    : DEFAULT_NANO_FP;
  _nanoStore.value = nanoFp;
  const initialCookies = inputCookies.filter(v => !v.startsWith('_nano_fp='));
  initialCookies.push('_nano_fp=' + nanoFp);
  let cookieStore = initialCookies.join('; ');

  /**
   * 真实浏览器事件时间约为 8~12 秒，模拟页面加载后再交互。
   */
  const PAGE_ALIVE_MS = Number(o.pageAliveMs) || (8000 + Math.floor(Math.random() * 4000)); // 8000~11999ms
  const now0 = Date.now() - PAGE_ALIVE_MS;
  const perf = {
    now: () => Date.now() - now0,
    timeOrigin: now0,
    timing: {
      navigationStart: now0 - 1000, loadEventEnd: now0 - 500,
      domContentLoadedEventEnd: now0 - 800, responseStart: now0 - 950,
      requestStart: now0 - 980, connectStart: now0 - 990, connectEnd: now0 - 985,
      domainLookupStart: now0 - 995, domainLookupEnd: now0 - 992,
      redirectStart: 0, redirectEnd: 0, fetchStart: now0 - 995,
      unloadEventStart: 0, unloadEventEnd: 0,
    },
    getEntriesByType: () => [], getEntriesByName: () => [], getEntries: () => [],
    mark() {}, measure() {}, clearMarks() {}, clearMeasures() {},
    memory: { jsHeapSizeLimit: 2172649472, totalJSHeapSize: 12345678, usedJSHeapSize: 9876543 },
  };

  const XHR = function () {
    this.open = () => {}; this.send = () => {}; this.setRequestHeader = () => {};
    this.getResponseHeader = () => null; this.addEventListener = () => {};
    this.removeEventListener = () => {}; this.upload = {};
    this.readyState = 0; this.status = 0; this.withCredentials = false;
  };

  const canvasCtx = {
    fillText() {}, fillRect() {}, clearRect() {}, save() {}, restore() {},
    translate() {}, rotate() {}, scale() {}, beginPath() {}, closePath() {},
    arc() {}, stroke() {}, fill() {}, moveTo() {}, lineTo() {}, rect() {},
    bezierCurveTo() {}, quadraticCurveTo() {}, setTransform() {}, transform() {},
    measureText: () => ({ width: 100, actualBoundingBoxAscent: 10 }),
    getImageData: () => ({ data: new Uint8Array(4 * 300 * 150), width: 300, height: 150 }),
    putImageData() {}, drawImage() {},
    createLinearGradient: () => ({ addColorStop() {} }),
    isPointInPath: () => false,
    getExtension: () => null, getParameter: () => 0, getSupportedExtensions: () => [],
  };

  const doc = {
    createElement: (tag) => {
      if (String(tag).toLowerCase() === 'canvas') {
        return {
          width: 300, height: 150, style: {},
          getContext: () => canvasCtx,
          toDataURL: () => 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==',
          getBoundingClientRect: () => ({ width: 300, height: 150, top: 0, left: 0, right: 300, bottom: 150 }),
        };
      }
      return {
        style: {}, tagName: String(tag).toUpperCase(),
        getContext: () => null, setAttribute() {}, getAttribute: () => null,
        appendChild() {}, removeChild() {}, insertBefore() {},
        addEventListener() {}, removeEventListener() {}, remove() {},
        getBoundingClientRect: () => ({ width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0 }),
      };
    },
    documentElement: { style: {}, addEventListener() {}, removeEventListener() {}, clientWidth: o.width, clientHeight: o.height },
    body: { style: {}, appendChild() {}, addEventListener() {}, clientWidth: o.width, clientHeight: o.height },
    cookie: '',
    addEventListener: (t, f) => addListener(t, f), removeEventListener: (t, f) => removeListener(t, f),
    querySelectorAll: () => [], querySelector: () => null,
    getElementsByTagName: () => [], getElementsByClassName: () => [],
    hidden: false, visibilityState: 'visible', hasFocus: () => true,
    // 注意：document 上不定义 ontouchstart。
    // SDK 用 ("ontouchstart" in document) 判定触屏环境（内部变量 g）：
    //   存在 -> g=true  -> 监听 touchstart/touchmove/click，ek(1)
    //   缺失 -> g=false -> 监听 mousedown/mousemove/click，ek(2)   ← 抓包环境实测走这条
    referrer: '', title: '', URL: o.url,
    characterSet: 'UTF-8', charset: 'UTF-8', compatMode: 'CSS1Compat',
  };

  // 收集 SDK 写入的 cookie（_nano_fp 等风控指纹 cookie）
  const cookieLog = [];
  Object.defineProperty(doc, 'cookie', {
    get() { return cookieStore; },
    set(v) { cookieLog.push(String(v)); cookieStore += '; ' + v; },
    configurable: true,
  });

  // ---- 事件派发支持：SDK 会监听 mousemove/touchmove 记录交互轨迹 ----
  const listeners = Object.create(null);
  const addListener = (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); };
  const removeListener = (type, fn) => {
    const a = listeners[type]; if (!a) return;
    const i = a.indexOf(fn); if (i >= 0) a.splice(i, 1);
  };

  const u = new URL(o.url);
  const win = {
    performance: perf,
    navigator: {
      userAgent: o.userAgent, platform: 'Win32', language: o.language,
      languages: [o.language, 'zh'], product: 'Gecko', productSub: '20030107',
      vendor: 'Google Inc.', vendorSub: '',
      appVersion: o.userAgent.replace('Mozilla/', ''), appName: 'Netscape', appCodeName: 'Mozilla',
      cookieEnabled: true, onLine: true,
      hardwareConcurrency: o.hardwareConcurrency, deviceMemory: o.deviceMemory,
      maxTouchPoints: 0, doNotTrack: null, webdriver: undefined,
      // 真实 Chrome 有 ~5 个插件。dt() 的 bit8 反无头检测：
      // plugins.length>0 → bit8=0（真实浏览器）；length=0 → bit8=1（被判定无头/无插件）。
      plugins: (() => {
        const names = ['PDF Viewer', 'Chrome PDF Viewer', 'Chromium PDF Viewer', 'Microsoft Edge PDF Viewer', 'WebKit built-in PDF'];
        const pt = Object.assign([], {
          length: names.length, item: (i) => pt[i], namedItem: () => pt[0],
          refresh: () => {}, [Symbol.iterator]: Array.prototype[Symbol.iterator],
        });
        names.forEach((n, i) => { pt[i] = { name: n, description: n, filename: n + '.dll', length: 0 }; });
        return pt;
      })(),
      mimeTypes: { length: 2, item: (i) => ({ type: 'application/pdf', suffixes: 'pdf', description: 'PDF' }), namedItem: () => null },
    },
    location: {
      href: o.url, protocol: u.protocol, hostname: u.hostname, host: u.host,
      origin: u.origin, pathname: u.pathname, search: u.search, hash: u.hash,
      port: u.port, ancestorOrigins: { length: 0 },
    },
    screen: {
      width: o.width, height: o.height, availWidth: o.width, availHeight: o.height,
      colorDepth: 24, pixelDepth: 24, availLeft: 0, availTop: 0,
      orientation: { type: 'landscape-primary', angle: 0 },
    },
    innerWidth: o.width, innerHeight: o.height,
    outerWidth: o.width, outerHeight: o.height,
    screenX: 0, screenY: 0, pageXOffset: 0, pageYOffset: 0, devicePixelRatio: 1,
    addEventListener: (t, f) => addListener(t, f), removeEventListener: (t, f) => removeListener(t, f), dispatchEvent() { return true; },
    setTimeout, clearTimeout, setInterval, clearInterval,
    document: doc,
    __cookieLog: cookieLog,
    // 监听器可能是函数，也可能是带 handleEvent 的对象（DOM 事件处理器规范）
    __dispatch: (type, ev) => {
      const a = listeners[type]; if (!a) return;
      for (const f of a.slice()) {
        try {
          if (typeof f === 'function') f(ev);
          else if (f && typeof f.handleEvent === 'function') f.handleEvent(ev);
        } catch (e) {}
      }
    },
    __listeners: listeners,
    get __cookieStore() { return cookieStore; },
      localStorage: {
        getItem: (k) => {
          if (k === 'nano_fp' || k === '_nano_fp') return _nanoStore.value;
          return null;
        },
        setItem: (k, v) => {
          if (k === 'nano_fp' || k === '_nano_fp') {
            _nanoStore.value = String(v);
            try {
              if (cookieStore.indexOf('_nano_fp=') === -1) {
                cookieLog.push('_nano_fp=' + _nanoStore.value);
                cookieStore += '; _nano_fp=' + _nanoStore.value;
              }
            } catch (e) {}
          }
        },
        removeItem: () => {}, clear() { _nanoStore.value = null; }, key: () => null,
        get length() { return _nanoStore.value ? 1 : 0; },
      },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {}, clear() {}, key: () => null, length: 0 },
    XMLHttpRequest: XHR,
    fetch: () => Promise.reject(new Error('no fetch')),
    crypto: {
      getRandomValues: (a) => { for (let i = 0; i < a.length; i++) a[i] = Math.floor(Math.random() * 256); return a; },
      randomUUID: () => 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
        const r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
      }),
    },
    requestAnimationFrame: (cb) => setTimeout(() => cb(Date.now()), 16),
    cancelAnimationFrame: (id) => clearTimeout(id),
    matchMedia: () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }),
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    history: { length: 1, state: null, pushState() {}, replaceState() {}, back() {}, forward() {}, go() {} },
    WebGLRenderingContext: function () {}, WebGL2RenderingContext: function () {},
    AudioContext: function () {
      return {
        createOscillator: () => ({ type: '', frequency: { value: 0, setValueAtTime() {} }, connect() {}, start() {}, stop() {} }),
        createAnalyser: () => ({ fftSize: 2048, frequencyBinCount: 1024, connect() {}, getFloatFrequencyData() {}, getByteFrequencyData() {} }),
        createGain: () => ({ gain: { value: 0, setValueAtTime() {} }, connect() {} }),
        destination: {}, sampleRate: 44100, state: 'suspended', close: () => Promise.resolve(),
      };
    },
    offscreenBuffering: true, doNotTrack: null, name: '', isSecureContext: true,
    crossOriginIsolated: false,
  };
  win.webkitAudioContext = win.AudioContext;

  // SDK 的 tag12 采集器：window.DeviceOrientationEvent ? "y" : "n"
  // 真实桌面 Chrome 该构造函数存在 → "y"；缺失会输出 "n" 造成 anti-content 偏差
  win.DeviceOrientationEvent = function DeviceOrientationEvent() {};
  win.DeviceMotionEvent = function DeviceMotionEvent() {};

  // ★ window.chrome —— 风控环境特征位 data[12] 的来源（实测对齐）
  // dt() 中： r[12] = (W["chrome"] === void 0) ? 1 : 0
  // 真实桌面 Chrome（有头模式）window.chrome = { loadTimes, csi, app }，
  // 沙箱若缺失会被判定为自动化环境（data[12]=1），使 tag10 加权和偏移 4096，
  // 服务端校验失败返回 4000000。
  // ★ runtime —— ee.init()（即 ne）浏览器指纹检测位（index 4, 权重 4）：
  //   chrome && (chrome.webstore || chrome.runtime) → +4；缺失 → d0 字节=0x00（真实=0x10）
  win.chrome = {
    loadTimes: function () { return {}; },
    csi: function () { return {}; },
    app: { isInstalled: false, InstallState: {}, RunningState: {} },
    runtime: { connect: function () {}, sendMessage: function () {} },
  };

  // SDK 通过 W["parseInt"] 等访问全局函数，浏览器里 window 上确实有，必须补齐
  const globals = {
    Object, Array, String, Number, Boolean, Math, JSON, Error, TypeError, RangeError,
    Symbol, Date, RegExp, Promise, Proxy, Reflect, Map, Set, WeakMap, WeakSet,
    Uint8Array, Uint16Array, Uint32Array, Int8Array, Int16Array, Int32Array,
    Float32Array, Float64Array, ArrayBuffer, DataView,
    encodeURIComponent, decodeURIComponent, encodeURI, decodeURI,
    parseInt, parseFloat, isNaN, isFinite, escape, unescape, Function,
  };
  Object.assign(win, globals);
  win.setTimeout = setTimeout;
  win.clearTimeout = clearTimeout;
  win.setInterval = setInterval;
  win.clearInterval = clearInterval;
  win.atob = (s) => Buffer.from(s, 'base64').toString('binary');
  win.btoa = (s) => Buffer.from(s, 'binary').toString('base64');
  win.document.location = win.location;
  win.frames = win; win.self = win; win.top = win; win.parent = win; win.window = win;

  // SDK 构造时先更新服务端时间，再读取 ot 作为事件基准。
  // 将第二次 Date.now() 读数回拨到页面加载时刻，之后保持真实时钟。
  // Proxy 保留 Date() 与 new Date() 的原生行为，供 SDK 的 cookie 写入逻辑使用。
  const _origDateNow = Date.now.bind(Date);
  let dateNowCalls = 0;
  let eventTimeOrigin = null;
  let eventElapsedMs = null;
  let lastRealNow = 0;
  win.Date = new Proxy(Date, {
    get(target, property, receiver) {
      if (property === 'now') {
        return function () {
          dateNowCalls++;
          const now = _origDateNow();
          if (dateNowCalls === 1) {
            eventTimeOrigin = now - PAGE_ALIVE_MS;
            return eventTimeOrigin;
          }
          // 第 2 次起的真实读数记为 lastRealNow：SDK 在 init 期用它写 ot（事件时间基准）。
          // 事件派发阶段返回 lastRealNow + elapsed，使 T = Date.now() - ot == elapsed，
          // 与真实浏览器一致（真实样本 T = 11808/8261/9906/11808/11809，即相对 ot 的毫秒）。
          // 旧实现返回 eventTimeOrigin + elapsed，比 ot 早 PAGE_ALIVE_MS(8~12s)，T 变小甚至为负。
          lastRealNow = now;
          if (eventElapsedMs !== null && eventTimeOrigin !== null) {
            return lastRealNow + eventElapsedMs;
          }
          return now;
        };
      }
      return Reflect.get(target, property, receiver);
    },
  });
  win.__setEventElapsed = (ms) => { eventElapsedMs = ms === null ? null : Number(ms); };
  win.__pageAliveMs = PAGE_ALIVE_MS;
  return win;
}

/** 在沙箱里加载模块 42983，返回 SDK 构造函数 */
function loadSDK(win) {
  let src = fs.readFileSync(MODULE_FILE, 'utf-8');
  const marker = '42983:';
  const i = src.indexOf(marker);
  if (i === -1) throw new Error('未在 _mod42983.js 中找到模块 42983');
  src = src.slice(i + marker.length);

  // 在 ae() 之前插入 ct 注入钩子：
  // 真实链路 ct = u.pbc(dt.toString() + ae.toString())，
  // 沙箱里 Function.prototype.toString 结果与浏览器不同，导致 pbc 结果偏差。
  //
  // 注意：u.pbc 同时被 locationInfo 采集器使用（pbc(location.href)），
  //      那里需要真实 CRC32。因此按输入特征分流：
  //        · 输入含 "function(" → 判定为 ce() 自校验，返回实测常量 ct
  //        · 其余 → 走原始 CRC32 实现
  // ---- 第二处钩子：校准环境特征加权和（tag10 段） ----
  // Et.packN() = [ek(10), u.va(n)]，n = Σ data[i]*i。
  // 沙箱里 dt() 的结果与真机不同，且 index 7 为 null 导致 NaN，
  // 实测真机该段字节为 [0x50, 0x80, 0xc3, 0x01]（n = 24960）。
  // 做法：直接替换 Et.packN 的返回值。
  // tag10 是「环境特征加权和」动态值，随每次采集变化，不可硬编码
  // （硬编码会导致服务端返回 4000000 系统异常）。
  // 这里只做防御性兜底：若算出 NaN 才回退到默认值，正常路径走原始实现。
  // ★ tag10（AC 环境指纹段）正确算法（逆向确定）：
  // Match the captured login sample's environment mask: 24960 = 2^14+2^13+2^8+2^7.
  const tag10Hook =
    'try{' +
    '  if(typeof Et==="object"&&Et&&Array.isArray(Et.data)&&typeof Et.packN==="function"){' +
    '    var _origPackN=Et.packN;' +
    '    Et.packN=function(){' +
    '      try{ Et.data[7]=1; Et.data[8]=1; Et.data[13]=1; Et.data[14]=1; }catch(e){}' +
    '      return _origPackN.apply(Et,arguments);' +
    '    };' +
    '  }' +
    '}catch(e){};';

  // 在 SDK 实例化之前注入 tag10 环境指纹钩子（pbc/ct 注入钩子已于 2026-09-30 撤销：
  // ct 由沙箱内原始 u.pbc(dt+ae) 自行计算，与所加载模块构建自洽，见文件头说明）
  const injectPoint = src.indexOf('var se=new ue;');
  if (injectPoint >= 0) {
    src = src.slice(0, injectPoint) + tag10Hook + src.slice(injectPoint);
  }

  const sandbox = Object.assign({}, win, {
    console, Buffer, process: undefined,
    setTimeout, clearTimeout, setInterval, clearInterval, queueMicrotask,
  });
  Object.assign(sandbox, {
    Object, Array, String, Number, Boolean, Math, JSON, Error, TypeError, RangeError,
    Symbol, RegExp, Promise, Proxy, Reflect, Map, Set, WeakMap, WeakSet,
    Uint8Array, Uint16Array, Uint32Array, Int8Array, Int16Array, Int32Array,
    Float32Array, Float64Array, ArrayBuffer, DataView,
    encodeURIComponent, decodeURIComponent, encodeURI, decodeURI,
    parseInt, parseFloat, isNaN, isFinite, escape, unescape,
    atob: (s) => Buffer.from(s, 'base64').toString('binary'),
    btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
  });
  // Date 必须用 buildWindow 里注入的偏移版（保证 SDK 事件时间戳为页面存活毫秒）
  sandbox.Date = win.Date;
  sandbox.window = win;
  sandbox.self = win;

  vm.createContext(sandbox);
  const wrapped = 'var M={exports:{}};(' + src + ').call(globalThis, M);globalThis.__SDK=M.exports;';
  vm.runInContext(wrapped, sandbox, { filename: 'mod42983.js' });
  const SDK = sandbox.__SDK;
  if (typeof SDK !== 'function') throw new Error('模块 42983 导出不是构造函数，实际为 ' + typeof SDK);
  return { SDK, sandbox };
}

/**
 * 创建 anti-content 生成器。
 * @param {{serverTime?:number, userAgent?:string, url?:string, width?:number, height?:number, cookie?:string}} opts
 *   cookie: 真实会话 cookie 串（必须含 api_uid，否则 anti-content 缺项导致风控 40002）
 * @returns {Promise<{generate:Function,setServerTime:Function,setConfig:Function,serverTime:number}>}
 */
async function createAntiContent(opts) {
  const o = opts || {};
  const win = buildWindow(o);   // o.cookie 会初始化 document.cookie（供 api_uid 采集）
  const { SDK } = loadSDK(win);

  const serverTime = o.serverTime || await fetchServerTime();
  const inst = SDK({ serverTime });
  try { inst.init(); } catch (e) { /* init 在部分环境非必需 */ }
  try { inst.updateServerTime(serverTime); } catch (e) { /* 已由构造参数注入 */ }

  /** 提取 SDK 写入的风控 cookie（如 _nano_fp） */
  function getCookies() {
    const out = {};
    for (const raw of (win.__cookieLog || [])) {
      const pair = String(raw).split(';')[0];
      const i = pair.indexOf('=');
      if (i > 0) out[pair.slice(0, i).trim()] = pair.slice(i + 1).trim();
    }
    const nano = /(?:^|;\s*)_nano_fp=([^;]*)/.exec(win.__cookieStore);
    if (nano) out._nano_fp = nano[1].trim();
    return out;

  }
  return {
    serverTime,
    pageAliveMs: win.__pageAliveMs,
    /** 派发浏览器事件（mousemove/touchmove 等），用于补齐交互轨迹采集项 */
    setEventElapsed: (ms) => win.__setEventElapsed(ms),
    dispatch: (type, ev) => win.__dispatch && win.__dispatch(type, ev),
    listeners: () => Object.keys(win.__listeners || {}),
    /** 产出一次 anti-content（字符串，形如 '0aq...'） */
    generate(flags) {
      // ★ tag14 = Date.now() - f（f 在 updateServerTime 时记录，packN 时重新 init）。
      // 真实浏览器在 messagePack 前一刻才同步服务端时间 → tag14=0。
      // 沙箱若不在打包前刷新 f，tag14 会是几十毫秒的漂移值，与真实样本不符。
      try { inst.updateServerTime(serverTime); } catch (e) {}
      const r = inst.messagePack(flags || COLLECT_FLAGS);
      if (typeof r !== 'string' || !r) throw new Error('messagePack 返回空值，环境注入不完整');
      return r;
    },
    /** SDK 生成的风控 cookie 对象 */
    getCookies,
    getNanoFp: () => _nanoStore.value,
    /** SDK 生成的 cookie 请求头字符串 */
    cookieHeader() {
      return Object.entries(getCookies()).map(([k, v]) => `${k}=${v}`).join('; ');
    },
    setServerTime(ts) {
      try { inst.updateServerTime(ts); } catch (e) {}
    },
    setConfig(cfg) {
      try { inst.init(cfg); } catch (e) {}
    },
  };
}

/** 从接口取服务端时间；失败则回落本地时间 */
async function fetchServerTime() {
  const url = 'https://seller.kuajingmaihuo.com/api/server/_stm';
  try {
    const res = await fetch(url, {
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
    });
    if (res.ok) {
      const j = await res.json();
      const t = j && j.server_time;
      if (typeof t === 'number' && t > 0) return t;
    }
  } catch (e) { /* 网络不可用时回落 */ }
  return Date.now();
}

module.exports = { createAntiContent, COLLECT_FLAGS, buildWindow, loadSDK };

// ---------------- CLI ----------------
if (require.main === module) {
  (async () => {
    const argv = process.argv.slice(2);
    const getArg = (name) => {
      const i = argv.indexOf('--' + name);
      return i === -1 ? null : (argv[i + 1] || true);
    };
    const count = parseInt(getArg('count') || '1', 10) || 1;
    const st = getArg('serverTime');
    const asJson = argv.includes('--json');

    const gen = await createAntiContent({ serverTime: st ? Number(st) : undefined });
    const results = [];
    for (let i = 0; i < count; i++) {
      const t0 = Date.now();
      let ac = null, err = null;
      try { ac = gen.generate(); } catch (e) { err = e.message; }
      results.push({ antiContent: ac, length: ac ? ac.length : 0, costMs: Date.now() - t0, error: err });
    }

    if (asJson) {
      console.log(JSON.stringify({ serverTime: gen.serverTime, results }, null, 2));
    } else {
      for (const r of results) {
        if (r.error) console.log('ERR:', r.error);
        else console.log(r.antiContent);
      }
    }
  })().catch(e => { console.error('FATAL:', e.message); process.exit(1); });
}
