<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Grid, Picture, Search } from '@element-plus/icons-vue'
import MaterialPicker from './MaterialPicker.vue'
import TemuCategoryPicker from './TemuCategoryPicker.vue'
import {
  createProduct,
  cropMaterial,
  describeProduct,
  getProductEnv,
  getProductSites,
  getProductTemplate,
  getCategoryPath,
  getCategoryConfig,
  getProductConstants,
  getProductRegions,
  getSizeSpecConfig,
  getSizeTemplates,
  getFormTemplate,
  listFormTemplates,
  saveFormTemplate,
  updateFormTemplate,
  deleteFormTemplate,
  validateSizeTemplate,
  searchProductCategories,
} from '../api'
import { normalizeVolumePayload, normalizeWeightPayload, volumeToPayload, weightToPayload } from '../unitConvert'
import { confirmAction, notifyError, notifySuccess, notifyWarning } from '../feedback'
import type {
  ProductCategoryPath,
  ProductDescribeResult,
  ProductRegions,
  ProductSizeTemplate,
  ProductSpecOption,
  ProductStoreEnv,
  ProductTemplateField,
  ProductTemplateInfo,
} from '../types'

/**
 * 手动创建商品。
 * 流程：类目模板 → 逐项选属性（依赖由上游模板引擎处理）→ 选图 → 填基础信息 → 预览 → 创建。
 */
const catPathText = ref('')
const sites = ref<Array<Record<string, unknown>>>([])
const env = ref<ProductStoreEnv | null>(null)
const tmpl = ref<ProductTemplateInfo | null>(null)
const desc = ref<ProductDescribeResult | null>(null)

const loadingTemplate = ref(false)
const loadingEnv = ref(false)
const loadingDescribe = ref(false)
const creating = ref(false)
const dryRun = ref(true)

const form = reactive({
  siteId: 100,
  name: '',
  enName: '',
  colorVid: null as number | null,
  sizeVids: [] as number[],
  images: [] as string[],
  materialImgUrl: '',
  skuPrice: 39.9,
  stock: 100,
  extCode: '',
  shipmentLimit: '2个工作日',
  warehouseIds: [] as string[],
  freightTemplateId: '',
  skuClassification: 1,
  pieces: 1,
  individuallyPacked: false,
})

const selected = reactive<Record<string, string | number>>({})

// ---- v1.1.3 补齐项 ----
const colorGroupId = ref<number | null>(null)
/** 尺码标准（欧美尺码 / 亚洲尺码 / 巴西尺码…） */
const sizeStandard = ref<string>('')
/** 尺码类型（sizeType 二级筛选） */
const sizeTypeId = ref<string>('')

const colorGroups = computed(() => desc.value?.colorGroups || [])

/** 模板里 extendInfo 形如 "(255,255,255,1)"，转成 CSS 颜色 */
function swatchOf(extendInfo?: string | null): string {
  if (!extendInfo) return '#e5e7eb'
  const m = /\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+))?\)/.exec(extendInfo)
  if (!m) return '#e5e7eb'
  const [, r, g, b, a] = m
  return a === undefined || a === '' ? `rgb(${r},${g},${b})` : `rgba(${r},${g},${b},${a})`
}

/** 按 vid 找色值 */
function colorSwatch(vid?: number | null): string {
  if (!vid) return '#e5e7eb'
  for (const g of colorGroups.value) {
    const hit = (g.colors || []).find((c) => c.vid === vid)
    if (hit) return swatchOf(hit.extendInfo)
  }
  return '#e5e7eb'
}

/**
 * 制造地点跟随产地国家：
 * 先按 shortName/code 精确匹配，再退回中文名前缀匹配（「中国大陆」↔「中国大陆」）。
 * 找不到就不动，保留用户手选值。
 */
