/**
 * 素材库模块
 * ===========
 * 素材中心的查询、上传、重命名、删除、文件夹管理。
 *
 * 上传链路（三步）：
 *   1. get_signature  取上传签名
 *   2. store_image    multipart 上传字节 → 返回 url / width / height / etag
 *   3. material/create 登记进素材库（tag=0 建原图，tag=3 建装饰图）
 *
 * 用法：
 *   const { Material } = require('./material');
 *   const mat = new Material(ops.glo);
 *
 *   // 查
 *   const { total, list } = await mat.pageQuery({ carousel: true });
 *
 *   // 传
 *   const { imgUrl, materialId } = await mat.upload(buf, 'a.jpg');
 *
 *   // 改名（注意：改名会重新处理图片，imgUrl 会变）
 *   const { newImgUrl } = await mat.rename(materialId, imgUrl, '新名字');
 *
 *   // 删（单次 1~100 个）
 *   await mat.remove({ imgIds: [materialId] });
 *
 *   // 文件夹
 *   const root = await mat.folderTree();
 *   await mat.folderCreate('我的文件夹');
 *   await mat.folderDelete(folderId);
 */
const https = require('https');
const crypto = require('crypto');

const HOST = 'agentseller.temu.com';

/**
 * materialType 枚举
 * ⚠️ 视频是 0，图片是 1（实测确认，不是 1/2）
 */
const MATERIAL_TYPE = {
  VIDEO: 0,
  IMAGE: 1,
};

/** 视频素材格式 */
const VIDEO_FORMAT = 'MP4';

/** 视频规格（官网提示：1:1 或 3:4 或 16:9，时长 ≤600 秒，大小 ≤500MB）*/
const VIDEO_SPEC = {
  maxDurationMs: 600 * 1000,      // 600 秒
  maxSize: 500 * 1024 * 1024,     // 500 MB
  ratios: ['1:1', '3:4', '16:9'],
};

/**
 * 视频上传参数（来自前端上传器源码 newon_4689 的默认配置）
 *   minSizeOfSplitUpload = chunkSize = 5MB，concurrency = 2
 */
const VIDEO_SPLIT_THRESHOLD = 5 * 1024 * 1024;
const VIDEO_CHUNK_SIZE = 5 * 1024 * 1024;

/** 视频上传接口（前端 uploadPath.video / videoMultipart 原样）*/
const VIDEO_UPLOAD_PATH = {
  single: '/api/galerie/v1/store_video',
  init: '/api/galerie/large_file/v1/video/upload_init',
  part: '/api/galerie/large_file/v1/video/upload_part',
  complete: '/api/galerie/large_file/v1/video/upload_complete',
};

/** 视频上传统一带的 query */
const VIDEO_TAG = '?tag_name=goods-video-tag';

/**
 * CDN 实时处理模式（前端枚举 p，见 newon_4689）
 *   1 LimitMinAndCrop        限定最小宽高并居中裁剪（1:1 800x800 用的就是它）
 *   2 LimitMaxWidthAndHeight 限最大宽高，等比缩放不裁
 *   3 LimitMinWidthAndHeight 限最小宽高
 *   4 LimitMinLongAndShortEdge
 *   5 LimitMaxLongAndShortEdge
 */
const IMAGE_VIEW_MODE = {
  LIMIT_MIN_AND_CROP: 1,
  LIMIT_MAX_WIDTH_HEIGHT: 2,
  LIMIT_MIN_WIDTH_HEIGHT: 3,
  LIMIT_MIN_LONG_SHORT_EDGE: 4,
  LIMIT_MAX_LONG_SHORT_EDGE: 5,
};

/**
 * 上传时裁切规则（`pic_operations.rules` 的一项）
 * 前端 CutConfig 的 pipe 语法，服务端在 store_image 时直接按规则处理字节
 */
const CUT_RULE = {
  /** 按左上角偏移裁指定尺寸：/cut/{w}x{h}x{dx}x{dy} */
  cut: ({ width, height, dx = 0, dy = 0 }) => `/cut/${width}x${height}x${dx}x${dy}`,
  /** 按重心裁：/crop/{w}x{h}[/gravity/{g}] */
  crop: ({ width, height, gravity }) =>
    `/crop/${width || ''}x${height || ''}` + (gravity ? `/gravity/${gravity}` : ''),
  /** 内圆角 */
  iradius: (r) => `/iradius/${r}`,
  /** 外圆角 */
  rradius: (r) => `/rradius/${r}`,
};

/**
 * 拼 CDN 实时处理规则串（imageView2 语法，改 URL 即出图，不落盘）
 * @returns {string} 例如 `imageView2/1/w/800/h/800`
 */
function imageViewRule(mode, opts = {}) {
  const { width, height, format, quality, force, relativeQuality, lowestQuality } = opts;
  const p = [`imageView2/${mode}`];
  if (width) p.push(`/w/${width}`);
  if (height) p.push(`/h/${height}`);
  if (format) p.push(`/format/${format}`);
  if (quality) p.push(`/q/${quality}${force ? '!' : ''}`);
  if (relativeQuality) p.push(`/rq/${relativeQuality}`);
  if (lowestQuality) p.push(`/lq/${lowestQuality}`);
  return p.join('');
}

/**
 * CDN 图片处理（官网通用 Image 组件同款，imageMogr2 语法）
 *
 * 官网做法：按**组件实际渲染尺寸 × DPR** 向 CDN 要缩略图，而不是加载原图。
 * 素材中心实测取值：列表卡片 height=116、选择器 118×118、商品列表 width=64。
 *
 * 短路规则（与官网一致）：URL 里已经有 `?` 就不再拼参数，避免重复处理。
 */