function syncManufacturingCountry(country: string) {
  if (!country) return
  const list = regionsData.value?.manufacturing_countries || []
  const key = String(country).toLowerCase()
  let hit = list.find((c) => String(c.shortName || c.code).toLowerCase() === key)
  if (!hit) hit = list.find((c) => String(c.code).toLowerCase() === key)
  if (!hit) {
    // 用产地下拉里选中的中文名去匹配制造地点列表
    const picked = (regionsData.value?.origin_countries || []).find(
      (c) => String(c.shortName || c.code).toLowerCase() === key,
    )
    const cn = String(picked?.name || '').replace(/[（(].*$/, '').trim()
    if (cn) hit = list.find((c) => String(c.name) === cn)
  }
  if (hit) manufacturing.value = [String(hit.shortName || hit.code)]
}

const currentColorName = computed(() => {
  if (!form.colorVid) return ''
  for (const g of colorGroups.value) {
    const hit = (g.colors || []).find((c) => c.vid === form.colorVid)
    if (hit) return hit.value
  }
  return ''
})

const currentColors = computed(
  () => colorGroups.value.find((g) => g.groupId === colorGroupId.value)?.colors || desc.value?.colors || [],
)

/** v1.2.4：官方三层结构 标准 → 类型 → 尺码值 */
const sizeTree = computed(() => desc.value?.sizeTree || {})
const sizeStandardOptions = computed(() => Object.keys(sizeTree.value))
/** 官方类型顺序：常规 → 均码 → Plus → Tall */
const SIZE_TYPE_ORDER = ['常规', '均码', 'Plus', 'Tall']
const sizeTypeOptions = computed(() => {
  const keys = Object.keys(sizeTree.value[sizeStandard.value] || {})
  const rank = (k: string) => {
    const i = SIZE_TYPE_ORDER.indexOf(k)
    return i >= 0 ? i : SIZE_TYPE_ORDER.length
  }
  return keys.sort((a, b) => rank(a) - rank(b))
})

/** 当前层级的尺码值 */
const currentSizes = computed(
  () => sizeTree.value[sizeStandard.value]?.[sizeTypeId.value as string] || [],
)
/** 官网文案：亚洲尺码的常规/均码加「亚洲-」前缀，Tall 不变 */
function sizeTypeLabel(type: string): string {
  if (sizeStandard.value === '亚洲尺码' && type !== 'Tall') return `亚洲-${type}`
  return type
}

const sizesInGroup = computed(
  () => Object.values(sizeTree.value[sizeStandard.value] || {}).flat(),
)


const regionsData = ref<ProductRegions | null>(null)
const originCountry = ref('CN')
const originProvince = ref<number | null>(null)
const manufacturing = ref<string[]>([])

const isSensitive = ref(false)
const sensitiveList = ref<number[]>([])
const sensitiveTypes = ref<number[]>([])

const skuClassOptions = ref<Array<{ label: string; value: number }>>([
  { label: '单品', value: 1 },
  { label: '同款多件装', value: 2 },
  { label: '混合套装', value: 3 },
])
const sensitiveTypeOptions = ref<Array<{ label: string; value: number }>>([])
/** 详情图文（每张一个楼层，3:4 / 1350×1800） */
const decorationImages = ref<string[]>([])
/** 正在自动裁素材图 */
const materialCropping = ref(false)
/** 当前素材图是否由首图自动裁出（删轮播图时要跟着清） */
const materialAuto = ref(false)
/** 颜色面板开关（选完自动关） */
const colorPopVisible = ref(false)
/** 表单模板 */
const formTemplates = ref<Array<{ id: number; name: string; note: string; field_count: number }>>([])
const templateLoading = ref(false)
const saveTplVisible = ref(false)
const saveTplName = ref('')
const saveTplNote = ref('')
const savingTpl = ref(false)
const tplManageVisible = ref(false)
const editTplVisible = ref(false)
const editTplId = ref(0)
const editTplName = ref('')
const editTplNote = ref('')
const savingEditTpl = ref(false)
/** 编辑模板时的参数编辑 */
const editTplFields = ref<Array<any>>([])
const editTplSelected = reactive<Record<string, string | number>>({})
const editTplLoading = ref(false)
const editTplCatPath = ref<number[]>([])
/** 模板的完整可复用值（产地/体积/服务承诺等都在这里，不只 selected） */
/** 编辑模板用的尺码三层数据 */
const editTplSizeTree = ref<Record<string, Record<string, any[]>>>({})
const editTplSizeStandard = ref('')
const editTplSizeType = ref('')
const editTplSizeTemplates = ref<any[]>([])
const editTplSizeTplId = ref<number | null>(null)

const editTplSites = ref<Array<Record<string, unknown>>>([])
const editTplEnv = ref<any>(null)

const editTplValues = ref<Record<string, any>>({
  origin: {},
  manufacturing: [],
  volume: { len: 10, width: 9, height: 8 },
  weight: { value: 200 },
  sensitiveTypes: [],
  sensitiveList: [],
  isSensitive: false,
  skuClassification: 1,
  pieces: 2,
  individuallyPacked: false,
  skuPrice: 39.9,
  stock: 100,
  shipmentLimit: '2个工作日',
  siteId: 100,
  warehouseIds: [],
})
/** 素材语言：图片文案要适配的市场语言 */
const MATERIAL_LANGS = [
  { value: 'en', label: '英语' },
  { value: 'es', label: '西班牙语' },
  { value: 'pt', label: '葡萄牙语' },
  { value: 'fr', label: '法语' },
  { value: 'ru', label: '俄语' },
  { value: 'ar', label: '阿拉伯语' },
  { value: 'ko', label: '韩语' },
]
const materialLanguages = ref<string[]>(['en'])
/** 体积（cm）与重量（g）用户输入值；提交时换算成毫米/毫克 */
const volumeInput = reactive({ len: 10, width: 9, height: 8 })
const weightInput = ref(200)
/** 尺码表模板（官网「尺码表」下拉） */
const sizeTemplates = ref<ProductSizeTemplate[]>([])
const sizeTemplateId = ref<number | null>(null)

/** 未选尺码时，价格/库存/体积重量不可填（官网表单联动，纯前端约束） */
const hasSize = computed(() => form.sizeVids.length > 0)

const stockDialogVisible = ref(false)
const stockPerWarehouse = reactive<Record<string, number>>({})

const freightTemplates = computed(() => env.value?.freightTemplates || [])

const categoryCfg = ref<{ sensitive_types: number[]; extra_requirements: string[]; title_max_length: number } | null>(null)

const categoryPickerVisible = ref(false)
/** 当前类目路径的中文名（选中或反查得到） */
const categoryNames = ref<string[]>([])
const categoryKeyword = ref('')
const categoryCandidates = ref<ProductCategoryPath[]>([])
const searchingCategory = ref(false)

const pickerVisible = ref(false)
const decoPickerVisible = ref(false)
const pickerCarousel = ref(true)

const catPath = computed(() =>
  catPathText.value
    .split(/[>,/]/)
    .map((x) => Number(x.trim()))
    .filter((x) => Number.isFinite(x) && x > 0),
)

const leafCatId = computed(() => catPath.value[catPath.value.length - 1] || 0)
const fields = computed<ProductTemplateField[]>(() => desc.value?.fields || [])
const missing = computed<string[]>(() => desc.value?.missing || [])

/** 属性填写率（对齐官网的「属性填报率 24/28」） */
const fillRate = computed(() => {
  const total = fields.value.length
  if (!total) return { total: 0, filled: 0, percent: 0 }
  let filled = 0
  for (const f of fields.value) {
    const v = selected[f.key]
    if (v !== undefined && v !== null && String(v).trim() !== '') filled += 1
  }
  return { total, filled, percent: Math.round((filled / total) * 100) }
})
const colors = computed<ProductSpecOption[]>(() => desc.value?.colors || [])
const sizes = computed<ProductSpecOption[]>(() => desc.value?.sizes || [])

const EN_NAME_MIN = 20
const enNameTooShort = computed(
  () => !!form.enName.trim() && form.enName.trim().length < EN_NAME_MIN,
)

const canSubmit = computed(
  () =>
    siteReady.value &&
    !!tmpl.value &&
    !!form.name.trim() &&
    form.enName.trim().length >= EN_NAME_MIN &&
    catPath.value.length > 0 &&
    !!form.colorVid &&
    form.sizeVids.length > 0 &&
    form.images.length > 0 &&
    form.skuPrice > 0 &&
    missing.value.length === 0,
)

/** 未满足的提交条件（按顺序列出，用于按钮禁用时提示） */
const submitBlockers = computed(() => {
  const out: string[] = []
  if (!siteReady.value) out.push('站点未就绪（缺发货仓或运费模板）')
  if (!tmpl.value) out.push('类目模板未加载')
  if (!catPath.value.length) out.push('未选商品类目')
  if (!form.name.trim()) out.push('未填商品名称')
  if (form.enName.trim().length < EN_NAME_MIN) {
    out.push(`英文名不足 ${EN_NAME_MIN} 字符（当前 ${form.enName.trim().length}）`)
  }
  if (!form.colorVid) out.push('未选颜色')
  if (!form.sizeVids.length) out.push('未选尺码')
  if (!form.images.length) out.push('未选轮播图')
  if (!(form.skuPrice > 0)) out.push('未填申报价格')
  if (missing.value.length) out.push(`属性必填未完成：${missing.value.join('、')}`)
  return out
})

const warehouseDialogVisible = ref(false)
/** 切站点时短暂为 true：禁用发货仓下拉，避免渲染到上一站点的残留数据 */
const featureGuard = ref(false)

/** 当前站点绑定的仓库（默认全选） */
const siteWarehouseIds = computed(() => env.value?.warehouseIds || [])
/** 所有出现过的仓库（允许手动跨站选） */
/** warehouseId → 中文名（只有 sendWarehouse/list 带名称，warehouseList 只有 ID） */
const warehouseNames = computed(() => {
  const map = new Map<string, string>()
  for (const w of env.value?.warehouses || []) {
    const id = String(w.warehouseId || '').trim()
    if (id) map.set(id, w.warehouseName || '')
  }
  return map
})

function warehouseLabel(id: string) {
  const key = String(id || '').trim()
  if (!key) return '未知仓库'
  const name = warehouseNames.value.get(key)
  if (name) return name
  // 名称取不到时不把 WH-0219... 这种长串丢给用户，用尾号区分
  return `未命名仓库（${key.slice(-6)}）`
}

/**
 * 只列当前站点可用的发货仓。
 * 不要合并 warehouseRoutes（那是所有站点的关系表），否则选美国站会看到加拿大仓。
 */
/**
 * 当前站点真正可用的发货仓。
 * 只认 sendWarehouses（它按站点过滤且带中文名）；
 * 不要退回 warehouseList 的 warehouseIdList——那是路由模板的全局配置，
 * 会给澳大利亚这种没配仓库的站点返回美西三仓，让用户以为能建品。
 */
const warehouseOptions = computed(() => {
  const set = new Set<string>()
  for (const w of env.value?.warehouses || []) {
    const id = String(w.warehouseId || '').trim()
    if (id) set.add(id)
  }
  return [...set]
})

/** 该站点是否具备建品条件（有发货仓 + 有运费模板） */
const siteReady = computed(
  () => warehouseOptions.value.length > 0 && !!env.value?.freightTemplateId,
)

/** 站点不可用的具体原因（区分接口无数据 vs 该站点确实没配） */
const warehouseHint = computed(() => {
  if (!env.value) return ''
  if (env.value.warehousesError) {
    return `读取发货仓失败：${env.value.warehousesError}`
  }
  if (!warehouseOptions.value.length) {
    if ((env.value.warehousesAllCount || 0) === 0) {
      return '这个店铺没有返回任何发货仓。请先到 Temu 后台「物流设置」里配置发货仓。'
    }
    return '该站点没有绑定发货仓。请到 Temu 后台「物流设置」里把仓库关联到这个站点，或换一个站点。'
  }
  if (!env.value.freightTemplateId) {
    return '该站点没有可用的运费模板。请到 Temu 后台「物流设置」里配置运费模板。'
  }
  return ''
})

const siteBlockReason = computed(() => {
  if (!env.value) return ''
  const missing: string[] = []
  if (!warehouseOptions.value.length) missing.push('发货仓')
  if (!env.value.freightTemplateId) missing.push('运费模板')
  return missing.length ? `${missing.join(' / ')}未配置` : ''
})

function buildForm() {
  return {
    catPath: catPath.value,
    siteId: form.siteId,
    name: form.name.trim(),
    enName: form.enName.trim(),
    selected: { ...selected },
    colorVid: Number(form.colorVid || 0),
    sizeVid: form.sizeVids[0] || 0,
    sizeVids: form.sizeVids,
    images: form.images,
    materialImgUrl: form.materialImgUrl,
    skuPrice: form.skuPrice,
    stock: form.stock,
    extCode: form.extCode.trim(),
    shipmentLimit: form.shipmentLimit,
    warehouseIds: form.warehouseIds,
    freightTemplateId: form.freightTemplateId || undefined,
    skuClassification: form.skuClassification,
    pieces: form.pieces,
    individuallyPacked: form.individuallyPacked ? 1 : 0,
    origin: {
      countryShortName: originCountry.value || undefined,
      region2Id: originProvince.value || undefined,
    },
    manufacturing: manufacturing.value,
    isSensitive: isSensitive.value,
    sensitiveTypes: sensitiveTypes.value,
    sensitiveList: sensitiveList.value,
    stockPerWarehouse: { ...stockPerWarehouse },
    decorationImages: decorationImages.value,
    // 英语是平台必填语言，勾选框已禁用取消，这里再兜底一次
    materialMultiLanguages: Array.from(new Set(['en', ...materialLanguages.value])),
    // SDK 预检要靠这个字段做「模板类型 vs 当前尺码类型」比对；不传的话预检只查一半，平台才报 6000012
    generalSizeType: currentGeneralSizeType.value || undefined,
    // 尺码表：下拉里选中的只是「蓝本」，product/add 不接受它（会报 6000012）。
    // 传 sizeTemplateSourceId，由 SDK 先 createSizeSpec 基于蓝本生成新表再用新 id 提交。
    sizeTemplateSourceId:
      sizeTemplateId.value &&
      sizeTemplates.value.some((x) => Number(x.businessId) === Number(sizeTemplateId.value))
        ? sizeTemplateId.value
        : undefined,
    volume: volumeToPayload(volumeInput),
    weight: weightToPayload({ value: weightInput.value }),
  }
}

async function loadSites() {
  try {
    sites.value = await getProductSites()
  } catch (error) {
    notifyError(error)
  }
}

async function loadSizeTemplates() {
  if (!leafCatId.value) return
  try {
    // 带上站点：平台按站点的尺码组过滤模板，不带会拿到不能用的模板（6000012）
    const r = await getSizeTemplates(leafCatId.value, form.siteId)
    sizeTemplates.value = r.list
    // 尺码模板必须属于当前类目：切类目后旧 ID 要清掉，否则平台报 6000012 不合法的尺码模板id
    if (sizeTemplateId.value && !r.list.some((x) => Number(x.businessId) === Number(sizeTemplateId.value))) {
      sizeTemplateId.value = null
    }
  } catch {
    sizeTemplates.value = []
    sizeTemplateId.value = null
  }
}

/** 尺码类型（中文）→ 模板的 generalSizeType（1 均码 / 2 常规 / 3 Tall） */
const SIZE_TYPE_TO_GST: Record<string, number> = { 均码: 1, 常规: 2, Tall: 3 }

/** 当前尺码类型对应的 generalSizeType（Plus 平台未定义 → 不过滤） */
const currentGeneralSizeType = computed(() => SIZE_TYPE_TO_GST[sizeTypeId.value] || 0)

/** 尺码表下拉：只列与当前尺码类型匹配的模板，避免「均码模板配常规码」触发 6000012 */
const sizeTemplateOptions = computed(() => {
  const gst = currentGeneralSizeType.value
  // 没选尺码类型 / 该类型在平台没有对应模板类型（如 Plus）→ 不给选，
  // 否则会退化成「全部模板」，用户能选到类型不匹配的，平台报 6000012
  if (!gst) return []
  return sizeTemplates.value.filter((x) => Number(x.generalSizeType) === gst)
})

/** 尺码模板预检：类目不符 / 类型不符 / 尺码覆盖不到（6000012 的三种成因） */
async function checkSizeTemplate(id: number | null) {
  if (!id || !leafCatId.value) return
  // 本次勾选的尺码值（M / L / one-size），用于比对模板尺码表的覆盖范围
  const pickedValues = currentSizes.value
    .filter((s) => form.sizeVids.includes(s.vid))
    .map((s) => s.value)
  try {
    const r = await validateSizeTemplate(
      leafCatId.value,
      [id],
      currentGeneralSizeType.value,
      pickedValues,
    )
    if (r.ok) return
    // 尺码覆盖不到时换模板无意义（下拉已按当前尺码类型过滤），只提示不动用户的选择
    if (r.sizeBad && r.sizeBad.length) {
      const missing = r.sizeBad[0]?.missing || []
      notifyWarning(
        `当前尺码表没有包含：${missing.join('、')}。请先在 Temu 后台补建尺码表，或取消勾选这些尺码。`,
      )
      return
    }
    const names = r.available.length
      ? `该类目可用模板：${r.available.map((x) => x.name || x.businessId).join('、')}`
      : '该类目还没有任何尺码模板'
    const tip = r.suggestion ? `，已自动改为「${r.available.find((x) => x.businessId === r.suggestion)?.name || r.suggestion}」` : ''
    notifyWarning(`${r.reason || '模板不属于当前类目'}。${names}${tip}`)
    sizeTemplateId.value = r.suggestion ?? null
  } catch {
    // 预检失败不阻塞，交给 createProduct 内部的检查
  }
}

// 勾选/取消尺码后重新预检：模板的尺码表可能覆盖不到新勾的尺码
watch(
  () => form.sizeVids.slice(),
  () => {
    if (sizeTemplateId.value) void checkSizeTemplate(sizeTemplateId.value)
  },
)

async function loadConstants() {
  try {
    const c = await getProductConstants()
    if (c.sku_classification?.length) skuClassOptions.value = c.sku_classification
    if (c.sensitive_types?.length) sensitiveTypeOptions.value = c.sensitive_types
  } catch {
    /* 常量拿不到就用内置兜底 */
  }
}

async function loadRegions() {
  if (regionsData.value) return
  try {
    regionsData.value = await getProductRegions()
    const provinces = regionsData.value?.cn_provinces || []
    if (!originProvince.value && provinces.length) {
      // 省份用 code 填 origin.region2Id
      originProvince.value = Number(provinces[0].code) || null
    }
    // 首次加载后按当前产地同步制造地点
    syncManufacturingCountry(originCountry.value)
  } catch {
    regionsData.value = null
  }
}

/** 类目配置：敏感属性按类目要求自动带出，并把额外必填项提示给用户 */
async function loadCategoryConfig() {
  if (!leafCatId.value) return
  try {
    const cfg = await getCategoryConfig(leafCatId.value, form.siteId)
    categoryCfg.value = {
      sensitive_types: cfg.sensitive_types || [],
      extra_requirements: cfg.extra_requirements || [],
      title_max_length: cfg.title_max_length || 0,
    }
    if (cfg.sensitive_types.length) {
      isSensitive.value = true
      sensitiveList.value = Array.from(new Set([...sensitiveList.value, ...cfg.sensitive_types]))
    }
  } catch {
    categoryCfg.value = null
  }
}

async function loadEnv(refresh = false) {
  loadingEnv.value = true
  try {
    env.value = await getProductEnv(form.siteId, refresh)
    // 默认勾选：只勾当前站点真正可用的仓库，没有就留空（后面会提示该站点不可用）
    const allowed = new Set(warehouseOptions.value)
    const preferred = siteWarehouseIds.value.map(String).filter((id) => allowed.has(id))
    form.warehouseIds = preferred.length ? preferred : [...allowed]
    if (!form.freightTemplateId && env.value?.freightTemplateId) {
      form.freightTemplateId = String(env.value.freightTemplateId)
    }
  } catch (error) {
    notifyError(error)
    env.value = null
  } finally {
    loadingEnv.value = false
  }
}

let catSearchTimer: number | undefined

/** 输入关键字即时匹配类目（对齐官方新建商品页，不用点按钮） */
async function runCategorySearch() {
  const name = categoryKeyword.value.trim()
  if (name.length < 2) {
    categoryCandidates.value = []
    return
  }
  searchingCategory.value = true
  try {
    // searchCategories 是官网分类搜索框的真实接口，返回多条
    const result = await searchProductCategories(name)
    categoryCandidates.value = result.paths
  } catch {
    categoryCandidates.value = []
  } finally {
    searchingCategory.value = false
  }
}

function onCategoryKeywordChange() {
  if (catSearchTimer) window.clearTimeout(catSearchTimer)
  catSearchTimer = window.setTimeout(() => void runCategorySearch(), 450)
}

/** 点候选：回填并加载模板 */
function pickCategoryCandidate(item: ProductCategoryPath) {
  categoryCandidates.value = []
  categoryKeyword.value = ''
  onCategoryConfirm({ catPath: item.catPath, names: item.names })
}

/** 类目选择器确认：回填路径并加载模板 */
function onCategoryConfirm(payload: { catPath: number[]; names: string[] }) {
  const changed = payload.catPath.join('>') !== catPathText.value
  catPathText.value = payload.catPath.join('>')
  categoryNames.value = payload.names
  if (changed) {
    // 类目变了，所有跟类目绑定的东西同步清空。
    // 不能只靠 loadSizeTemplates 异步回来再清：请求返回前 sizeTemplates 还是旧列表，
    // 提交校验会误判旧模板 ID 合法 → 平台报 6000012 不合法的尺码模板id
    sizeTemplateId.value = null
    sizeTemplates.value = []
    form.colorVid = null
    form.sizeVids = []
    colorGroupId.value = null
    sizeStandard.value = ''
    sizeTypeId.value = ''
  }
  void loadTemplate(false)
}

/** 手填 ID 时反查中文名，避免只看到一串数字 */
async function resolveCategoryNames() {
  const ids = catPath.value
  if (!ids.length) {
    categoryNames.value = []
    return
  }
  try {
    const result = await getCategoryPath(ids)
    const hit = result.list.find((x) => x.catPath.join('>') === ids.join('>')) || result.list[0]
    categoryNames.value = hit ? hit.names : []
  } catch {
    // 反查失败不影响创建，静默降级为只显示 ID
    categoryNames.value = []
  }
}

async function loadTemplate(refresh = false) {
  if (!leafCatId.value) {
    notifyWarning('请先选择商品类目')
    return
  }
  loadingTemplate.value = true
  if (!categoryNames.value.length) void resolveCategoryNames()
  try {
    tmpl.value = await getProductTemplate(leafCatId.value, refresh)
    // 属性区在 describe 回来后才渲染，但提示不必等它——否则「模板已加载」比界面晚一秒
    notifySuccess(`模板已加载：${tmpl.value.summary?.total ?? 0} 个属性`)
    Object.keys(selected).forEach((k) => delete selected[k])
    await refreshDescribe()
    // 默认选第一个色系/尺码体系，并尝试按站点自动匹配尺码组
    colorGroupId.value = colorGroups.value[0]?.groupId ?? null
    sizeStandard.value = sizeStandardOptions.value[0] || ''
    sizeTypeId.value = sizeTypeOptions.value[0] || ''
    void loadCategoryConfig()
    void loadSizeTemplates()
  } catch (error) {
    notifyError(error)
    tmpl.value = null
    desc.value = null
  } finally {
    loadingTemplate.value = false
  }
}

let describeTimer: number | undefined

async function refreshDescribe() {
  if (!leafCatId.value || !tmpl.value) return
  loadingDescribe.value = true
  try {
    desc.value = await describeProduct(leafCatId.value, { ...selected })
  } catch (error) {
    notifyError(error)
  } finally {
    loadingDescribe.value = false
  }
}

/** 选值后重新拉一次 describe：条件显示/值依赖由上游引擎算，前端不猜 */
function onFieldChange() {
  if (describeTimer) window.clearTimeout(describeTimer)
  describeTimer = window.setTimeout(() => void refreshDescribe(), 350)
}

function setNumber(field: ProductTemplateField, value: number | undefined) {
  const key = field.numberInput?.key || `num_${field.refPid}`
  if (value === undefined || value === null) delete selected[key]
  else selected[key] = String(value)
  onFieldChange()
}

function setNumberPart(field: ProductTemplateField, value: number | undefined) {
  const key = field.numberInput?.key
  if (!key) return
  if (value === undefined || value === null) delete selected[key]
  else selected[key] = String(value)
  onFieldChange()
}

function openPicker(carousel: boolean) {
  pickerCarousel.value = carousel
  pickerVisible.value = true
}

function onPickDecoration(urls: string[]) {
  // 弹窗内已能增删（含同一张选多次），确认后整体替换；不要用 Set 去重
  decorationImages.value = [...urls]
}

/** 读 URL 图片的实际尺寸 */
function loadImageSize(url: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve) => {
    const img = new Image()
    img.onload = () => resolve({ width: img.naturalWidth, height: img.naturalHeight })
    img.onerror = () => resolve({ width: 0, height: 0 })
    img.src = url
  })
}