const CDN_IMAGE = {
  /**
   * 缩略图：?imageMogr2/thumbnail/{w}x{h}（只给一边也行）
   * @example CDN_IMAGE.thumb(url, { height: 116 })        → ...?imageMogr2/thumbnail/x116
   * @example CDN_IMAGE.thumb(url, { width: 118, height: 118, dpr: 2 })
   */
  thumb(url, opts = {}) {
    if (!url || url.indexOf('?') > -1) return url;
    const dpr = opts.dpr || 1;
    const w = opts.width ? Math.floor(opts.width * dpr) : 0;
    const h = opts.height ? Math.floor(opts.height * dpr) : 0;
    if (!w && !h) return url;
    if (w && h) return `${url}?imageMogr2/thumbnail/${w}x${h}`;
    return w ? `${url}?imageMogr2/thumbnail/${w}x` : `${url}?imageMogr2/thumbnail/x${h}`;
  },

  /**
   * 按显示尺寸居中裁剪：?imageMogr2/crop/{w}x{h}/gravity/center
   */
  crop(url, opts = {}) {
    if (!url || url.indexOf('?') > -1) return url;
    const dpr = opts.dpr || 1;
    const w = opts.width ? Math.floor(opts.width * dpr) : 0;
    const h = opts.height ? Math.floor(opts.height * dpr) : 0;
    if (!w || !h) return url;
    return `${url}?imageMogr2/crop/${w}x${h}/gravity/center`;
  },

  /**
   * 按坐标抠图：?imageMogr2/cut/{w}x{h}x{dx}x{dy}
   * ⚠️ 官网这条会先丢掉原 URL 上的 query（split('?')[0]）
   */
  cut(url, opts = {}) {
    if (!url) return url;
    const { width, height, dx = 0, dy = 0 } = opts;
    if (!width || !height) return url;
    return `${url.split('?')[0]}?imageMogr2/cut/${Math.round(width)}x${Math.round(height)}x${Math.round(dx)}x${Math.round(dy)}`;
  },
};

/** uploadStatus 枚举 */
const UPLOAD_STATUS = {
  PARSING: 1,          // 转码中（视频）
  FAIL: 2,             // 失败
  AVAILABLE: 3,        // 可用
  TRANSFER_FAIL: 4,    // 转存失败
  // 5 出现在视频轮询的 uploadStatusList 里（[5,1,2]），含义未确认
  UNKNOWN_5: 5,
};

/** 轮播图硬性规格（素材中心 pageQuery 用的过滤参数）*/
/**
 * 轮播图筛选参数（素材中心 pageQuery 的过滤条件）
 *
 * ⚠️ 这里的分辨率是「推荐尺寸」，不是上传硬门槛；真实限制见 UPLOAD_LIMITS。
 * ⚠️ maxMaterialSize 原写 2MB 是错的：官网前端拦的是 5MB（5242880），
 *    见 _pm_5719 商品图片上传 beforeUpload / _pm_9255 本地素材上传。
 */
const CAROUSEL_SPEC = {
  minMaterialWidth: 1340,
  minMaterialHeight: 1785,
  maxMaterialSize: 5242880,   // 5 MB（官网前端硬拦值）
  proportion: '3:4',
};

/**
 * 素材图（materialImgUrl）硬性规格
 * ⚠️ 和轮播图不同：1:1，≥800x800
 */
const MATERIAL_SPEC = {
  minMaterialWidth: 800,
  minMaterialHeight: 800,
  maxMaterialSize: 5242880,   // 5 MB（同上）
  proportion: '1:1',
};

/**
 * 图片规格（官网校验函数 L() 的等价实现）
 *
 * 官网判据来自 _mat_4332 的 L(item, spec)：
 *   spec = { maxSize(MB), minWidth, minHeight, maxWidth, maxHeight,
 *            ratios: [[3,4]] / [[1,1]], ratiosFloat(像素容差),
 *            widthHeightTuples: [{width,height}], minWidthHeightRatio: [a,b], accepts: [".jpg"] }
 *
 * ⚠️ 比例容差是「像素」不是百分比（官网 ratiosFloat 默认 0）。
 * ⚠️ 超体积官网是「先调 /material/render/compress 压缩再校验」，不是直接拒绝。
 */
const UPLOAD_LIMITS = {
  // 商品页本地图的硬门槛（_pm_5719 beforeUpload 实测常量）
  maxImageSize: 5242880,      // 5 MB
  minImageSide: 600,          // 短边 ≥600px

  // 比例容差：官网 _cdn_9255 的 tn() 实现 ——
  //   Math.abs(height - width / ratio) <= (ratio === 3/4 ? 10 : 0)
  // 即 **3:4 容差 10 像素，其它比例 0 像素**（不是统一值，也不是百分比）。
  ratioFloat: 10,             // 仅 3:4 使用
  ratioFloatOther: 0,

  carousel: { proportion: "3:4", ratio: [3, 4], recommended: "1340x1785" },
  material: { proportion: "1:1", ratio: [1, 1], recommended: "800x800" },

  // 详情图（limitRule === oP.GoodsDetail）的固定规格，来自 _cdn_9255：
  //   kt=480 It=480 Tt=3145728 At=0.5 St=2
  goodsDetail: {
    minWidth: 480,
    minHeight: 480,
    maxSize: 3,               // MB（官网 Tt=3145728 字节）
    ratioRange: [0.5, 2],     // width/height 必须在 0.5 ~ 2 之间（1:2 ~ 2:1）
  },

  // 官网支持的比例标签（Ct 映射）
  proportions: { "1:1": [1, 1], "1:3": [1, 3], "2:1": [2, 1], "3:4": [3, 4], "4:1": [4, 1] },
};

/** 按 URL 后缀判断格式是否在白名单里（对应官网 accepts 检查） */
function _extOk(url, accepts) {
  if (!url || !accepts || !accepts.length) return true;
  const clean = String(url).split(/[?#]/)[0].toLowerCase();
  return accepts.some((x) => clean.endsWith(String(x).toLowerCase()));
}

/**
 * 上传前自检（离线，复刻官网 L()）
 *
 * @param {object} o
 *   width / height / size(字节) / url        被检测的图
 *   kind      "carousel"(3:4) | "material"(1:1)，给默认 spec
 *   spec      完整 spec，优先级高于 kind
 *              { maxSize(MB), minWidth, minHeight, maxWidth, maxHeight,
 *                ratios, ratiosFloat, widthHeightTuples, minWidthHeightRatio, accepts }
 * @returns {{ ok:boolean, reasons:string[] }}
 */
function validateImage(o = {}) {
  const w = Number(o.width) || 0;
  const h = Number(o.height) || 0;
  const size = Number(o.size) || 0;
  const reasons = [];

  const spec = o.spec || (o.kind === "material"
    ? { ratios: [UPLOAD_LIMITS.material.ratio] }
    : o.kind === "carousel"
      ? { ratios: [UPLOAD_LIMITS.carousel.ratio] }
      : null);

  // ① 格式白名单
  if (spec && spec.accepts && !_extOk(o.url, spec.accepts)) {
    reasons.push("格式需为 " + spec.accepts.join("、"));
  }

  // ② 体积（maxSize 单位 MB；不给 spec 时用 5MB 硬门槛）
  const maxMB = spec && spec.maxSize != null ? Number(spec.maxSize) : UPLOAD_LIMITS.maxImageSize / 1048576;
  if (size && size > maxMB * 1048576) {
    reasons.push("体积 " + (size / 1048576).toFixed(2) + "MB 超过上限 " + maxMB + "MB");
  }

  // ③ 宽高上下限
  const minW = spec && spec.minWidth != null ? Number(spec.minWidth) : UPLOAD_LIMITS.minImageSide;
  const minH = spec && spec.minHeight != null ? Number(spec.minHeight) : UPLOAD_LIMITS.minImageSide;
  if (w && minW && w < minW) reasons.push("宽度 " + w + " 小于最小 " + minW);
  if (h && minH && h < minH) reasons.push("高度 " + h + " 小于最小 " + minH);
  if (spec && spec.maxWidth && w && w > spec.maxWidth) reasons.push("宽度 " + w + " 超过最大 " + spec.maxWidth);
  if (spec && spec.maxHeight && h && h > spec.maxHeight) reasons.push("高度 " + h + " 超过最大 " + spec.maxHeight);

  // ④ 比例（像素容差：3:4 用 10px，其它用 0）
  if (spec && spec.ratios && w && h) {
    const list = Array.isArray(spec.ratios[0]) ? spec.ratios : [spec.ratios];
    const okAny = list.some((r) => {
      const ratio = r[0] / r[1];
      const tol = spec.ratiosFloat != null
        ? Number(spec.ratiosFloat)
        : (Math.abs(ratio - 3 / 4) < 1e-9 ? UPLOAD_LIMITS.ratioFloat : UPLOAD_LIMITS.ratioFloatOther);
      return Math.abs(w / ratio - h) <= tol;
    });
    if (!okAny) {
      reasons.push("比例 " + w + "x" + h + " 不符合 " +
        list.map((r) => r[0] + ":" + r[1]).join("、") +
        "（像素容差：3:4 ±" + UPLOAD_LIMITS.ratioFloat + "，其它 ±0）");
    }
  }

  // ④' 宽高比区间（详情图：0.5 ~ 2）
  if (spec && Array.isArray(spec.ratioRange) && spec.ratioRange.length === 2 && w && h) {
    const r = w / h;
    const [lo, hi] = spec.ratioRange.map(Number);
    if (r < lo || r > hi) {
      reasons.push("宽高比 " + r.toFixed(3) + " 超出允许区间 " + lo + " ~ " + hi);
    }
  }

  // ⑤ 精确尺寸白名单
  if (spec && spec.widthHeightTuples && spec.widthHeightTuples.length && w && h) {
    const hit = spec.widthHeightTuples.some((t) => Number(t.width) === w && Number(t.height) === h);
    if (!hit) {
      reasons.push("尺寸需为 " + spec.widthHeightTuples.map((t) => t.width + "x" + t.height).join(" 或 "));
    }
  }

  // ⑥ 宽高比下限：width/height >= a/b
  if (spec && Array.isArray(spec.minWidthHeightRatio) && spec.minWidthHeightRatio.length === 2 && w && h) {
    const a = Number(spec.minWidthHeightRatio[0]);
    const b = Number(spec.minWidthHeightRatio[1]);
    if (a > 0 && b > 0 && w / h < a / b) {
      reasons.push("宽高比 " + (w / h).toFixed(3) + " 低于下限 " + a + ":" + b);
    }
  }

  return { ok: reasons.length === 0, reasons };
}

/**
 * 发 multipart 请求（零依赖，不走 GloClient 的 JSON 通道）
 */
function postMultipart(pathname, headers, fields, fileField, fileBuf, fileName, mime) {
  return new Promise((resolve, reject) => {
    const boundary = '----TemuOpsBoundary' + Math.random().toString(36).slice(2, 18);
    const parts = [];
    for (const [k, v] of Object.entries(fields)) {
      if (v === undefined || v === null) continue;
      parts.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="${k}"\r\n\r\n${v}\r\n`));
    }
    parts.push(Buffer.from(
      `--${boundary}\r\nContent-Disposition: form-data; name="${fileField}"; filename="${fileName}"\r\n` +
      `Content-Type: ${mime || 'image/jpeg'}\r\n\r\n`
    ));
    parts.push(fileBuf);
    parts.push(Buffer.from(`\r\n--${boundary}--\r\n`));
    const body = Buffer.concat(parts);

    const req = https.request({
      hostname: HOST,
      port: 443,
      path: pathname,
      method: 'POST',
      timeout: 90000,
      headers: Object.assign({}, headers, {
        'Content-Type': `multipart/form-data; boundary=${boundary}`,
        'Content-Length': String(body.length),
      }),
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        let json = null;
        try { json = JSON.parse(text); } catch (e) { /* 非 JSON 原样返回 */ }
        if (res.statusCode !== 200 || !json) {
          return reject(new Error(`上传失败 HTTP ${res.statusCode}: ${text.slice(0, 200)}`));
        }
        resolve(json);
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(new Error('上传超时')); });
    req.write(body);
    req.end();
  });
}

class Material {
  /**
   * @param {GloClient} glo
   */
  constructor(glo) {
    this.glo = glo;
  }

  // ==================== 查询 ====================

  /**
   * 查素材列表
   * @param {object} opts
   *   pageNo / pageSize   分页，默认 1 / 50
   *   name                素材名模糊搜索
   *   type                1=图片 2=视频，默认 1
   *   folderId            文件夹，0=全部
   *   proportion          比例过滤，如 '3:4' / '1:1'
   *   carousel            true=只查轮播图可用的（1340x1785 / 2MB / 3:4）
   *   uploadStatusList    默认 [3]（可用）
   * @returns {{ total:number, list:object[] }}
   */
  async pageQuery(opts = {}) {
    const body = {
      pageInfo: {
        pageNo: opts.pageNo || 1,
        pageSize: opts.pageSize || 50,
      },
      uploadStatusList: opts.uploadStatusList || [UPLOAD_STATUS.AVAILABLE],
      folderId: opts.folderId || 0,
      // ⚠️ 视频是 0，不能用 || 兜底
      materialTypeList: [opts.type !== undefined ? opts.type : MATERIAL_TYPE.IMAGE],
    };

    if (opts.name) body.materialNameLike = opts.name;

    if (opts.carousel) {
      Object.assign(body, CAROUSEL_SPEC, { proportionList: [CAROUSEL_SPEC.proportion] });
    } else if (opts.material) {
      Object.assign(body, MATERIAL_SPEC, { proportionList: [MATERIAL_SPEC.proportion] });
    } else if (opts.proportion) {
      body.proportion = opts.proportion;
      body.proportionList = [opts.proportion];
    }

    const r = await this.glo.api('POST', '/phoenix-mms/material/pageQuery', body);
    if (String(r.code) !== '1000000') {
      throw new Error(`[material.pageQuery] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    const res = r.body.result || {};
    return { total: res.total || 0, list: res.materialList || [] };
  }

  /** 按 MD5 查重（命中说明已上传过）*/
  async queryByMd5(md5List, materialType = MATERIAL_TYPE.IMAGE) {
    const r = await this.glo.api('POST', '/phoenix-mms/material/queryByMd5', {
      materialType,
      md5IdList: Array.isArray(md5List) ? md5List : [md5List],
    });
    if (String(r.code) !== '1000000') {
      throw new Error(`[material.queryByMd5] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    return ((r.body.result || {}).responseDetailList) || [];
  }

  // ==================== 上传 ====================

  /** 取上传签名（素材库专用桶）*/
  async signature() {
    const r = await this.glo.api(
      'POST',
      '/general_auth/get_signature?tag_name=product-material-tag&scene_id=agent-seller',
      { bucket_tag: 'product-material-tag' }
    );
    const sig = (r.body && r.body.signature) || '';
    if (!sig) throw new Error('[material.signature] 未取到签名');
    return sig;
  }

  /**
   * 上传字节到 COS（不登记素材库）
   * @returns {{ url, width, height, size, etag }}
   */
  async storeImage(buf, fileName, opts = {}) {
    const sig = await this.signature();
    const mime = /\.png$/i.test(fileName) ? 'image/png' : 'image/jpeg';

    const fields = {
      url_width_height: 'true',
      filename: fileName,
      // ⚠️ 字段名必须是 upload_sign，写成 signature 会报「The signature has expired」
      upload_sign: sig,
    };
    // pic_operations：上传时让服务端直接按规则处理（裁切/限宽高/圆角…）
    //   { original_needed, rules: ['/cut/800x800x0x0', ...] }
    const rules = [];
    if (opts.crop) rules.push(CUT_RULE.cut(opts.crop));
    if (Array.isArray(opts.rules)) rules.push(...opts.rules);
    if (opts.keepOriginal !== false || rules.length) {
      fields.pic_operations = JSON.stringify({
        original_needed: opts.keepOriginal !== false,
        rules,
      });
    }

    return await postMultipart('/api/galerie/v3/store_image', {
      'User-Agent': this.glo.userAgent || 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/157.0.0.0 Safari/537.36',
      'Cookie': this.glo.gloJar.str(),
      'mallid': String(this.glo.mallId),
      'Origin': `https://${HOST}`,
      'Referer': `https://${HOST}/goods/edit`,
      'Accept': '*/*',
    }, fields, 'image', buf, fileName, mime);
  }

  /**
   * 服务端裁剪：让服务端把某张 CDN 图按 cutConfig 裁成新图，返回新 URL
   *
   *   POST /temu-avi/image-crop
   *   { url, cutConfig: { dx, dy, width, height }, extendConfig }
   *   → { url, width, height, size }
   *
   * ⚠️ 若 cutConfig 与原图完全一致（dx=dy=0 且宽高相同），前端不会调这个接口，直接用原 URL。
   *
   * @param {object} opts
   *   url           源图 URL（必须是已上传的 CDN 地址）
   *   cutConfig     { width, height, dx=0, dy=0 }
   *   extendConfig  { maxSize?, ... } 额外校验/限制
   */
  async cropImage(opts = {}) {
    const url = opts.url;
    const cut = opts.cutConfig || {};
    if (!url) throw new Error('[material.cropImage] 需要 url');
    if (!cut.width || !cut.height) throw new Error('[material.cropImage] cutConfig 需要 width / height');

    const r = await this.glo.api('POST', '/temu-avi/image-crop', {
      url,
      cutConfig: {
        dx: cut.dx || 0,
        dy: cut.dy || 0,
        width: cut.width,
        height: cut.height,
        ...cut,
      },
      extendConfig: opts.extendConfig || {},
    });
    const body = r.body || {};
    const out = body.result || body;
    if (!out || !out.url) {
      throw new Error(`[material.cropImage] 裁剪失败: ${String(r.raw || r.code).slice(0, 200)}`);
    }
    return out;   // { url, width, height, size }
  }

  /**
   * 服务端压缩：图片超限时压到阈值内（前端在 maxSize 超限时自动调）
   *
   *   POST /phoenix-mms/material/render/compress
   *   { requestItemList: [{ originImageUrl }] }
   *   → { compressMap: { 原url: 新url } }
   *
   * @param {string|string[]} urls
   * @returns {{compressMap:object, raw:object}}
   */
  async compressImage(urls) {
    const list = (Array.isArray(urls) ? urls : [urls]).filter(Boolean);
    if (!list.length) throw new Error('[material.compressImage] 需要至少一个 url');
    const r = await this.glo.api('POST', '/phoenix-mms/material/render/compress', {
      requestItemList: list.map((u) => ({ originImageUrl: u })),
    });
    const body = r.body || {};
    const out = body.result || body;
    if (!out || !out.compressMap) {
      throw new Error(`[material.compressImage] 压缩失败: ${String(r.raw || r.code).slice(0, 200)}`);
    }
    return { compressMap: out.compressMap, raw: out };
  }

  /**
   * CDN 实时裁剪/缩放 URL（不调接口、不落盘，改 URL 就出图）
   *
   *   cdnImageUrl(url, IMAGE_VIEW_MODE.LIMIT_MIN_AND_CROP, { width: 800, height: 800 })
   *   → https://img.kwcdn.com/xxx.jpg?imageView2/1/w/800/h/800
   *
   * ⚠️ 带 query 的 URL 提交商品时会被校验拒（2000346 Image suffix validation failed），
   *    要落成干净地址仍需 materialImageFrom() 下载重传。
   */
  cdnImageUrl(url, mode = IMAGE_VIEW_MODE.LIMIT_MIN_AND_CROP, opts = {}) {
    if (!url) throw new Error('[material.cdnImageUrl] 需要 url');
    const rule = imageViewRule(mode, opts);
    return url + (url.includes('?') ? '&' : '?') + rule;
  }

  /**
   * 列表用的缩略图 URL（官网同款，见 CDN_IMAGE.thumb）
   * 素材中心列表建议 { height: 116 }；选择器 { width: 118, height: 118 }；商品行 { width: 64 }
   */
  thumbUrl(url, opts = {}) {
    return CDN_IMAGE.thumb(url, opts);
  }

  /** 按显示尺寸居中裁剪的 URL（官网同款 CDN_IMAGE.crop）*/
  cropUrl(url, opts = {}) {
    return CDN_IMAGE.crop(url, opts);
  }

  /**
   * 上传图片并登记进素材库（完整链路）
   * @param {Buffer} buf        图片字节
   * @param {string} fileName   文件名（用于推断 mime 和默认素材名）
   * @param {object} opts
   *   name        素材名（默认用文件名）
   *   skipCache   true=不查重，强制上传
   * @returns {{ imgUrl, materialId, md5, width, height, size, fromCache }}
   */
  async upload(buf, fileName, opts = {}) {
    const md5 = crypto.createHash('md5').update(buf).digest('hex');

    // 1. 查重（有延迟，命中算赚）
    if (!opts.skipCache) {
      try {
        const hits = await this.queryByMd5(md5);
        if (hits.length && hits[0].imgUrl) {
          return {
            imgUrl: hits[0].imgUrl,
            materialId: hits[0].id,
            md5,
            fromCache: true,
          };
        }
      } catch (e) { /* 查重失败不影响主流程 */ }
    }

    // 2. 上传
    const up = await this.storeImage(buf, fileName, opts);

    // 3. 登记原图素材（tag=0）
    const c = await this.glo.api('POST', '/phoenix-mms/material/create', {
      folderId: opts.folderId || 0,
      materialProcessingTag: 0,
      createDetailList: [{
        materialType: MATERIAL_TYPE.IMAGE,
        materialMd5: up.etag || md5,
        materialName: (opts.name || fileName.replace(/\.[^.]+$/, '')) + '-' + Date.now().toString().slice(-6),
        imgUrl: up.url,
      }],
    });

    const detail = ((((c.body || {}).result) || {}).responseDetailList || [])[0] || {};
    return {
      imgUrl: up.url,
      materialId: detail.id,
      md5: up.etag || md5,
      width: up.width,
      height: up.height,
      size: up.size,
      fromCache: false,
    };
  }

  /**
   * 建装饰图（tag=3，需要 unadornedMaterialId）
   * @param {number} unadornedMaterialId  原图素材的 id
   */
  async createDecorated({ unadornedMaterialId, md5, imgUrl, name, folderId = 0 }) {
    const r = await this.glo.api('POST', '/phoenix-mms/material/create', {
      folderId,
      materialProcessingTag: 3,
      createDetailList: [{
        materialType: MATERIAL_TYPE.IMAGE,
        materialMd5: md5,
        materialName: (name || 'decorated') + '-' + Date.now().toString().slice(-6),
        unadornedMaterialId,
        imgUrl,
      }],
    });
    return {
      ok: String(r.code) === '1000000',
      msg: (r.body && r.body.errorMsg) || '',
      result: (r.body && r.body.result) || null,
    };
  }

  // ==================== 修改 ====================

  /**
   * 重命名素材
   * ⚠️ 会重新处理图片：返回的 imgUrl 与传入的不同，商品里引用旧 URL 的要更新
   * @returns {{ ok:boolean, newImgUrl:string, uploadStatus:number }}
   */
  async rename(id, imgUrl, newName, materialType = MATERIAL_TYPE.IMAGE) {
    const r = await this.glo.api('POST', '/phoenix-mms/material/edit', {
      id,
      url: imgUrl,
      materialName: newName,
      uploadStatus: UPLOAD_STATUS.AVAILABLE,
      materialType,
    });
    const ok = String(r.code) === '1000000';
    const res = (r.body && r.body.result) || {};
    return {
      ok,
      msg: ok ? '' : ((r.body && r.body.errorMsg) || String(r.code)),
      newImgUrl: res.imgUrl || imgUrl,
      uploadStatus: res.uploadStatus,
    };
  }

  /**
   * 删除素材（服务端叫 batchOffline）
   * ⚠️ 只传非空的那个列表；传空数组会报「个数必须在1和100之间」
   * @param {object} opts { imgIds: number[], videoIds: number[] }
   */
  async remove({ imgIds = [], videoIds = [] } = {}) {
    const body = {};
    if (imgIds.length) body.imgIdList = imgIds;
    if (videoIds.length) body.videoIdList = videoIds;

    if (!body.imgIdList && !body.videoIdList) {
      throw new Error('[material.remove] 至少给一个 imgIds 或 videoIds');
    }
    if (imgIds.length > 100 || videoIds.length > 100) {
      throw new Error('[material.remove] 单次最多 100 个');
    }

    const r = await this.glo.api('POST', '/phoenix-mms/material/batchOffline', body);
    return {
      ok: String(r.code) === '1000000',
      msg: (r.body && r.body.errorMsg) || '',
    };
  }

  // ==================== 文件夹 ====================

  /** 取文件夹树根节点（childFolderList 递归）*/
  async folderTree() {
    const r = await this.glo.api('POST', '/phoenix-mms/material/folder/query', {});
    if (String(r.code) !== '1000000') {
      throw new Error(`[material.folderTree] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    return ((r.body.result) || {}).rootFolder || null;
  }

  /**
   * 新建文件夹
   * ⚠️ 参数是 parentId，不是 parentFolderId
   * ⚠️ 带 folderId 不会改名，只会再建一个
   */
  async folderCreate(folderName, parentId = 0) {
    const r = await this.glo.api('POST', '/phoenix-mms/material/folder/create', {
      folderName,
      parentId,
    });
    return {
      ok: String(r.code) === '1000000',
      msg: (r.body && r.body.errorMsg) || '',
    };
  }

  /** 删除文件夹（服务端叫 offline）*/
  async folderDelete(folderId) {
    const r = await this.glo.api('POST', '/phoenix-mms/material/folder/offline', { folderId });
    return {
      ok: String(r.code) === '1000000',
      msg: (r.body && r.body.errorMsg) || '',
    };
  }

  // ==================== 便利方法 ====================

  /**
   * 取 N 张可用的轮播图 URL（3:4，≥1340x1785）
   * @param {number} n 数量（默认 5）
   */
  async carouselImages(n = 5) {
    const { list } = await this.pageQuery({ carousel: true, pageSize: n });
    if (list.length < n) {
      throw new Error(`[material.carouselImages] 可用轮播图只有 ${list.length} 张，需要 ${n} 张`);
    }
    return list.slice(0, n).map((m) => m.imgUrl);
  }

  /**
   * 取 1 张可用的素材图 URL（1:1，≥800x800）
   * @param {number} pageNo 换一张用（默认 1）
   */
  async materialImage(pageNo = 1) {
    const { total, list } = await this.pageQuery({ material: true, pageNo });
    if (!list.length) {
      throw new Error(`[material.materialImage] 没有可用的 1:1 素材图（共 ${total} 张符合规格）`);
    }
    return list[0].imgUrl;
  }

  /**
   * 查视频素材
   *
   * 视频素材字段（和图片不同）：
   *   materialType      0（视频）
   *   materialFormat    'MP4'
   *   imgUrl            null          ← 视频没有 imgUrl
   *   proportion        null          ← 视频没有比例
   *   **destMaterialUrl** 转码后视频 URL  ← 提交商品用这个
   *   originalMaterialUrl 原视频 URL
   *   firstImageUrl     封面首帧图
   *   materialDuration  时长（毫秒）
   *   vid               视频素材 ID（goods-video-tag#...）
   *   width / height    画面尺寸
   *
   * @param {object} opts { pageNo, pageSize, name }
   * @returns {{ total:number, list:Array }}
   */
  async videoQuery(opts = {}) {
    const body = {
      pageInfo: {
        pageNo: opts.pageNo || 1,
        pageSize: opts.pageSize || 50,
      },
      uploadStatusList: opts.uploadStatusList || [UPLOAD_STATUS.AVAILABLE],
      folderId: opts.folderId || 0,
      materialTypeList: [MATERIAL_TYPE.VIDEO],
    };
    if (opts.name) body.materialNameLike = opts.name;

    const r = await this.glo.api('POST', '/phoenix-mms/material/pageQuery', body);
    if (String(r.code) !== '1000000') {
      throw new Error(`[material.videoQuery] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    const res = (r.body.result) || {};

    const list = (res.materialList || []).map((v) => ({
      id: v.id,
      materialName: v.materialName,
      // 提交商品用的 URL
      videoUrl: v.destMaterialUrl,
      originalUrl: v.originalMaterialUrl,
      coverUrl: v.firstImageUrl,
      duration: v.materialDuration,      // 毫秒
      durationSec: v.materialDuration ? Math.round(v.materialDuration / 100) / 10 : null,
      vid: v.vid,
      width: v.width,
      height: v.height,
      size: v.materialSize,
      format: v.materialFormat,
      uploadStatus: v.uploadStatus,
      productBindCount: v.productBindCount,
      uploadTime: v.uploadTime,
    }));

    return { total: res.total || list.length, list };
  }

  /** 取 1 条可用的视频素材 */
  async videoMaterial(pageNo = 1) {
    const { total, list } = await this.videoQuery({ pageNo });
    if (!list.length) {
      throw new Error(`[material.videoMaterial] 没有可用的视频素材（共 ${total} 条）`);
    }
    return list[0];
  }

  /**
   * 视频素材 → 商品请求体里的 productCarouseVideoReqList 项
   *
   * 服务端要求的字段（从代码反查）：
   *   { videoType:1, vid, coverUrl, videoUrl, width, height }
   *
   * @param {object} v videoQuery() 返回的项，或素材库原始项
   */
  toVideoReq(v) {
    if (!v) throw new Error('[material.toVideoReq] 需要视频素材');
    // ⚠️ 只能是转码后的 destMaterialUrl；originalMaterialUrl 是原片，提进 product/add 用不了
    const videoUrl = v.videoUrl || v.destMaterialUrl;
    if (!v.vid) {
      throw new Error('[material.toVideoReq] 缺 vid —— 视频还没转码完成（uploadStatus≠3），用 waitVideoReady 等');
    }
    if (!videoUrl) {
      throw new Error('[material.toVideoReq] 缺 destMaterialUrl —— 不要用 originalMaterialUrl 提交');
    }
    return {
      videoType: 1,
      vid: v.vid,
      coverUrl: v.coverUrl || v.firstImageUrl || '',
      videoUrl,
      width: v.width || null,
      height: v.height || null,
    };
  }

  // ==================== 视频上传（四步）====================

  /**
   * ① 视频上传签名
   * ⚠️ 与图片不同：视频的 tag_name 是 `goods-video-tag`（图片是 `product-material-tag`）
   */
  async videoSignature() {
    const r = await this.glo.api(
      'POST',
      '/general_auth/get_signature?tag_name=goods-video-tag&scene_id=agent-seller',
      { bucket_tag: 'goods-video-tag' }
    );
    const sig = (r.body && r.body.signature) || '';
    if (!sig) throw new Error('[material.videoSignature] 未取到视频签名');
    return sig;
  }

  /**
   * ② 预建视频素材记录（拿到 id，此时还没有 URL）
   * @returns {{ id:number, exists:boolean }}
   */
  async createVideoMaterial(md5, name, opts = {}) {
    const r = await this.glo.api('POST', '/phoenix-mms/material/create', {
      folderId: opts.folderId || 0,
      createDetailList: [{
        materialType: MATERIAL_TYPE.VIDEO,        // 0
        materialMd5: md5,
        materialName: name,
      }],
    });
    if (String(r.code) !== '1000000') {
      throw new Error(`[material.createVideoMaterial] ${(r.body && r.body.errorMsg) || r.code}`);
    }
    const d = ((((r.body || {}).result) || {}).responseDetailList || [])[0] || {};
    return { id: d.id, exists: !!d.alreadyExists };
  }

  /**
   * ③ 上传完成后回写 URL（uploadStatus=1 表示转码中）
   * @param {object} opts { id, url, name }
   */
  async registerVideoUrl(opts) {
    const r = await this.glo.api('POST', '/phoenix-mms/material/edit', {
      id: opts.id,
      url: opts.url,
      materialName: opts.name,
      uploadStatus: UPLOAD_STATUS.PARSING,      // 1 = 转码中
      materialType: MATERIAL_TYPE.VIDEO,
    });
    return {
      ok: String(r.code) === '1000000',
      code: r.code,
      msg: (r.body && r.body.errorMsg) || '',
    };
  }

  /**
   * ④ 轮询等转码完成
   * @param {number} id 素材 id
   * @param {object} opts { timeoutMs, intervalMs }
   * @returns {object|null} 转码完成后返回完整素材项
   */
  async waitVideoReady(id, opts = {}) {
    const timeout = opts.timeoutMs || 180000;
    const interval = opts.intervalMs || 3000;
    const start = Date.now();

    while (Date.now() - start < timeout) {
      // 抓包里用的 uploadStatusList 是 [5,1,2]
      const r = await this.glo.api('POST', '/phoenix-mms/material/pageQuery', {
        pageInfo: { pageNo: 1, pageSize: 100 },
        uploadStatusList: [5, UPLOAD_STATUS.PARSING, UPLOAD_STATUS.FAIL],
        folderId: 0,
        materialTypeList: [MATERIAL_TYPE.VIDEO],
      });
      const list = (((r.body || {}).result) || {}).materialList || [];
      const hit = list.find((x) => x.id === id);
      if (hit && hit.uploadStatus === UPLOAD_STATUS.AVAILABLE) {
        return this.toVideoReq({
          vid: hit.vid,
          coverUrl: hit.firstImageUrl,
          videoUrl: hit.destMaterialUrl,
          width: hit.width,
          height: hit.height,
        });
      }
      if (hit && hit.uploadStatus === UPLOAD_STATUS.FAIL) {
        throw new Error(`[material.waitVideoReady] 转码失败: ${hit.failReason || ''}`);
      }
      await new Promise((s) => setTimeout(s, interval));
    }
    throw new Error(`[material.waitVideoReady] 等待超时（${timeout}ms）`);
  }

  /**
   * 视频上传（完整链路）
   *
   * 前端的实现（newon_4689 上传器，已按源码 1:1 还原）：
   *   ① get_signature?tag_name=goods-video-tag        → { signature }（644 字符）
   *   ② ≤5MB：POST /api/galerie/v1/store_video        multipart {create_media:true, upload_sign} + file
   *      >5MB：POST /api/galerie/large_file/v1/video/upload_init
   *             JSON {content_type, create_media:true, sign} → { sign }（新令牌）
   *             POST .../upload_part  multipart {sign, part_num} + part_file → {uploaded_part_num}
   *             POST .../upload_complete JSON {sign, large_file_size, content_md5} → { url }
   *   ③ 素材记录：服务端 create_media:true 会自己建；没建则本地预建 + material/edit 回写
   *   ④ 轮询 pageQuery uploadStatusList:[5,1,2] 直到 uploadStatus=3（可用）
   *
   * @param {Buffer} buf 视频字节
   * @param {string} fileName
   * @param {object} opts
   *   name            素材名（默认文件名去扩展名）
   *   folderId        素材目录
   *   chunkSize       分片大小（默认 5MB）
   *   splitThreshold  超过多少字节走分片（默认 5MB）
   *   onProgress      ({part,total,uploaded}) => void
   *   register        false = 只上传字节，不建/不回写素材记录
   *   wait            false = 不等转码（返回 url 即返回）
   */
  async uploadVideo(buf, fileName, opts = {}) {
    const md5 = crypto.createHash('md5').update(buf).digest('hex');
    const name = opts.name || fileName.replace(/\.[^.]+$/, '');

    // 1. 查重（视频查重命中率低，但先试）
    if (!opts.skipCache) {
      try {
        const hits = await this.queryByMd5(md5, MATERIAL_TYPE.VIDEO);
        if (hits.length && hits[0].id) {
          const ready = await this.waitVideoReady(hits[0].id, opts).catch(() => null);
          if (ready) return Object.assign({ materialId: hits[0].id, md5, fromCache: true }, ready);
        }
      } catch (e) { /* 查重失败不影响主流程 */ }
    }

    // 2. 预建素材记录（UI 的真实顺序：先占位，拿到 id 后再上传、再回写）
    let id = null;
    if (opts.preCreate !== false) {
      try {
        const pre = await this.createVideoMaterial(md5, name, opts);
        id = pre && pre.id;
      } catch (e) {
        // 已存在同 md5 记录等情况：不阻断上传，后面靠查询兜底
        id = null;
      }
    }

    // 3. 取签名 + 上传字节
    const signature = await this.videoSignature();
    const up = await this._uploadVideoBytes(signature, buf, fileName, opts);

    const result = {
      url: up.url,
      md5,
      fromCache: false,
      split: !up.single,
      parts: up.parts || 1,
      signatureSign: up.sign || null,
    };
    if (opts.register === false) {
      result.materialId = id;
      return result;
    }

    // 4. 回写 URL（服务端若已带 id 则优先用它的）
    id = up.materialId || id;
    if (id) {
      const reg = await this.registerVideoUrl({ id, url: up.url, name });
      if (!reg.ok) throw new Error(`[material.uploadVideo] 回写 URL 失败: ${reg.msg}`);
    }
    result.materialId = id;

    // 5. 等转码
    if (opts.wait === false || !id) return result;
    const ready = await this.waitVideoReady(id, opts);
    return Object.assign(result, ready);
  }

  /**
   * 只上传视频字节（不建素材记录）
   *
   * 已验证：upload_init 真实返回 { sign }
   * 按源码实现：init → part(part_file) → complete，>5MB 自动分片
   *
   * @returns {{ url, single:boolean, parts?:number, sign?:string, materialId?:number, raw:object }}
   */
  async _uploadVideoBytes(signature, buf, fileName, opts = {}) {
    const ext = (fileName.match(/\.[^.]+$/) || [''])[0].toLowerCase();
    const mime = ext === '.mov' ? 'video/quicktime'
      : ext === '.webm' ? 'video/webm'
        : 'video/mp4';

    const headers = {
      'User-Agent': this.glo.userAgent || 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/156.0.0.0 Safari/537.36',
      'Cookie': this.glo.gloJar.str(),
      'mallid': String(this.glo.mallId),
      'Origin': `https://${HOST}`,
      'Referer': `https://${HOST}/goods/edit`,
      'Accept': '*/*',
    };

    const chunkSize = opts.chunkSize || VIDEO_CHUNK_SIZE;
    const threshold = opts.splitThreshold || VIDEO_SPLIT_THRESHOLD;

    // ---------- 小文件：单次 store_video ----------
    if (buf.length <= threshold) {
      const r = await postMultipart(VIDEO_UPLOAD_PATH.single + VIDEO_TAG, headers, {
        create_media: 'true',
        upload_sign: signature,
      }, 'file', buf, fileName, mime);
      if (!r.url) throw new Error(`[material._uploadVideoBytes] store_video 未返回 url: ${JSON.stringify(r).slice(0, 200)}`);
      return {
        url: r.url,
        single: true,
        materialId: r.id || r.material_id || null,
        raw: r,
      };
    }

    // ---------- 大文件：init → part → complete ----------
    const init = await this.glo.api('POST', VIDEO_UPLOAD_PATH.init + VIDEO_TAG, {
      content_type: mime,
      create_media: true,
      sign: signature,
    });
    const sign = (init.body || {}).sign;
    if (!sign) {
      throw new Error(`[material._uploadVideoBytes] upload_init 未返回 sign: ${String(init.raw || '').slice(0, 200)}`);
    }

    const total = Math.ceil(buf.length / chunkSize);
    const uploaded = [];
    for (let i = 0; i < total; i++) {
      const start = i * chunkSize;
      const chunk = buf.subarray(start, Math.min(start + chunkSize, buf.length));
      const r = await postMultipart(VIDEO_UPLOAD_PATH.part + VIDEO_TAG, headers, {
        sign,
        part_num: i + 1,          // 从 1 开始
      }, 'part_file', chunk, `${fileName.replace(/\.[^.]+$/, '')}-part${i + 1}${ext || '.mp4'}`, mime);
      const done = Number(r.uploaded_part_num || 0);
      if (!(done > 0)) {
        throw new Error(`[material._uploadVideoBytes] 第 ${i + 1}/${total} 片上传失败: ${JSON.stringify(r).slice(0, 200)}`);
      }
      uploaded.push(done);
      if (typeof opts.onProgress === 'function') {
        opts.onProgress({ part: i + 1, total, uploaded: done, bytes: chunk.length });
      }
    }

    const md5 = crypto.createHash('md5').update(buf).digest('hex');
    const fin = await this.glo.api('POST', VIDEO_UPLOAD_PATH.complete + VIDEO_TAG, {
      sign,
      large_file_size: buf.length,
      content_md5: md5,
    });
    const url = (fin.body || {}).url;
    if (!url) {
      throw new Error(`[material._uploadVideoBytes] upload_complete 未返回 url: ${String(fin.raw || '').slice(0, 200)}`);
    }
    return {
      url,
      single: false,
      parts: total,
      sign,
      materialId: (fin.body || {}).id || null,
      raw: fin.body,
    };
  }

  /**
   * 从任意 CDN 图生成 1:1 素材图
   *
   * 原理：Temu CDN 支持七牛式实时裁剪，直接在 URL 后加
   *   `?imageView2/1/w/800/h/800` 会返回裁好的方图。
   * 但 product/add 校验 URL 后缀，带 query 会被拒（2000346 Image suffix validation failed），
   * 所以要把裁好的字节下载下来重新上传，拿一个干净的 URL。
   *
   * @param {string} sourceUrl 轮播图 URL（3:4 也行）
   * @param {number} size      边长，默认 800
   * @returns {{ imgUrl, materialId, width, height, size }}
   */
  async materialImageFrom(sourceUrl, size = 800) {
    if (!sourceUrl) throw new Error('[material.materialImageFrom] 需要 sourceUrl');

    // 1. 让 CDN 裁成方图
    const cropUrl = sourceUrl + (sourceUrl.includes('?') ? '&' : '?') +
      `imageView2/1/w/${size}/h/${size}`;
    const buf = await this._download(cropUrl);
    if (!buf.length) throw new Error('[material.materialImageFrom] 下载裁切图失败');

    // 2. 重新上传，拿干净 URL
    const up = await this.upload(buf, `material-${size}-${Date.now()}.jpg`, { name: '素材图' });
    return up;
  }

  /** 下载 URL 到 Buffer */
  _download(url) {
    return new Promise((resolve, reject) => {
      const req = https.get(url, { headers: { 'User-Agent': 'Mozilla/5.0' }, timeout: 60000 }, (res) => {
        if (res.statusCode !== 200) {
          res.resume();
          return resolve(Buffer.alloc(0));
        }
        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => resolve(Buffer.concat(chunks)));
      });
      req.on('error', reject);
      req.on('timeout', () => { req.destroy(new Error('下载超时')); });
    });
  }
}

module.exports = {
  Material, MATERIAL_TYPE, UPLOAD_STATUS, CAROUSEL_SPEC, MATERIAL_SPEC,
  UPLOAD_LIMITS, validateImage,
  VIDEO_SPEC, VIDEO_FORMAT, VIDEO_SPLIT_THRESHOLD, VIDEO_CHUNK_SIZE, VIDEO_UPLOAD_PATH, VIDEO_TAG,
  IMAGE_VIEW_MODE, CUT_RULE, imageViewRule, CDN_IMAGE,
};