/**
 * 首图 → 居中裁 800×800 作为素材图。
 * 尺寸不足时留空，由服务端 autoMaterialImg 兜底（它会再裁一次）。
 */
async function autoCropMaterial(sourceUrl?: string) {
  const first = sourceUrl || form.images[0]
  if (!first || form.materialImgUrl) return
  if (!leafCatId.value) return
  const { width, height } = await loadImageSize(first)
  if (width < 800 || height < 800) return
  materialCropping.value = true
  try {
    const dx = Math.max(0, Math.round((width - 800) / 2))
    const dy = Math.max(0, Math.round((height - 800) / 2))
    const r = await cropMaterial(first, { width: 800, height: 800, dx, dy })
    if (r.url) {
      form.materialImgUrl = r.url
      materialAuto.value = true
    }
  } catch {
    // 自动裁失败就保持留空，交给服务端的 autoMaterialImg
  } finally {
    materialCropping.value = false
  }
}

function onPick(urls: string[]) {
  if (pickerCarousel.value) {
    // 官网允许同一张图占多个轮播位，这里保留重复项
    form.images = [...urls]
    void autoCropMaterial(urls[0])
  } else {
    form.materialImgUrl = urls[0] || ''
    materialAuto.value = false   // 手动选的，不再跟随首图
  }
}

/** 删掉指定位置的轮播图（同一张图可能在多个位置，只删这一个） */
function removeImageAt(index: number) {
  form.images = form.images.filter((_, i) => i !== index)
  // 素材图是首图裁出来的，轮播图变了就清空；
  // 不立刻重裁，避免在素材库堆积废图 —— 留空由服务端 autoMaterialImg 在提交时兜底
  if (materialAuto.value) {
    form.materialImgUrl = ''
    materialAuto.value = false
  }
}

/** 打开库存分布弹窗：按当前仓库初始化 */
function openStockDialog() {
  for (const id of form.warehouseIds) {
    if (stockPerWarehouse[id] === undefined) {
      stockPerWarehouse[id] = Math.max(0, Math.floor(form.stock / Math.max(1, form.warehouseIds.length)))
    }
  }
  stockDialogVisible.value = true
}

/** 一键全选：把总库存平均分到所有仓库 */
function distributeStockEvenly() {
  const ids = form.warehouseIds
  if (!ids.length) return
  const each = Math.floor(form.stock / ids.length)
  const rest = form.stock - each * ids.length
  ids.forEach((id, i) => {
    stockPerWarehouse[id] = each + (i === 0 ? rest : 0)
  })
}

/** 库存合计（弹窗里显示，避免和总库存不一致） */
const skuClassHint = computed(() => {
  const v = form.skuClassification
  if (v === 1) return '仅 1 件（1 个碗 / 1 对耳环），件数固定为 1，不能填是否独立包装'
  if (v === 2) return '多件同规格同种类（2 个相同杯子），需要填件数'
  return '多件不同品类/规格（1 个杯子 + 1 个勺子），需要填件数'
})

const stockTotal = computed(() =>
  Object.values(stockPerWarehouse).reduce((sum, n) => sum + (Number(n) || 0), 0),
)


// 模板列表依赖「当前已登录店铺」：登录前拉会拿到空/403，登录成功后要重新拉一次
watch(
  () => env.value?.siteId,
  (now, before) => {
    if (now && now !== before) void loadFormTemplates()
  },
)

async function loadFormTemplates(silent = true) {
  try {
    const r = await listFormTemplates()
    formTemplates.value = r.items
  } catch (error) {
    formTemplates.value = []
    // 首次挂载时可能还没登录店铺（403），不该吓用户；但至少留个日志便于排查
    if (!silent) notifyError(error)
    else console.warn('[template] 拉取模板列表失败：', error)
  }
}

/** 套用模板：先切类目（属性 key 依赖它），再回填可复用字段 */
async function applyFormTemplate(id: number) {
  if (!id) return
  templateLoading.value = true
  try {
    const r = await getFormTemplate(id)
    const v = r.values as Record<string, any>

    // 1. 类目：变了就等模板加载完再填属性
    if (Array.isArray(v.catPath) && v.catPath.length) {
      const text = v.catPath.join('>')
      if (text !== catPathText.value) {
        catPathText.value = text
        categoryNames.value = []
        await loadTemplate(false)
      }
    }
    // 2. 属性
    if (v.selected && typeof v.selected === 'object') {
      Object.keys(selected).forEach((k) => delete selected[k])
      Object.assign(selected, v.selected)
      await refreshDescribe()
    }
    // 3. 产地 / 制造地点
    if (v.origin && typeof v.origin === 'object') {
      if (v.origin.countryShortName) originCountry.value = String(v.origin.countryShortName)
      if (v.origin.region2Id) originProvince.value = Number(v.origin.region2Id)
    }
    if (Array.isArray(v.manufacturing)) manufacturing.value = v.manufacturing.map(String)
    // 4. 体积重量
    if (v.volume && typeof v.volume === 'object') {
      volumeInput.len = Number(v.volume.inputLen || 10)
      volumeInput.width = Number(v.volume.inputWidth || 9)
      volumeInput.height = Number(v.volume.inputHeight || 8)
    }
    if (v.weight && typeof v.weight === 'object') {
      weightInput.value = Number(v.weight.inputValue || 200)
    }
    // 5. 其它可复用
    if (typeof v.isSensitive === 'boolean') isSensitive.value = v.isSensitive
    if (Array.isArray(v.sensitiveTypes)) sensitiveTypes.value = v.sensitiveTypes
    if (Array.isArray(v.sensitiveList)) sensitiveList.value = v.sensitiveList
    if (v.skuClassification) form.skuClassification = Number(v.skuClassification)
    if (v.pieces) form.pieces = Number(v.pieces)
    if (typeof v.individuallyPacked !== 'undefined') form.individuallyPacked = !!v.individuallyPacked
    if (v.shipmentLimit) form.shipmentLimit = String(v.shipmentLimit)
    if (v.freightTemplateId) form.freightTemplateId = String(v.freightTemplateId)
    if (v.skuPrice) form.skuPrice = Number(v.skuPrice)
    if (typeof v.stock === 'number') form.stock = v.stock
    if (Array.isArray(v.sizeVids) && v.sizeVids.length) form.sizeVids = v.sizeVids.map(Number)
    if (v.sizeTemplateId) sizeTemplateId.value = Number(v.sizeTemplateId)
    if (v.generalSizeType) {
      const name = Object.keys(SIZE_TYPE_TO_GST).find((k) => SIZE_TYPE_TO_GST[k] === Number(v.generalSizeType))
      if (name) sizeTypeId.value = name
    }
    if (Array.isArray(v.materialMultiLanguages) && v.materialMultiLanguages.length) {
      materialLanguages.value = v.materialMultiLanguages.map(String)
    }

    notifySuccess(`已套用模板「${r.name}」，标题/图片/货号/颜色保持原样`)
  } catch (error) {
    notifyError(error)
  } finally {
    templateLoading.value = false
  }
}

function openSaveTemplate() {
  saveTplName.value = ''
  saveTplNote.value = ''
  saveTplVisible.value = true
}

async function confirmSaveTemplate() {
  if (!saveTplName.value.trim()) {
    notifyWarning('请填写模板名称')
    return
  }
  savingTpl.value = true
  try {
    const r = await saveFormTemplate(saveTplName.value.trim(), saveTplNote.value.trim(), buildForm())
    notifySuccess(r.updated ? '模板已更新' : '模板已保存')
    saveTplVisible.value = false
    await loadFormTemplates()
  } catch (error) {
    notifyError(error)
  } finally {
    savingTpl.value = false
  }
}

async function openEditTemplate(tpl: { id: number; name: string; note: string }) {
  editTplId.value = tpl.id
  editTplName.value = tpl.name
  editTplNote.value = tpl.note || ''
  editTplFields.value = []
  editTplCatPath.value = []
  Object.keys(editTplSelected).forEach((k) => delete editTplSelected[k])
  editTplLoading.value = true
  try {
    const r = await getFormTemplate(tpl.id)
    const v = r.values as Record<string, any>
    // 补齐默认结构：老模板可能没存某个对象，直接 v-model 会报 undefined
    editTplValues.value = {
      origin: {},
      manufacturing: [],
      volume: { len: 10, width: 9, height: 8 },
      weight: { value: 200 },
      sensitiveTypes: [],
      sensitiveList: [],
      isSensitive: false,
      skuClassification: 1,
      pieces: 2,
      individuallyPacked: false,
      skuPrice: 39.9,
      stock: 100,
      shipmentLimit: '2个工作日',
      ...JSON.parse(JSON.stringify(v || {})),
    }
    if (Array.isArray(v.catPath)) editTplCatPath.value = v.catPath.map(Number)
    if (v.selected && typeof v.selected === 'object') {
      Object.assign(editTplSelected, v.selected)
    }
    // 拉类目模板，才知道每个属性该用什么控件、有哪些选项
    if (!editTplSites.value.length) {
      editTplSites.value = await getProductSites().catch(() => [])
    }
    editTplEnv.value = await getProductEnv(editTplValues.value.siteId || undefined).catch(() => null)
    const catId = editTplCatPath.value[editTplCatPath.value.length - 1]
    editTplSizeTree.value = {}
    editTplSizeStandard.value = ''
    editTplSizeType.value = ''
    editTplSizeTemplates.value = []
    // buildForm 存的是 sizeTemplateSourceId（蓝本 id），sizeTemplateId 是旧字段名
    const srcTpl = v.sizeTemplateSourceId ?? v.sizeTemplateId
    editTplSizeTplId.value = srcTpl ? Number(srcTpl) : null
    if (catId) {
      const [d, st] = await Promise.all([
        describeProduct(catId, { ...editTplSelected }),
        getSizeTemplates(catId, form.siteId || undefined).catch(() => ({ list: [] })),
      ])
      editTplFields.value = d.fields || []
      editTplSizeTree.value = (d.sizeTree as Record<string, Record<string, any[]>>) || {}
      editTplSizeTemplates.value = (st as any).list || []
      // 用已保存的 sizeVids 反推它是哪个「标准 + 类型」
      const saved = Array.isArray(v.sizeVids) ? v.sizeVids.map(Number) : []
      if (saved.length) {
        for (const [std, types] of Object.entries(editTplSizeTree.value)) {
          for (const [ty, list] of Object.entries(types || {})) {
            if ((list || []).some((x: any) => saved.includes(Number(x.vid)))) {
              editTplSizeStandard.value = std
              editTplSizeType.value = ty
              break
            }
          }
          if (editTplSizeStandard.value) break
        }
      }
      if (!editTplSizeStandard.value) {
        editTplSizeStandard.value = Object.keys(editTplSizeTree.value)[0] || ''
        editTplSizeType.value = Object.keys(editTplSizeTree.value[editTplSizeStandard.value] || {})[0] || ''
      }
    }
  } catch (error) {
    notifyError(error)
  } finally {
    editTplLoading.value = false
    // 数据铺好后再显示，避免渲染时读到空对象报错
    editTplVisible.value = true
  }
}

/** select+number 字段：数字部分写进 input_<key> */
function setEditTplNumber(field: any, v: number | undefined) {
  const key = `input_${field.refPid ?? field.key}`
  if (v === undefined || v === null) delete editTplSelected[key]
  else editTplSelected[key] = v
}

const editTplSizes = computed(
  () => editTplSizeTree.value[editTplSizeStandard.value]?.[editTplSizeType.value] || [],
)

/** 尺码表模板按当前尺码类型过滤（同主表单逻辑） */
const editTplSizeTplOptions = computed(() => {
  const gst = SIZE_TYPE_TO_GST[editTplSizeType.value]
  if (!gst) return []
  return editTplSizeTemplates.value.filter((x) => Number(x.generalSizeType) === gst)
})

async function onEditTplSiteChange(siteId: number) {
  editTplValues.value.siteId = siteId
  editTplEnv.value = null
  editTplEnv.value = await getProductEnv(siteId, true).catch(() => null)
}

async function confirmEditTemplate() {
  if (!editTplName.value.trim()) {
    notifyWarning('请填写模板名称')
    return
  }
  savingEditTpl.value = true
  try {
    // 参数一律取弹窗里编辑后的完整值（属性 + 产地 + 体积 + 服务承诺 + SKU）
    const nextForm: Record<string, unknown> = {
      ...editTplValues.value,
      selected: { ...editTplSelected },
      sizeVids: [...(editTplValues.value.sizeVids || [])],
      sizeTemplateSourceId: editTplSizeTplId.value || undefined,
      sizeTemplateId: editTplSizeTplId.value || undefined,
      generalSizeType: SIZE_TYPE_TO_GST[editTplSizeType.value] || undefined,
    }
    // 编辑弹窗改的是 cm/g，保存前必须按它重算平台单位
    if (nextForm.volume) nextForm.volume = normalizeVolumePayload(nextForm.volume as Record<string, unknown>)
    if (nextForm.weight) nextForm.weight = normalizeWeightPayload(nextForm.weight as Record<string, unknown>)

    await updateFormTemplate(editTplId.value, {
      name: editTplName.value.trim(),
      note: editTplNote.value.trim(),
      form: nextForm,
    })
    notifySuccess('已保存')
    editTplVisible.value = false
    await loadFormTemplates()
  } catch (error) {
    notifyError(error)
  } finally {
    savingEditTpl.value = false
  }
}

async function removeFormTemplate(id: number) {
  const ok = await confirmAction('删除这个模板？', '删除模板')
  if (!ok) return
  try {
    await deleteFormTemplate(id)
    await loadFormTemplates()
    notifySuccess('已删除')
  } catch (error) {
    notifyError(error)
  }
}

async function doCreate() {
  if (missing.value.length) {
    notifyWarning(`还缺必填属性：${missing.value.join('、')}`)
    return
  }
  const label = dryRun.value ? '预览创建（只建草稿，不真正建品）' : '正式创建商品'
  if (!(await confirmAction(`${label}：${form.name}？`, '创建商品'))) return
  creating.value = true
  try {
    const result = await createProduct(buildForm(), dryRun.value)
    if (dryRun.value) {
      notifySuccess(`草稿已保存（draftId=${result.draft_id}），未创建商品`)
    } else {
      notifySuccess(`创建成功：productId=${result.product_id}`)
      form.name = ''
      form.images = []
      form.materialImgUrl = ''
    }
  } catch (error) {
    notifyError(error)
  } finally {
    creating.value = false
  }
}

watch(
  () => form.siteId,
  () => {
    // 先清空：否则新站点数据回来前，下拉框仍渲染上一站点的仓库（名称查不到就变成 WH-xxx）
    featureGuard.value = true
    env.value = null
    form.warehouseIds = []
    void loadEnv().finally(() => {
      featureGuard.value = false
    })
  },
)


// 类目一变，模板列表和已选模板立刻作废。
// 不能只依赖 onCategoryConfirm：手填类目 ID 不走那里，会残留上一个类目的模板，
// 提交时就变成「39276 的类目 + 39275 的模板」→ 6000012。
watch(leafCatId, (next, prev) => {
  if (!next || next === prev) return
  sizeTemplates.value = []
  sizeTemplateId.value = null
  // 旧类型的尺码值属于旧类目，类型本身也要重选
  sizeTypeId.value = ''
  form.sizeVids = []
})

watch(originCountry, (country) => {
  // 切到非中国时清空省份，避免残留「江苏省」
  if (country !== 'CN') originProvince.value = null
  syncManufacturingCountry(country)
})

watch(
  () => [form.name, form.images, form.materialImgUrl, form.colorVid, form.sizeVids, form.skuPrice, form.stock],
  () => {
    },
  { deep: true },
)

onMounted(async () => {
  await loadSites()
  await loadEnv()
  void loadRegions()
  void loadConstants()
})
</script>

<template>
  <div class="pcreate">
    <!-- 表单模板 -->
    <el-card shadow="never" class="pcreate__card pcreate__tplbar">
      <div class="pcreate__tplrow">
        <span class="pcreate__tpllabel">表单模板</span>
        <el-select
          :model-value="null"
          class="pcreate__select"
          placeholder="套用一个已保存的模板"
          :loading="templateLoading"
          filterable
          :disabled="templateLoading"
          @visible-change="(open: boolean) => open && loadFormTemplates()"
          @change="(v: number) => applyFormTemplate(v)"
        >
          <el-option
            v-for="tpl in formTemplates"
            :key="tpl.id"
            :label="`${tpl.name}${tpl.note ? ` · ${tpl.note}` : ''}（${tpl.field_count} 项属性）`"
            :value="tpl.id"
          />
          <template #empty>
            <span class="pcreate__meta">还没有模板，填好表单后点「保存为模板」</span>
          </template>
        </el-select>
        <el-button :disabled="!tmpl" @click="openSaveTemplate">保存为模板</el-button>
        <el-button text @click="loadFormTemplates(false); tplManageVisible = true">管理</el-button>
      </div>
      <p class="pcreate__meta">
        套用模板会复用类目、属性、产地、体积重量、尺码等；标题、图片、货号、颜色保持你当前填的
      </p>
    </el-card>

    <el-dialog v-model="saveTplVisible" title="保存为表单模板" width="min(480px, calc(100vw - 32px))">
      <el-form label-width="90px" label-position="left">
        <el-form-item label="模板名称" required>
          <el-input v-model="saveTplName" maxlength="60" show-word-limit placeholder="如：黑色蕾丝连体衣通用属性" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="saveTplNote" type="textarea" :rows="2" maxlength="300" show-word-limit placeholder="选填，便于以后识别" />
        </el-form-item>
      </el-form>
      <p class="pcreate__meta">同名模板会被覆盖；想用当前页面整体更新已有模板，直接输同名保存即可</p>
      <template #footer>
        <el-button @click="saveTplVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingTpl" @click="confirmSaveTemplate">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="tplManageVisible" title="表单模板管理" width="min(620px, calc(100vw - 32px))">
      <el-table :data="formTemplates" size="small" max-height="380">
        <el-table-column prop="name" label="名称" min-width="120" show-overflow-tooltip />
        <el-table-column prop="note" label="备注" min-width="140" show-overflow-tooltip>
          <template #default="scope">{{ scope.row.note || '—' }}</template>
        </el-table-column>
        <el-table-column label="属性" width="70">
          <template #default="scope">{{ scope.row.field_count }} 项</template>
        </el-table-column>
        <el-table-column label="操作" width="180">
          <template #default="scope">
            <el-button link type="primary" size="small" @click="applyFormTemplate(scope.row.id); tplManageVisible = false">套用</el-button>
            <el-button link size="small" @click="openEditTemplate(scope.row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="removeFormTemplate(scope.row.id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="tplManageVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="editTplVisible"
      title="编辑模板"
      width="min(760px, calc(100vw - 32px))"
      top="6vh"
      class="pcreate__tpldlg"
    >
      <div class="pcreate__tpleditor">
      <el-form label-width="90px" label-position="left">
        <el-form-item label="模板名称" required>
          <el-input v-model="editTplName" maxlength="60" show-word-limit />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="editTplNote" type="textarea" :rows="2" maxlength="300" show-word-limit />
        </el-form-item>
      </el-form>

      <el-divider content-position="left">经营站点</el-divider>
      <el-form label-width="90px" label-position="left" size="small">
        <el-form-item label="站点">
          <el-select
            v-model="editTplValues.siteId"
            class="pcreate__attrctl"
            filterable
            placeholder="选择站点"
            @change="(v: number) => onEditTplSiteChange(v)"
          >
            <el-option
              v-for="s in editTplSites"
              :key="String(s.siteId)"
              :label="String(s.siteName || s.siteId)"
              :value="Number(s.siteId)"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="发货仓">
          <el-select
            v-model="editTplValues.warehouseIds"
            multiple
            collapse-tags
            filterable
            class="pcreate__attrctl"
            placeholder="选择发货仓"
          >
            <el-option
              v-for="w in editTplEnv?.warehouses || []"
              :key="String(w.warehouseId)"
              :label="String(w.warehouseName || w.warehouseId)"
              :value="String(w.warehouseId)"
            />
          </el-select>
        </el-form-item>
      </el-form>

      <el-divider content-position="left">
        模板参数{{ editTplFields.length ? `（${editTplFields.length} 项）` : '' }}
      </el-divider>
      <div v-loading="editTplLoading" class="pcreate__tplparams">
        <el-alert
          v-if="!editTplLoading && !editTplFields.length"
          type="info"
          :closable="false"
          title="这个模板没有可编辑的属性"
        />
        <el-form v-else label-width="112px" label-position="left" size="small">
          <el-form-item v-for="f in editTplFields" :key="f.key" :label="f.label">
            <el-select
              v-if="f.type === 'select'"
              v-model="editTplSelected[f.key]"
              class="pcreate__attrctl"
              clearable
              filterable
              placeholder="请选择"
            >
              <el-option v-for="o in f.options" :key="o.value" :label="o.label" :value="o.value" />
            </el-select>
            <div v-else-if="f.type === 'select+number'" class="pcreate__inline">
              <el-select v-model="editTplSelected[f.key]" class="pcreate__inline-select" clearable filterable>
                <el-option v-for="o in f.options" :key="o.value" :label="o.label" :value="o.value" />
              </el-select>
              <el-input-number
                :model-value="f.numberInput?.value ? Number(f.numberInput.value) : undefined"
                :min="f.numberInput?.min ?? 0"
                :max="f.numberInput?.max ?? 999999"
                class="pcreate__inline-num"
                controls-position="right"
                @update:model-value="(v: number | undefined) => setEditTplNumber(f, v)"
              />
              <span v-if="f.numberInput?.unit" class="pcreate__unit">{{ f.numberInput.unit }}</span>
            </div>
            <el-input-number
              v-else
              v-model="editTplSelected[f.key] as number"
              :min="f.numberInput?.min ?? 0"
              :max="f.numberInput?.max ?? 999999"
              class="pcreate__attrctl"
              controls-position="right"
            />
          </el-form-item>
        </el-form>
      </div>

      <el-divider content-position="left">产地与合规</el-divider>
      <el-form label-width="112px" label-position="left" size="small">
        <el-form-item label="商品产地">
          <div class="pcreate__inline">
            <el-select v-model="editTplValues.origin.countryShortName" class="pcreate__inline-select" filterable placeholder="国家">
              <el-option
                v-for="c in regionsData?.origin_countries || []"
                :key="String(c.shortName || c.code)"
                :label="String(c.name)"
                :value="String(c.shortName || c.code)"
              />
            </el-select>
            <el-select
              v-if="editTplValues.origin.countryShortName === 'CN'"
              v-model="editTplValues.origin.region2Id"
              class="pcreate__inline-select"
              filterable
              placeholder="省份"
            >
              <el-option
                v-for="pr in regionsData?.cn_provinces || []"
                :key="String(pr.code)"
                :label="String(pr.name)"
                :value="Number(pr.code)"
              />
            </el-select>
          </div>
        </el-form-item>
        <el-form-item label="制造地点">
          <el-select v-model="editTplValues.manufacturing" multiple collapse-tags filterable class="pcreate__attrctl" placeholder="可多选">
            <el-option
              v-for="c in regionsData?.manufacturing_countries || []"
              :key="String(c.shortName || c.code)"
              :label="String(c.name)"
              :value="String(c.shortName || c.code)"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="敏感属性">
          <el-switch v-model="editTplValues.isSensitive" active-text="是" inactive-text="否" />
        </el-form-item>
        <el-form-item v-if="editTplValues.isSensitive" label="敏感项">
          <el-checkbox-group v-model="editTplValues.sensitiveTypes">
            <el-checkbox v-for="s in sensitiveTypeOptions" :key="s.value" :value="s.value">{{ s.label }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="体积（cm）">
          <div class="pcreate__inline">
            <span class="pcreate__dimplabel">最长边</span>
            <el-input-number v-model="editTplValues.volume.inputLen" :min="1" :max="999" class="pcreate__dimnum" controls-position="right" />
            <span class="pcreate__dimplabel">次长边</span>
            <el-input-number v-model="editTplValues.volume.inputWidth" :min="1" :max="999" class="pcreate__dimnum" controls-position="right" />
            <span class="pcreate__dimplabel">最短边</span>
            <el-input-number v-model="editTplValues.volume.inputHeight" :min="1" :max="999" class="pcreate__dimnum" controls-position="right" />
          </div>
        </el-form-item>
        <el-form-item label="重量（g）">
          <el-input-number v-model="editTplValues.weight.inputValue" :min="1" :max="999999" class="pcreate__weightnum" controls-position="right" />
        </el-form-item>
      </el-form>

      <el-divider content-position="left">服务与承诺</el-divider>
      <el-form label-width="112px" label-position="left" size="small">
        <el-form-item label="承诺发货时效">
          <el-select v-model="editTplValues.shipmentLimit" class="pcreate__attrctl">
            <el-option
              v-for="item in ['1个工作日', '2个工作日', '3个工作日', '5个工作日', '7个工作日', '9个工作日', '10个工作日']"
              :key="item" :label="item" :value="item"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="运费模板">
          <el-select v-model="editTplValues.freightTemplateId" class="pcreate__attrctl" clearable filterable placeholder="选择运费模板">
            <el-option
              v-for="tpl2 in freightTemplates"
              :key="String(tpl2.freightTemplateId)"
              :label="String(tpl2.templateName || tpl2.freightTemplateId)"
              :value="String(tpl2.freightTemplateId)"
            />
          </el-select>
        </el-form-item>
      </el-form>

      <el-divider content-position="left">SKU 与尺码</el-divider>
      <el-form label-width="112px" label-position="left" size="small">
        <el-form-item label="SKU 分类">
          <el-radio-group v-model="editTplValues.skuClassification">
            <el-radio v-for="opt in skuClassOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="editTplValues.skuClassification !== 1" label="件数">
          <div class="pcreate__inline">
            <el-input-number v-model="editTplValues.pieces" :min="2" :max="9999" class="pcreate__inline-num" controls-position="right" />
            <el-checkbox v-model="editTplValues.individuallyPacked">是否独立包装</el-checkbox>
          </div>
        </el-form-item>
        <el-form-item label="尺码标准">
          <el-select
            v-if="Object.keys(editTplSizeTree).length > 1"
            v-model="editTplSizeStandard"
            class="pcreate__attrctl"
            placeholder="尺码标准"
            @change="editTplSizeType = Object.keys(editTplSizeTree[editTplSizeStandard] || {})[0] || ''; editTplValues.sizeVids = []; editTplSizeTplId = null"
          >
            <el-option v-for="s in Object.keys(editTplSizeTree)" :key="s" :label="s" :value="s" />
          </el-select>
          <span v-else class="pcreate__meta">{{ editTplSizeStandard || '该类目只有一个尺码标准' }}</span>
        </el-form-item>

        <el-form-item label="尺码类型">
          <el-radio-group
            v-model="editTplSizeType"
            size="small"
            @change="editTplValues.sizeVids = []; editTplSizeTplId = null"
          >
            <el-radio-button v-for="ty in Object.keys(editTplSizeTree[editTplSizeStandard] || {})" :key="ty" :value="ty">
              {{ ty }}（{{ (editTplSizeTree[editTplSizeStandard]?.[ty] || []).length }}）
            </el-radio-button>
          </el-radio-group>
          <span v-if="!Object.keys(editTplSizeTree[editTplSizeStandard] || {}).length" class="pcreate__meta">
            没有可用的尺码类型
          </span>
        </el-form-item>

        <el-form-item v-if="editTplSizes.length" label="具体尺码">
          <div class="pcreate__sizechecks" style="margin: 0; width: 100%">
            <el-checkbox
              :model-value="(editTplValues.sizeVids || []).length === editTplSizes.length && editTplSizes.length > 0"
              :indeterminate="(editTplValues.sizeVids || []).length > 0 && (editTplValues.sizeVids || []).length < editTplSizes.length"
              @change="(v: boolean) => (editTplValues.sizeVids = v ? editTplSizes.map((s: any) => s.vid) : [])"
            >全选</el-checkbox>
            <el-checkbox-group v-model="editTplValues.sizeVids">
              <el-checkbox v-for="s in editTplSizes" :key="s.vid" :value="s.vid">{{ s.value }}</el-checkbox>
            </el-checkbox-group>
          </div>
        </el-form-item>

        <el-form-item label="尺码表">
          <el-select
            v-model="editTplSizeTplId"
            class="pcreate__attrctl"
            clearable
            filterable
            :placeholder="editTplSizeTplOptions.length ? '选择已保存的尺码表' : '该类目/类型下没有可用尺码表'"
          >
            <el-option
              v-for="tp in editTplSizeTplOptions"
              :key="tp.businessId"
              :label="`${tp.name}（${tp.sizeTypeName || '均码'} · ${(tp.sizes || []).join('/')}）`"
              :value="tp.businessId"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="申报价格（元）">
          <el-input-number v-model="editTplValues.skuPrice" :min="0.01" :max="999999" :precision="2" class="pcreate__attrctl" controls-position="right" />
        </el-form-item>

        <el-form-item label="库存">
          <el-input-number v-model="editTplValues.stock" :min="0" :max="999999" class="pcreate__attrctl" controls-position="right" />
        </el-form-item>
      </el-form>

      </div>

      <template #footer>
        <el-button @click="editTplVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingEditTpl" @click="confirmEditTemplate">保存</el-button>
      </template>
    </el-dialog>

    <!-- 第一步：类目与站点 -->
    <el-card shadow="never" class="pcreate__card">
      <template #header><span class="pcreate__title">1. 站点与类目</span></template>

      <el-form label-width="96px" label-position="left">
        <el-form-item label="经营站点" required>
          <el-select
            v-model="form.siteId"
            class="pcreate__site"
            placeholder="选择站点"
            filterable
            :loading="loadingEnv"
            @change="loadEnv()"
          >
            <el-option
              v-for="item in sites"
              :key="String(item.siteId)"
              :label="String(item.siteName || item.siteId)"
              :value="Number(item.siteId)"
            />
          </el-select>
          <el-button size="small" :loading="loadingEnv" @click="loadEnv(true)">刷新环境</el-button>
        </el-form-item>

        <p v-if="!canSubmit && submitBlockers.length" class="pcreate__blockers">
        还差：{{ submitBlockers.join('；') }}
      </p>

      <el-alert
          v-if="env && !siteReady"
          class="pcreate__sitewarn"
          type="error"
          :closable="false"
          show-icon
          :title="`${siteBlockReason || '该站点不可用于建品'}：${sites.find((s) => Number(s.siteId) === form.siteId)?.siteName || form.siteId}`"
          :description="warehouseHint"
        />

        <el-form-item label="发货仓" :required="siteReady">
          <div class="pcreate__catbox">
            <div class="pcreate__catrow">
              <el-select
                v-model="form.warehouseIds"
                multiple
                collapse-tags
                collapse-tags-tooltip
                class="pcreate__wh"
                :disabled="featureGuard || !warehouseOptions.length"
                :placeholder="featureGuard ? '切换站点中…' : warehouseOptions.length ? '选择发货仓' : '该站点无可用发货仓'"
              >
                <el-option v-for="id in warehouseOptions" :key="id" :label="warehouseLabel(id)" :value="id" />
              </el-select>
              <el-button link type="primary" @click="warehouseDialogVisible = true">
                站点-仓库关系（{{ (env?.warehouseRoutes || []).length }} 个站点）
              </el-button>
            </div>
            <div class="pcreate__catpath" v-if="warehouseOptions.length">
              该站点可用仓库：{{ warehouseOptions.map(warehouseLabel).join('、') }}
            </div>
          </div>
        </el-form-item>


        <el-form-item label="商品分类" required>
          <div class="pcreate__catbox">
            <div class="pcreate__catrow">
              <el-input
                v-model="categoryKeyword"
                class="pcreate__catsearch"
                placeholder="输入关键字，自动匹配类目（如：情趣、连衣裙）"
                clearable
                :prefix-icon="Search"
                @input="onCategoryKeywordChange"
              />
              <el-button :icon="Grid" @click="categoryPickerVisible = true">浏览类目树</el-button>
            </div>

            <div v-if="searchingCategory" class="pcreate__catloading">匹配中…</div>
            <div v-else-if="categoryCandidates.length" class="pcreate__catlist">
              <button
                v-for="item in categoryCandidates"
                :key="item.label"
                type="button"
                class="pcreate__catitem"
                @click="pickCategoryCandidate(item)"
              >{{ item.label }}</button>
            </div>

            <div v-if="categoryNames.length" class="pcreate__catpath">
              <span class="pcreate__catnametext">已选：{{ categoryNames.join(' > ') }}</span>
              <el-button link type="primary" size="small" :loading="loadingTemplate" @click="loadTemplate(true)">
                重新加载
              </el-button>
            </div>
            <div v-else-if="leafCatId" class="pcreate__catpath">
              <span class="pcreate__catnametext">已选类目</span>
              <el-button link type="primary" size="small" :loading="loadingTemplate" @click="loadTemplate(false)">
                加载模板
              </el-button>
            </div>
          </div>
        </el-form-item>
      </el-form>

      <p v-if="tmpl" class="pcreate__meta">
        模板 {{ tmpl.template_id }} · 属性 {{ tmpl.summary?.attrs ?? 0 }} 个
      </p>
    </el-card>

    <!-- 素材语言 -->
    <el-card shadow="never" class="pcreate__card">
      <template #header><span class="pcreate__title">素材语言</span></template>
      <p class="pcreate__meta">
        图片里含文字卖点或商品说明时，勾选对应语言，让商品描述精准适配目标市场。英语为必选。
      </p>
      <el-checkbox-group v-model="materialLanguages">
        <el-checkbox
          v-for="l in MATERIAL_LANGS"
          :key="l.value"
          :value="l.value"
          :disabled="l.value === 'en'"
        >{{ l.label }}</el-checkbox>
      </el-checkbox-group>
    </el-card>

    <!-- 第二步：基础信息 -->
    <el-card v-if="tmpl" shadow="never" class="pcreate__card">
      <template #header><span class="pcreate__title">2. 基础信息</span></template>
      <el-form label-width="150px" label-position="left">
        <el-form-item label="商品名称" required>
          <el-input v-model="form.name" maxlength="500" show-word-limit placeholder="中文商品名称" />
        </el-form-item>
        <el-form-item label="英文名称" required>
          <el-input
            v-model="form.enName"
            maxlength="500"
            show-word-limit
            placeholder="英文商品标题，至少 20 个字符（半托管强制要求）"
            :class="{ 'pcreate__input--error': enNameTooShort }"
          />
          <span v-if="enNameTooShort" class="pcreate__enwarn">
            还差 {{ EN_NAME_MIN - form.enName.trim().length }} 个字符，不足 20 字平台会报 2000177
          </span>
        </el-form-item>
        <el-form-item label="申报价格（元）" required>
          <el-input-number
            v-model="form.skuPrice"
            :min="0.01"
            :max="999999"
            :precision="2"
            :step="1"
            :disabled="!hasSize"
          />
          <span class="pcreate__meta">
            {{ hasSize ? '平台按「分」存储，提交时自动换算' : '请先在第 3 步选择尺码' }}
          </span>
        </el-form-item>
        <el-form-item label="库存">
          <el-input-number v-model="form.stock" :min="0" :max="999999" :disabled="!hasSize" />
          <el-button link type="primary" size="small" @click="openStockDialog">按仓库分布</el-button>
          <span class="pcreate__meta">创建时非必填，填了请确保准确</span>
        </el-form-item>

        <el-form-item label="SKU 分类" required>
          <el-radio-group v-model="form.skuClassification">
            <el-radio v-for="opt in skuClassOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</el-radio>
          </el-radio-group>
          <span class="pcreate__meta">{{ skuClassHint }}</span>
        </el-form-item>
        <el-form-item v-if="form.skuClassification !== 1" label="件数" required>
          <el-input-number v-model="form.pieces" :min="2" :max="9999" />
          <el-checkbox v-model="form.individuallyPacked" class="pcreate__inlinecheck">是否独立包装</el-checkbox>
        </el-form-item>
        <el-form-item label="货号">
          <el-input v-model="form.extCode" maxlength="64" placeholder="SKU 货号（选填）" />
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 第三步：规格与图片 -->
    <el-card v-if="tmpl" shadow="never" class="pcreate__card">
      <template #header><span class="pcreate__title">3. 颜色 / 尺码 / 图片</span></template>
      <el-form label-width="150px" label-position="left">
        <el-form-item label="颜色规格" required>
          <el-popover
            v-model:visible="colorPopVisible"
            trigger="click"
            :width="476"
            placement="bottom-start"
            popper-class="pcreate__colorpop"
          >
            <template #reference>
              <button type="button" class="pcreate__colortrigger">
                <span class="pcreate__dot" :style="{ background: colorSwatch(form.colorVid) }" />
                <span>{{ currentColorName || '选择颜色' }}</span>
                <span class="pcreate__meta">{{ colorGroups.reduce((n, g) => n + (g.colors || []).length, 0) }} 个可选</span>
              </button>
            </template>

            <div class="pcreate__colorpanel">
              <aside class="pcreate__colorgroups">
                <button
                  v-for="g in colorGroups"
                  :key="g.groupId"
                  type="button"
                  class="pcreate__colorgroup"
                  :class="{ 'pcreate__colorgroup--active': g.groupId === colorGroupId }"
                  @mouseenter="colorGroupId = g.groupId"
                  @focus="colorGroupId = g.groupId"
                  @click="colorGroupId = g.groupId"
                >
                  <span class="pcreate__dot" :style="{ background: colorSwatch((g.colors || [])[0]?.vid) }" />
                  <span>{{ g.groupName }}</span>
                  <span class="pcreate__metacount">{{ (g.colors || []).length }}</span>
                </button>
              </aside>
              <div class="pcreate__colorgrid">
                <button
                  v-for="c in currentColors"
                  :key="c.vid"
                  type="button"
                  class="pcreate__coloritem"
                  :class="{ 'pcreate__coloritem--active': c.vid === form.colorVid }"
                  @click="form.colorVid = c.vid; colorPopVisible = false"
                >
                  <span class="pcreate__dot" :style="{ background: colorSwatch(c.vid) }" />
                  <span class="pcreate__colorname">{{ c.value }}</span>
                </button>
              </div>
            </div>
          </el-popover>
        </el-form-item>

        <el-form-item label="尺码规格" required class="pcreate__sizefield">
          <div class="pcreate__catrow">
            <el-select
              v-if="sizeStandardOptions.length > 1"
              v-model="sizeStandard"
              class="pcreate__group"
              placeholder="尺码标准"
              @change="sizeTypeId = sizeTypeOptions[0] || ''; form.sizeVids = []; sizeTemplateId = null"
            >
              <el-option v-for="s in sizeStandardOptions" :key="s" :label="s" :value="s" />
            </el-select>
          </div>
          <div v-if="sizeTypeOptions.length" class="pcreate__sizetypes">
            <el-radio-group
              v-model="sizeTypeId"
              size="small"
              @change="form.sizeVids = []; sizeTemplateId = null"
            >
              <el-radio-button v-for="t in sizeTypeOptions" :key="t" :value="t">
                {{ sizeTypeLabel(t) }}（{{ (sizeTree[sizeStandard]?.[t] || []).length }}）
              </el-radio-button>
            </el-radio-group>
          </div>
          <div v-if="currentSizes.length" class="pcreate__sizechecks">
            <el-checkbox
              :model-value="form.sizeVids.length === currentSizes.length && currentSizes.length > 0"
              :indeterminate="form.sizeVids.length > 0 && form.sizeVids.length < currentSizes.length"
              @change="(v: boolean) => (form.sizeVids = v ? currentSizes.map((s) => s.vid) : [])"
            >全选</el-checkbox>
            <el-checkbox-group v-model="form.sizeVids">
              <el-checkbox v-for="opt in currentSizes" :key="opt.vid" :value="opt.vid">
                {{ opt.value }}
              </el-checkbox>
            </el-checkbox-group>
            <span class="pcreate__meta">
              已选 {{ form.sizeVids.length }} 个 → {{ form.sizeVids.length }} 个 SKU
            </span>
          </div>
        </el-form-item>

        <el-form-item label="尺码表" required>
          <el-select
            v-model="sizeTemplateId"
            class="pcreate__select"
            clearable
            filterable
            :disabled="!form.sizeVids.length"
            @change="(v: number | null) => checkSizeTemplate(v)"
            :placeholder="form.sizeVids.length ? '选择已保存的尺码表' : '请先选择尺码'"
          >
            <el-option
              v-for="tpl in sizeTemplateOptions"
              :key="tpl.businessId"
              :label="`${tpl.name}（${tpl.sizeTypeName || '均码'} · ${tpl.sizes.join('/')}）`"
              :value="tpl.businessId"
            />
          </el-select>
          <span class="pcreate__meta">
            {{
              !sizeTypeId
                ? '请先选择尺码类型'
                : sizeTemplateOptions.length
                  ? `${sizeTemplateOptions.length} 个匹配「${sizeTypeId}」的模板`
                  : sizeTemplates.length
                    ? `该类目有 ${sizeTemplates.length} 个模板，但没有匹配「${sizeTypeId}」的`
                    : '该类目还没有尺码表，需先在 Temu 后台创建'
            }}
          </span>
        </el-form-item>

        <el-form-item label="轮播图（3:4）" required>
          <div class="pcreate__images">
            <div v-for="(url, i) in form.images" :key="`${i}-${url}`" class="pcreate__thumb">
              <img :src="url" alt="" />
              <span class="pcreate__thumb-idx">{{ i + 1 }}</span>
              <button type="button" class="pcreate__thumb-x" @click="removeImageAt(i)">×</button>
            </div>
            <button type="button" class="pcreate__add" @click="openPicker(true)">
              <el-icon><Picture /></el-icon>
              <span>选图</span>
            </button>
          </div>
        </el-form-item>

        <el-form-item label="素材图（1:1）">
          <div class="pcreate__images">
            <div v-if="form.materialImgUrl" class="pcreate__thumb pcreate__thumb--square">
              <img :src="form.materialImgUrl" alt="" />
            </div>
            <button type="button" class="pcreate__add" @click="openPicker(false)">
              <el-icon><Picture /></el-icon>
              <span>{{ form.materialImgUrl ? '换图' : '选图' }}</span>
            </button>
            <span class="pcreate__meta" v-if="!form.materialImgUrl && materialCropping">
              正在从首图裁切 1:1…
            </span>
            <span class="pcreate__meta" v-else-if="!form.materialImgUrl">
              留空将自动从首图裁成 1:1
            </span>
            <button v-else type="button" class="pcreate__add" @click="form.materialImgUrl = ''; materialAuto = false">
              <span>清空</span>
            </button>
          </div>
        </el-form-item>
        <el-form-item label="详情图文（3:4）">
          <div class="pcreate__images">
            <div v-for="(url, i) in decorationImages" :key="`${i}-${url}`" class="pcreate__thumb">
              <img :src="url" alt="" />
              <span class="pcreate__thumb-idx">{{ i + 1 }}</span>
              <button
                type="button"
                class="pcreate__thumb-x"
                @click="decorationImages = decorationImages.filter((_, k) => k !== i)"
              >×</button>
            </div>
            <button type="button" class="pcreate__add" @click="decoPickerVisible = true">
              <el-icon><Picture /></el-icon>
              <span>选图</span>
            </button>
          </div>
          <span class="pcreate__meta">
            每张一个楼层（3:4 / 1350×1800），按顺序展示；不选则用首张轮播图
          </span>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 第四步：属性（模板驱动） -->
    <el-card v-if="tmpl" shadow="never" class="pcreate__card">
      <template #header>
        <span class="pcreate__title">4. 商品属性</span>
        <span class="pcreate__rate">
          属性填报率 {{ fillRate.filled }}/{{ fillRate.total }}
          <el-progress
            :percentage="fillRate.percent"
            :stroke-width="6"
            :show-text="false"
            class="pcreate__ratebar"
          />
          <span class="pcreate__ratelevel">{{ fillRate.percent >= 80 ? '高' : fillRate.percent >= 50 ? '中' : '低' }}</span>
        </span>
        <el-tag v-if="missing.length" type="warning" size="small" effect="plain" class="pcreate__tag">
          还缺 {{ missing.length }} 项：{{ missing.join('、') }}
        </el-tag>
        <el-tag v-else type="success" size="small" effect="plain" class="pcreate__tag">必填已齐</el-tag>
        <el-tag
          v-for="req in categoryCfg?.extra_requirements || []"
          :key="req"
          type="warning"
          size="small"
          effect="plain"
          class="pcreate__tag"
        >{{ req }}</el-tag>
      </template>

      <el-row :gutter="12">
        <el-col
          v-for="field in fields"
          :key="field.key"
          :xs="24"
          :sm="field.type === 'select' ? 12 : 24"
          :md="field.type === 'select' ? 8 : 12"
          :lg="field.type === 'select' ? 6 : 12"
          :xl="field.type === 'select' ? 6 : 12"
        >
        <el-form-item label-width="104px" class="pcreate__attr">
          <template #label>
            <el-tooltip
              :content="field.label"
              :disabled="field.label.length <= 7"
              placement="top"
              :show-after="200"
            >
              <span class="pcreate__attrlabel" @click.prevent.stop>
                {{ field.label }}<span v-if="field.required" class="pcreate__req">*</span>
              </span>
            </el-tooltip>
          </template>
          <!-- 纯数值 -->
          <template v-if="field.type === 'number'">
            <el-input-number
              class="pcreate__attrctl"
              :model-value="field.value === '' ? undefined : Number(field.value)"
              :min="field.numberInput?.min ?? 0"
              :max="field.numberInput?.max ?? 999999"
              :precision="field.numberInput?.precision"
              @update:model-value="(v: number | undefined) => setNumber(field, v)"
            />
            <span v-if="field.unit" class="pcreate__unit">{{ field.unit }}</span>
          </template>

          <!-- 下拉 + 数值 -->
          <template v-else-if="field.type === 'select+number'">
            <div class="pcreate__inline">
              <el-select
                v-model="selected[field.key]"
                class="pcreate__inline-select"
                clearable
                filterable
                placeholder="请选择"
                @change="onFieldChange"
              >
                <el-option v-for="opt in field.options" :key="opt.value" :label="opt.label" :value="opt.value" />
              </el-select>
              <el-input-number
                :model-value="field.numberInput?.value ? Number(field.numberInput.value) : undefined"
                :min="field.numberInput?.min ?? 0"
                :max="field.numberInput?.max ?? 999999"
                :precision="field.numberInput?.precision"
                controls-position="right"
                class="pcreate__inline-num"
                @update:model-value="(v: number | undefined) => setNumberPart(field, v)"
              />
              <span v-if="field.numberInput?.unit" class="pcreate__unit">{{ field.numberInput.unit }}</span>
            </div>
          </template>

          <!-- 纯下拉 -->
          <template v-else>
            <el-select
              v-model="selected[field.key]"
              class="pcreate__attrctl"
              clearable
              filterable
              placeholder="请选择"
              @change="onFieldChange"
            >
              <el-option v-for="opt in field.options" :key="opt.value" :label="opt.label" :value="opt.value" />
            </el-select>
          </template>
        </el-form-item>
        </el-col>
      </el-row>
    </el-card>

    <!-- 第五步：产地与合规 -->
    <el-card v-if="tmpl" shadow="never" class="pcreate__card">
      <template #header><span class="pcreate__title">5. 产地与合规</span></template>
      <el-form label-width="150px" label-position="left">
        <el-form-item label="商品产地">
          <div class="pcreate__catrow">
            <el-select v-model="originCountry" class="pcreate__group" filterable placeholder="国家">
              <el-option
                v-for="c in regionsData?.origin_countries || []"
                :key="String(c.shortName || c.code)"
                :label="String(c.name)"
                :value="String(c.shortName || c.code)"
              />
            </el-select>
            <el-select v-if="originCountry === 'CN'" v-model="originProvince" class="pcreate__select" filterable placeholder="省份">
              <el-option
                v-for="pr in regionsData?.cn_provinces || []"
                :key="String(pr.code)"
                :label="String(pr.name)"
                :value="Number(pr.code)"
              />
            </el-select>
          </div>
        </el-form-item>

        <el-form-item label="制造地点">
          <el-select
            v-model="manufacturing"
            multiple
            collapse-tags
            filterable
            class="pcreate__select"
            placeholder="可选择多个（选填）"
          >
            <el-option
              v-for="c in regionsData?.manufacturing_countries || []"
              :key="String(c.shortName || c.code)"
              :label="String(c.name)"
              :value="String(c.shortName || c.code)"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="敏感属性">
          <el-switch v-model="isSensitive" active-text="是" inactive-text="否" />
          <span class="pcreate__meta">按类目配置自动带出时请勿关闭</span>
        </el-form-item>
        <el-form-item label="体积（cm）" required>
          <div class="pcreate__inline">
            <span class="pcreate__dimplabel">最长边</span>
            <el-input-number v-model="volumeInput.len" :min="1" :max="999" :disabled="!hasSize" controls-position="right" class="pcreate__dimnum" />
            <span class="pcreate__dimplabel">次长边</span>
            <el-input-number v-model="volumeInput.width" :min="1" :max="999" :disabled="!hasSize" controls-position="right" class="pcreate__dimnum" />
            <span class="pcreate__dimplabel">最短边</span>
            <el-input-number v-model="volumeInput.height" :min="1" :max="999" :disabled="!hasSize" controls-position="right" class="pcreate__dimnum" />
          </div>
        </el-form-item>
        <el-form-item label="重量（g）" required>
          <el-input-number v-model="weightInput" :min="1" :max="999999" :disabled="!hasSize" controls-position="right" class="pcreate__weightnum" />
          <span class="pcreate__meta">用于运费核算与 EPR 合规，填错可能被判违规</span>
        </el-form-item>

        <el-form-item v-if="isSensitive" label="敏感项">
          <el-checkbox-group v-model="sensitiveTypes">
            <el-checkbox v-for="s in sensitiveTypeOptions" :key="s.value" :value="s.value">{{ s.label }}</el-checkbox>
          </el-checkbox-group>
          <span v-if="!sensitiveTypeOptions.length" class="pcreate__meta">枚举未取到</span>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 服务与承诺 -->
    <el-card v-if="tmpl" shadow="never" class="pcreate__card">
      <template #header><span class="pcreate__title">服务与承诺</span></template>
      <el-form label-width="150px" label-position="left">
        <el-form-item label="承诺发货时效" required>
          <el-select v-model="form.shipmentLimit" class="pcreate__select">
            <el-option
              v-for="item in ['1个工作日', '2个工作日', '3个工作日', '5个工作日', '7个工作日', '9个工作日', '10个工作日']"
              :key="item"
              :label="item"
              :value="item"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="运费模板" required>
          <el-select
            v-model="form.freightTemplateId"
            class="pcreate__select"
            clearable
            filterable
            placeholder="选择运费模板"
          >
            <el-option
              v-for="tpl in freightTemplates"
              :key="String(tpl.freightTemplateId)"
              :label="String(tpl.templateName || tpl.freightTemplateId)"
              :value="String(tpl.freightTemplateId)"
            />
          </el-select>
          <el-button v-if="!form.freightTemplateId && env?.freightTemplateId" link type="primary" size="small"
            @click="form.freightTemplateId = String(env.freightTemplateId)">
            用默认（{{ env.freightTemplateId }}）
          </el-button>
          <span v-else-if="!freightTemplates.length" class="pcreate__meta">该站点没有可用运费模板</span>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 库存分布 -->
    <el-dialog v-model="stockDialogVisible" title="按仓库分布库存" width="min(520px, calc(100vw - 32px))">
      <el-table :data="form.warehouseIds" size="small">
        <el-table-column label="仓库">
          <template #default="scope">{{ warehouseLabel(String(scope.row)) }}</template>
        </el-table-column>
        <el-table-column label="库存" width="170">
          <template #default="scope">
            <el-input-number
              :model-value="stockPerWarehouse[String(scope.row)] || 0"
              :min="0"
              :max="999999"
              size="small"
              @update:model-value="(v: number | undefined) => (stockPerWarehouse[String(scope.row)] = v ?? 0)"
            />
          </template>
        </el-table-column>
      </el-table>
      <p class="pcreate__meta">
        合计 {{ stockTotal }} / 总库存 {{ form.stock }}
        <span v-if="stockTotal !== form.stock" class="pcreate__whnote">（不一致，提交时会以总库存为准）</span>
      </p>
      <template #footer>
        <el-button @click="distributeStockEvenly">一键平均分配</el-button>
        <el-button @click="form.stock = stockTotal || form.stock; stockDialogVisible = false">用合计作总库存</el-button>
        <el-button type="primary" @click="stockDialogVisible = false">确定</el-button>
      </template>
    </el-dialog>

    <!-- 提交 -->
    <div v-if="tmpl" class="pcreate__submit">
      <el-switch v-model="dryRun" active-text="只建草稿（不真正创建）" />
      <el-tooltip
        :disabled="canSubmit"
        placement="top"
        :content="`还差：${submitBlockers.join('；')}`"
      >
        <span>
          <el-button type="primary" :loading="creating" :disabled="!canSubmit" @click="doCreate">
            {{ dryRun ? '保存草稿' : '创建商品' }}
          </el-button>
        </span>
      </el-tooltip>
    </div>

    <el-dialog v-model="warehouseDialogVisible" title="站点与发货仓关系" width="min(560px, calc(100vw - 32px))">
      <el-table :data="env?.warehouseRoutes || []" size="small" max-height="380">
        <el-table-column label="站点 ID" width="120">
          <template #default="scope">
            <span :class="{ 'pcreate__whactive': scope.row.siteId === form.siteId }">{{ scope.row.siteId }}</span>
            <el-tag v-if="scope.row.siteId === form.siteId" size="small" type="success" class="pcreate__whtag">当前</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="发货仓">
          <template #default="scope">
            {{ scope.row.warehouseIds.map(warehouseLabel).join('、') || '未绑定' }}
          </template>
        </el-table-column>
      </el-table>
      <p class="pcreate__meta">
        {{ env?.templateName ? `路由模板：${env.templateName}` : '未取到路由模板' }}
      </p>
      <template #footer>
        <el-button @click="warehouseDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <TemuCategoryPicker
      v-model="categoryPickerVisible"
      :selected="catPath"
      @confirm="onCategoryConfirm"
    />

    <MaterialPicker
      v-model="decoPickerVisible"
      carousel
      kind="detail"
      :selected="decorationImages"
      @confirm="onPickDecoration"
    />

    <MaterialPicker
      v-model="pickerVisible"
      :carousel="pickerCarousel"
      :selected="pickerCarousel ? form.images : [form.materialImgUrl].filter(Boolean)"
      @confirm="onPick"
    />
  </div>
</template>

<style scoped>
.pcreate {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.pcreate__card :deep(.el-card__header) {
  padding: 12px 16px;
}

.pcreate__title {
  font-weight: 600;
}

.pcreate__tag {
  margin-left: 10px;
}

.pcreate__row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.pcreate__name {
  flex: 1 1 320px;
  min-width: 240px;
}

.pcreate__sitewarn {
  margin-bottom: 12px;
}

.pcreate__enwarn {
  color: #b45309;
  font-size: 12px;
  margin-left: 8px;
}

.pcreate__input--error :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px #f59e0b inset;
}

.pcreate__wh {
  flex: 1 1 380px;
  min-width: 240px;
}

.pcreate__whnote {
  color: #b45309;
}

.pcreate__whactive {
  font-weight: 600;
  color: var(--primary, #2563eb);
}

.pcreate__whtag {
  margin-left: 6px;
}

.pcreate__catbox {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.pcreate__catrow {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.pcreate__catsearch {
  flex: 1 1 380px;
  min-width: 240px;
}

.pcreate__catid {
  flex: 1 1 320px;
  min-width: 220px;
}

.pcreate__catloading {
  color: var(--muted, #6b7280);
  font-size: 12px;
}

.pcreate__catlist {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 220px;
  padding: 6px;
  overflow-y: auto;
  background: #f8fafc;
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 8px;
}

.pcreate__catitem {
  padding: 7px 10px;
  color: var(--primary, #2563eb);
  font-size: 12px;
  text-align: left;
  background: transparent;
  border: 0;
  border-radius: 6px;
  cursor: pointer;
}

.pcreate__catitem:hover {
  background: #eff6ff;
}

.pcreate__catnametext {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pcreate__tplparams {
  /* 滚动交给外层 .pcreate__tpleditor 统一处理，避免多层滚动条 */
}

.pcreate__tpleditor {
  max-height: 70vh;
  overflow-y: auto;
  padding-right: 6px;
}

.pcreate__tplrow {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.pcreate__tpllabel {
  flex: none;
  font-size: 13px;
  color: var(--muted, #6b7280);
}

.pcreate__blockers {
  margin: 8px 0 0;
  color: var(--el-color-warning, #e6a23c);
  font-size: 12px;
  line-height: 1.6;
}

.pcreate__catpath {
  display: flex;
  align-items: center;
  gap: 8px;
  text-align: left;
}

.pcreate__catpath {
  padding: 6px 10px;
  margin-bottom: 8px;
  color: var(--primary, #2563eb);
  font-size: 12px;
  background: #eff6ff;
  border-radius: 6px;
}

.pcreate__cat {
  flex: 1 1 420px;
  min-width: 260px;
}

.pcreate__site {
  width: 200px;
}

.pcreate__meta {
  color: var(--muted, #6b7280);
  font-size: 12px;
  margin-left: 8px;
}

.pcreate__inlinecheck {
  margin-left: 16px;
}

.pcreate__group {
  width: 180px;
  flex: none;
}

.pcreate__select {
  width: 280px;
}

/* 尺码规格：标准 / 类型 / 具体尺码 各占一行 */
.pcreate__sizefield :deep(.el-form-item__content) {
  display: flex !important;
  flex-direction: column !important;
  flex-wrap: nowrap !important;
  align-items: stretch !important;
  gap: 8px;
  line-height: 1.5;
}

.pcreate__sizefield :deep(.el-form-item__content) > * {
  width: 100%;
}

.pcreate__sizetypes {
  margin-top: 0;
}

.pcreate__sizechecks {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 16px;
  padding: 8px 10px;
  margin-top: 0;
  background: var(--surface-subtle, #f8fafc);
  border-radius: 6px;
}

.pcreate__sizechecks :deep(.el-checkbox-group) {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.pcreate__colortrigger {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 5px 12px;
  font-size: 13px;
  background: #fff;
  border: 1px solid var(--border, #dcdfe6);
  border-radius: 4px;
  cursor: pointer;
}

.pcreate__colortrigger:hover {
  border-color: var(--primary, #2563eb);
}

.pcreate__dot {
  flex: none;
  width: 14px;
  height: 14px;
  border: 1px solid rgba(0, 0, 0, .1);
  border-radius: 50%;
}

.pcreate__colorpanel {
  display: flex;
  gap: 10px;
  max-height: 340px;
}

.pcreate__colorgroups {
  flex: 0 0 118px;
  overflow-y: auto;
  border-right: 1px solid var(--border, #ebeef5);
}

.pcreate__colorgroup {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 8px;
  font-size: 13px;
  text-align: left;
  background: transparent;
  border: 0;
  border-radius: 4px;
  cursor: pointer;
}

.pcreate__colorgroup:hover,
.pcreate__colorgroup--active {
  color: var(--primary, #2563eb);
  background: #eff6ff;
}

.pcreate__metacount {
  margin-left: auto;
  color: var(--muted, #9ca3af);
  font-size: 11px;
}

.pcreate__colorgrid {
  flex: 1 1 auto;
  display: grid;
  /* 固定 3 列，颜色少时从左上依次排列；不再 auto-fill，避免宽度差几像素就掉成 2 列 */
  grid-template-columns: repeat(3, 104px);
  grid-auto-rows: 30px;
  gap: 2px 6px;
  align-content: start;
  justify-content: start;
  overflow-y: auto;
  padding-right: 4px;
}

.pcreate__coloritem {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 104px;
  height: 30px;
  padding: 0 8px;
  overflow: hidden;
  font-size: 13px;
  text-align: left;
  background: transparent;
  border: 0;
  border-radius: 4px;
  cursor: pointer;
}

.pcreate__coloritem:hover,
.pcreate__coloritem--active {
  color: var(--primary, #2563eb);
  background: #eff6ff;
}

.pcreate__colorname {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pcreate__attrctl {
  width: 100%;
  max-width: 190px;
}

/* 三列网格对齐：label 单行省略，行高统一，控件不撑破列宽 */
.pcreate__attr {
  align-items: center;
  margin-bottom: 14px !important;
}


.pcreate__attr :deep(.el-form-item__label) {
  padding-right: 8px;
  /* 单行省略，完整名称走 tooltip 悬浮提示 */
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 32px;
  /* 点 label 不要触发展开下拉 */
  pointer-events: none;
  display: flex;
  align-items: center;
  justify-content: flex-start;
}

.pcreate__attrlabel {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  /* label 容器是 pointer-events:none，这里要恢复才能触发 tooltip */
  pointer-events: auto;
}

.pcreate__req {
  flex: none;
  color: var(--el-color-danger, #f56c6c);
  font-weight: 600;
}

.pcreate__attr :deep(.el-form-item__content) {
  min-width: 0;
  overflow: hidden;
  line-height: 32px;
}

/* 体积/重量那两行：控件不换行，避免数字框被挤到下一行露出半个下拉 */
.pcreate__sizefield :deep(.el-form-item__content),
.el-form-item:has(.pcreate__dimnum) :deep(.el-form-item__content),
.el-form-item:has(.pcreate__weightnum) :deep(.el-form-item__content) {
  flex-wrap: nowrap;
}

.pcreate__dimplabel {
  flex: none;
  color: var(--muted, #6b7280);
  font-size: 12px;
}

.pcreate__inline {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  width: auto;
  max-width: 100%;
  flex-wrap: nowrap;
}

.pcreate__inline-select {
  flex: none;
  width: 168px;
}

.pcreate__inline-num {
  flex: none;
  width: 120px;
}

.pcreate__weightnum {
  flex: none;
  width: 148px;
}

/* 体积那三格（最长边/次长边/最短边）也用统一宽度 */
.pcreate__dimnum {
  flex: none;
  width: 104px;
}

.pcreate__rate {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: 12px;
  color: var(--muted, #6b7280);
  font-size: 12px;
}

.pcreate__ratebar {
  width: 90px;
}

.pcreate__ratelevel {
  color: #059669;
  font-weight: 600;
}

.pcreate__num {
  width: 140px;
  margin-left: 8px;
}

.pcreate__unit {
  margin-left: 6px;
  color: var(--muted, #6b7280);
  font-size: 12px;
}

.pcreate__images {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.pcreate__thumb {
  position: relative;
  width: 84px;
  height: 112px;
  overflow: hidden;
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 6px;
}

.pcreate__thumb--square {
  height: 84px;
}

.pcreate__thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.pcreate__thumb-idx {
  position: absolute;
  left: 3px;
  bottom: 3px;
  min-width: 16px;
  padding: 0 4px;
  color: #fff;
  font-size: 10px;
  line-height: 16px;
  text-align: center;
  background: rgba(0, 0, 0, .55);
  border-radius: 8px;
}

.pcreate__thumb-x {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 18px;
  height: 18px;
  line-height: 1;
  color: #fff;
  background: rgba(0, 0, 0, .55);
  border: 0;
  border-radius: 50%;
  cursor: pointer;
}

.pcreate__add {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  width: 84px;
  height: 112px;
  color: var(--muted, #6b7280);
  font-size: 12px;
  background: var(--surface-subtle, #f8fafc);
  border: 1px dashed var(--border, #cbd5e1);
  border-radius: 6px;
  cursor: pointer;
}

.pcreate__add:hover {
  color: var(--primary, #2563eb);
  border-color: var(--primary, #2563eb);
}

.pcreate__submit {
  position: sticky;
  bottom: 0;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  padding: 10px 16px;
  background: #fff;
  border-top: 1px solid var(--border, #e5e7eb);
}

</style>
