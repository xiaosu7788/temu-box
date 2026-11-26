<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import {
  cancelProductBatch,
  getCategoryPath,
  getFormTemplate,
  getProductBatchStatus,
  getProductConstants,
  getProductEnv,
  getProductRegions,
  getProductSites,
  getProductTemplate,
  getSizeTemplates,
  listFormTemplates,
  describeProduct,
  startProductBatch,
  cropMaterial,
} from '../api'
import { normalizeVolumePayload, normalizeWeightPayload } from '../unitConvert'
import { confirmAction, notifyError, notifySuccess, notifyWarning } from '../feedback'
import type { ProductBatchState } from '../types'
import ColorPicker from './ColorPicker.vue'
import TemuCategoryPicker from './TemuCategoryPicker.vue'
import MaterialPicker from './MaterialPicker.vue'

/** 一张卡片 = 一个待创建商品 */
interface BatchCard {
  key: string
  templateId: number | null
  templateName: string
  templateValues: Record<string, any> | null
  colorGroups: Array<{ groupId: number; groupName: string; colors: Array<{ vid: number; value: string }> }>
  name: string
  enName: string
  extCode: string
  colorVid: number | null
  images: string[]
  materialImgUrl: string
  decorationImages: string[]
  skuPrice: number
  stock: number
  busy: boolean
  selected: boolean
  /** 站点与发货仓：和手动创建一样，每张卡片独立 */
  siteId: number
  warehouseIds: string[]
  freightTemplateId: string
}

const EN_NAME_MIN = 20
const MAX_CARDS = 50

const templates = ref<Array<{ id: number; name: string; note: string; field_count: number }>>([])
const cards = ref<BatchCard[]>([])
const cardCount = ref(5)
const bulkTemplateId = ref<number | null>(null)
const batch = ref<ProductBatchState>({ status: 'idle' })
const starting = ref(false)
let pollTimer: number | null = null

/* ---------- 选图 ---------- */
/* ---------- 单卡编辑（类目/属性/产地/体积/尺码/服务承诺） ---------- */
const editCardVisible = ref(false)
const editCardIndex = ref(-1)
const editCardRaw = ref<Record<string, any>>({})
const editCardFields = ref<Array<any>>([])
const editCardLoading = ref(false)
const editCatPath = ref<number[]>([])
const editCatNames = ref<string[]>([])
const editCatPickerVisible = ref(false)
const editColorGroups = ref<Array<any>>([])

const editRegions = ref<any>(null)
const editSensitiveTypes = ref<Array<{ label: string; value: number }>>([])
const editSizeTree = ref<Record<string, Record<string, any[]>>>({})
const editSizeStandard = ref('')
const editSizeType = ref('')
const editSizeTemplates = ref<any[]>([])
/** 编辑时的站点环境（仓库名 / 运费模板名） */
const editEnv = ref<any>(null)
const editSites = ref<Array<Record<string, unknown>>>([])

const SIZE_TYPE_TO_GST: Record<string, number> = { 均码: 1, 常规: 2, Tall: 3 }
const SHIPMENT_OPTIONS = ['1个工作日', '2个工作日', '3个工作日', '5个工作日', '7个工作日', '9个工作日', '10个工作日']

const pickerVisible = ref(false)
const pickerCarousel = ref(true)
const pickerKind = ref<'carousel' | 'material' | 'detail'>('carousel')
const pickerSelection = ref<string[]>([])
let pickerTarget: { index: number; field: 'images' | 'materialImgUrl' | 'decorationImages' } | null = null

function openPicker(index: number, field: 'images' | 'materialImgUrl' | 'decorationImages') {
  const card = cards.value[index]
  if (!card) return
  pickerTarget = { index, field }
  pickerCarousel.value = field !== 'materialImgUrl'
  // 详情图文的规格与轮播图不同（480×480 / 3MB / 0.5~2），不能复用 carousel 的 3:4 判据
  pickerKind.value =
    field === 'decorationImages' ? 'detail' : field === 'materialImgUrl' ? 'material' : 'carousel'
  pickerSelection.value = field === 'materialImgUrl'
    ? (card.materialImgUrl ? [card.materialImgUrl] : [])
    : [...(field === 'images' ? card.images : card.decorationImages)]
  pickerVisible.value = true
}

/** 读 URL 图片的实际尺寸 */
function loadImageSize(url: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve) => {
    const img = new Image()
    img.onload = () => resolve({ width: img.naturalWidth, height: img.naturalHeight })
    img.onerror = () => resolve({ width: 0, height: 0 })
    img.src = thumbUrl(url)
  })
}

/** URL 的缩略图版本（CDN 支持 imageMogr2，避免拉原图只为量尺寸） */
function thumbUrl(url: string): string {
  if (!url) return ''
  if (url.includes('?')) return url
  return `${url}?imageMogr2/thumbnail/x400`
}

/**
 * 选完轮播图后自动把首图裁成 1:1 当素材图（与手动创建一致）。
 * 素材图留空时服务端也会 autoMaterialImg，但本地先裁能给用户即时反馈。
 */
async function autoCropMaterialForCard(card: BatchCard, sourceUrl?: string) {
  const first = sourceUrl || card.images[0]
  if (!first || card.materialImgUrl) return
  try {
    const { width, height } = await loadImageSize(first)
    if (width < 800 || height < 800) return
    const dx = Math.max(0, Math.round((width - 800) / 2))
    const dy = Math.max(0, Math.round((height - 800) / 2))
    const r = await cropMaterial(first, { width: 800, height: 800, dx, dy })
    if (r.url) card.materialImgUrl = r.url
  } catch {
    // 自动裁失败就保持留空，交给服务端的 autoMaterialImg
  }
}

function onPick(urls: string[]) {
  if (!pickerTarget) return
  const card = cards.value[pickerTarget.index]
  if (!card) return
  if (pickerTarget.field === 'materialImgUrl') card.materialImgUrl = urls[0] || ''
  else if (pickerTarget.field === 'images') {
    card.images = [...urls]
    void autoCropMaterialForCard(card, urls[0])
  } else card.decorationImages = [...urls]
  pickerTarget = null
}

/* ---------- 批量卡片图片拖拽排序 ---------- */
type DragInfo = { card: number; field: 'images' | 'decorationImages'; img: number }
const dragInfo = ref<DragInfo | null>(null)
const dragOver = ref<{ card: number; img: number } | null>(null)

function onImgDragStart(card: number, field: 'images' | 'decorationImages', img: number, e: DragEvent) {
  dragInfo.value = { card, field, img }
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', `${card}:${field}:${img}`)
  }
}

function onImgDragOver(card: number, img: number) {
  dragOver.value = { card, img }
}

/** 只允许在同一张卡的同一种图列表内重排（跨卡拖没有业务意义） */
function onImgDrop(card: number, field: 'images' | 'decorationImages', img: number) {
  const src = dragInfo.value
  if (src && src.card === card && src.field === field && src.img !== img) {
    const list = [...cards.value[card][field]]
    const [moved] = list.splice(src.img, 1)
    list.splice(img, 0, moved)
    cards.value[card][field] = list
  }
  resetImgDrag()
}

function resetImgDrag() {
  dragInfo.value = null
  dragOver.value = null
}

function removeImage(index: number, field: 'images' | 'decorationImages', i: number) {
  const card = cards.value[index]
  if (!card) return
  const list = field === 'images' ? card.images : card.decorationImages
  const next = list.filter((_, k) => k !== i)
  if (field === 'images') card.images = next
  else card.decorationImages = next
}

/* ---------- 卡片 ---------- */
function makeCard(): BatchCard {
  return {
    key: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    templateId: null,
    templateName: '',
    templateValues: null,
    colorGroups: [],
    name: '',
    enName: '',
    extCode: '',
    colorVid: null,
    images: [],
    materialImgUrl: '',
    decorationImages: [],
    skuPrice: 39.9,
    stock: 100,
    busy: false,
    selected: false,
    siteId: 100,
    warehouseIds: [],
    freightTemplateId: '',
  }
}

function createCards() {
  const n = Math.max(1, Math.min(MAX_CARDS, Math.floor(cardCount.value) || 1))
  const next: BatchCard[] = []
  for (let i = 0; i < n; i += 1) {
    const card = cards.value[i] ? { ...cards.value[i] } : makeCard()
    next.push(card)
  }
  cards.value = next
  // 新建后如果选了批量模板，顺手套上
  if (bulkTemplateId.value) void applyTemplateToAll()
}

function addCard() {
  if (cards.value.length >= MAX_CARDS) {
    notifyWarning(`单批最多 ${MAX_CARDS} 个`)
    return
  }
  cards.value = [...cards.value, makeCard()]
}

function removeCard(index: number) {
  cards.value = cards.value.filter((_, i) => i !== index)
}

const selectedCount = computed(() => cards.value.filter((c) => c.selected).length)
const allSelected = computed(() => cards.value.length > 0 && selectedCount.value === cards.value.length)

function toggleSelectAll(v: boolean) {
  cards.value = cards.value.map((c) => ({ ...c, selected: v }))
}

async function removeSelected() {
  if (!selectedCount.value) return
  const ok = await confirmAction(`删除选中的 ${selectedCount.value} 张卡片？`, '批量删除')
  if (!ok) return
  cards.value = cards.value.filter((c) => !c.selected)
}

/** 套模板：拉模板内容 + 该类目的颜色选项 */
async function applyTemplate(card: BatchCard, templateId: number | null) {
  card.templateId = templateId
  card.templateValues = null
  card.colorGroups = []
  card.colorVid = null
  if (!templateId) {
    card.templateName = ''
    return
  }
  const meta = templates.value.find((x) => x.id === templateId)
  card.templateName = meta?.name || ''
  card.busy = true
  try {
    const r = await getFormTemplate(templateId)
    card.templateValues = r.values as Record<string, any>
    const v = card.templateValues
    // 颜色选项要按类目取
    const catPath = Array.isArray(v.catPath) ? v.catPath.map(Number) : []
    const catId = catPath[catPath.length - 1]
    if (catId) {
      const d = await describeProduct(catId, (v.selected || {}) as Record<string, string | number>)
      card.colorGroups = (d.colorGroups as any) || []
    }
    if (typeof v.skuPrice === 'number') card.skuPrice = v.skuPrice
    if (typeof v.stock === 'number') card.stock = v.stock
    // 站点与发货仓：模板里存过就要带过来，否则用户得每张卡重新选一遍
    if (v.siteId) card.siteId = Number(v.siteId)
    if (Array.isArray(v.warehouseIds) && v.warehouseIds.length) {
      card.warehouseIds = v.warehouseIds.map(String)
    }
  } catch (error) {
    notifyError(error)
  } finally {
    card.busy = false
  }
}

async function onTemplateChange(index: number, templateId: number | null) {
  const card = cards.value[index]
  if (!card) return
  await applyTemplate(card, templateId)
}

async function applyTemplateToAll() {
  if (!bulkTemplateId.value) {
    notifyWarning('请先选择要批量应用的模板')
    return
  }
  const tpl = bulkTemplateId.value
  await Promise.all(cards.value.map((c) => applyTemplate(c, tpl)))
  notifySuccess(`已给 ${cards.value.length} 个任务设置模板「${templates.value.find((x) => x.id === tpl)?.name || tpl}」`)
}

/** 单张卡的校验问题 */
function problemsOf(card: BatchCard): string[] {
  const out: string[] = []
  if (!card.templateId) out.push('未选模板')
  if (!card.name.trim()) out.push('缺标题')
  if (card.enName.trim().length < EN_NAME_MIN) out.push(`英文名不足 ${EN_NAME_MIN} 字符`)
  if (!card.colorVid) out.push('未选颜色')
  if (!card.images.length) out.push('缺轮播图')
  if (!(card.skuPrice > 0)) out.push('缺价格')
  return out
}

const readyCount = computed(() => cards.value.filter((c) => problemsOf(c).length === 0).length)
const canStart = computed(
  () => cards.value.length > 0 && readyCount.value === cards.value.length && batch.value.status !== 'running',
)

/** 卡片 → 提交用的 form（模板值 + 本卡差异） */
function buildItem(card: BatchCard) {
  const v = card.templateValues || {}
  return {
    ...v,
    siteId: card.siteId || v.siteId || 100,
    warehouseIds: card.warehouseIds.length ? card.warehouseIds : undefined,
    freightTemplateId: card.freightTemplateId || v.freightTemplateId || undefined,
    name: card.name.trim(),
    enName: card.enName.trim(),
    extCode: card.extCode.trim(),
    colorVid: card.colorVid,
    images: [...card.images],
    materialImgUrl: card.materialImgUrl || undefined,
    decorationImages: [...card.decorationImages],
    skuPrice: card.skuPrice,
    stock: card.stock,
    // 模板里存的是 sizeTemplateSourceId（蓝本），sizeTemplateId 是旧字段名
    sizeTemplateSourceId: v.sizeTemplateSourceId || v.sizeTemplateId || undefined,
    generalSizeType: v.generalSizeType || undefined,
  }
}

/* ---------- 单卡编辑逻辑 ---------- */
async function refreshEditFields() {
  const catId = editCatPath.value[editCatPath.value.length - 1]
  if (!catId) {
    editCardFields.value = []
    editColorGroups.value = []
    editSizeTree.value = {}
    return
  }
  const selected = (editCardRaw.value.selected || {}) as Record<string, string | number>
  const [d, st] = await Promise.all([
    describeProduct(catId, selected),
    // 尺码表按「类目 + 站点」下发，不传站点可能拿到别的站点的表甚至空列表
    getSizeTemplates(catId, editCardRaw.value.siteId || undefined).catch(() => ({ list: [] })),
  ])
  editCardFields.value = (d.fields as any) || []
  editColorGroups.value = (d.colorGroups as any) || []
  editSizeTree.value = (d.sizeTree as any) || {}
  editSizeTemplates.value = (st as any).list || []
  // 用已存 sizeVids 反推标准/类型
  const saved = Array.isArray(editCardRaw.value.sizeVids) ? editCardRaw.value.sizeVids.map(Number) : []
  editSizeStandard.value = ''
  editSizeType.value = ''
  if (saved.length) {
    for (const [std, types] of Object.entries(editSizeTree.value)) {
      for (const [ty, list] of Object.entries(types || {})) {
        if ((list || []).some((x: any) => saved.includes(Number(x.vid)))) {
          editSizeStandard.value = std
          editSizeType.value = ty
          break
        }
      }
      if (editSizeStandard.value) break
    }
  }
  if (!editSizeStandard.value) {
    editSizeStandard.value = Object.keys(editSizeTree.value)[0] || ''
    editSizeType.value = Object.keys(editSizeTree.value[editSizeStandard.value] || {})[0] || ''
  }
}

async function openCardEditor(index: number) {
  const card = cards.value[index]
  if (!card) return
  editCardIndex.value = index
  // 必须先铺默认嵌套结构，否则模板里的 editCardRaw.volume.len / .origin.x / .selected[k]
  // 会往 undefined 上写，Vue 反复重渲染直接把页面卡死
  const saved = JSON.parse(JSON.stringify(card.templateValues || {}))
  const merged: Record<string, any> = Object.assign(
    {
      selected: {},
      origin: {},
      manufacturing: [],
      volume: { len: 10, width: 9, height: 8 },
      weight: { value: 200 },
      sizeVids: [],
      sensitiveTypes: [],
      sensitiveList: [],
      isSensitive: false,
      shipmentLimit: '2个工作日',
      skuClassification: 1,
      pieces: 2,
    },
    saved,
  )
  // saved 里可能是 null，逐个兜一遍
  if (!merged.selected || typeof merged.selected !== 'object') merged.selected = {}
  if (!merged.origin || typeof merged.origin !== 'object') merged.origin = {}
  if (!merged.volume || typeof merged.volume !== 'object') merged.volume = { len: 10, width: 9, height: 8 }
  if (!merged.weight || typeof merged.weight !== 'object') merged.weight = { value: 200 }
  if (!Array.isArray(merged.manufacturing)) merged.manufacturing = []
  if (!Array.isArray(merged.sizeVids)) merged.sizeVids = []
  if (!Array.isArray(merged.sensitiveTypes)) merged.sensitiveTypes = []
  editCardRaw.value = merged
  editCardRaw.value.siteId = card.siteId || editCardRaw.value.siteId || 100
  editCardRaw.value.freightTemplateId = card.freightTemplateId || editCardRaw.value.freightTemplateId || ''
  // 模板存的是 sizeTemplateSourceId（蓝本 id），下拉绑的是 sizeTemplateId；
  // 不做这层映射，套用模板后「尺码表」永远是空的
  if (!editCardRaw.value.sizeTemplateId && editCardRaw.value.sizeTemplateSourceId) {
    editCardRaw.value.sizeTemplateId = Number(editCardRaw.value.sizeTemplateSourceId)
  }
  editCatPath.value = Array.isArray(editCardRaw.value.catPath) ? editCardRaw.value.catPath.map(Number) : []
  editCatNames.value = []
  editCardLoading.value = true
  try {
    const jobs: Array<Promise<unknown>> = []
    if (!editRegions.value) jobs.push(getProductRegions().then((r) => { editRegions.value = r }))
    if (!editSensitiveTypes.value.length) {
      jobs.push(getProductConstants().then((c) => { editSensitiveTypes.value = c.sensitive_types || [] }))
    }
    if (!editSites.value.length) {
      jobs.push(getProductSites().then((r) => { editSites.value = (r as any) || [] }).catch(() => { editSites.value = [] }))
    }
    jobs.push(
      getProductEnv(card.siteId || undefined)
        .then((e) => { editEnv.value = e })
        .catch(() => { editEnv.value = null }),
    )
    if (editCatPath.value.length) {
      jobs.push(getCategoryPath(editCatPath.value).then((r) => {
        const hit = r.list.find((x: any) => (x.catPath || []).join('>') === editCatPath.value.join('>')) || r.list[0]
        editCatNames.value = hit ? hit.names : []
      }).catch(() => { editCatNames.value = [] }))
    }
    await Promise.all(jobs)
    await refreshEditFields()
  } catch (error) {
    notifyError(error)
  } finally {
    editCardLoading.value = false
    editCardVisible.value = true
  }
}

async function onEditCategoryConfirm(payload: { catPath: number[]; names: string[] }) {
  editCatPath.value = payload.catPath
  editCatNames.value = payload.names
  // 换类目：属性作废
  editCardRaw.value.selected = {}
  const tpl = await getProductTemplate(payload.catPath[payload.catPath.length - 1]).catch(() => null)
  if (tpl) editCardRaw.value.catPath = payload.catPath
  await refreshEditFields()
}

/** 编辑弹窗里切站点：重拉该站点的仓库与运费模板 */
async function onEditSiteChange(siteId: number) {
  editCardRaw.value.siteId = siteId
  editEnv.value = null
  try {
    editEnv.value = await getProductEnv(siteId, true)
  } catch {
    editEnv.value = null
  }
  const card = cards.value[editCardIndex.value]
  if (card) card.siteId = siteId
}

async function saveCardEditor() {
  const card = cards.value[editCardIndex.value]
  if (!card) return
  const next = JSON.parse(JSON.stringify(editCardRaw.value))
  next.catPath = editCatPath.value
  next.sizeTemplateId = next.sizeTemplateId || undefined
  // 提交链路读的是 sizeTemplateSourceId，编辑下拉改的是 sizeTemplateId，两个都要落
  next.sizeTemplateSourceId = next.sizeTemplateId || next.sizeTemplateSourceId || undefined
  next.generalSizeType = SIZE_TYPE_TO_GST[editSizeType.value] || undefined
  // 编辑改的是 cm/g，保存前按它重算平台单位
  if (next.volume) next.volume = normalizeVolumePayload(next.volume)
  if (next.weight) next.weight = normalizeWeightPayload(next.weight)
  card.templateValues = next
  if (next.siteId) card.siteId = Number(next.siteId)
  if (Array.isArray(next.warehouseIds) && next.warehouseIds.length) {
    card.warehouseIds = next.warehouseIds.map(String)
  }
  if (next.freightTemplateId) card.freightTemplateId = String(next.freightTemplateId)
  card.templateName = templates.value.find((x) => x.id === card.templateId)?.name || card.templateName
  editCardVisible.value = false
  notifySuccess('卡片参数已更新')
}

const editSizes = computed(
  () => editSizeTree.value[editSizeStandard.value]?.[editSizeType.value] || [],
)

const editSizeTplOptions = computed(() => {
  const gst = SIZE_TYPE_TO_GST[editSizeType.value]
  if (!gst) return []
  return editSizeTemplates.value.filter((x) => Number(x.generalSizeType) === gst)
})

function setEditNumber(field: any, v: number | undefined) {
  const key = `input_${field.refPid ?? field.key}`
  if (v === undefined || v === null) delete editCardRaw.value.selected[key]
  else editCardRaw.value.selected[key] = v
}

/* ---------- 提交 ---------- */
async function start() {
  if (!canStart.value) return
  const bad = cards.value.map((c, i) => ({ i, p: problemsOf(c) })).filter((x) => x.p.length)
  if (bad.length) {
    notifyWarning(`还有 ${bad.length} 个任务未填完整`)
    return
  }
  const ok = await confirmAction(
    `将依次创建 ${cards.value.length} 个商品，每个间隔约 20 秒（合计约 ${Math.ceil((cards.value.length * 20) / 60)} 分钟）。开始吗？`,
    '一键批量创建',
  )
  if (!ok) return

  starting.value = true
  try {
    const items = cards.value.map(buildItem)
    batch.value = await startProductBatch(items, false)
    notifySuccess('批量任务已启动，可以离开此页面')
    startPolling()
  } catch (error) {
    notifyError(error)
  } finally {
    starting.value = false
  }
}

function startPolling() {
  stopPolling()
  pollTimer = window.setInterval(async () => {
    try {
      batch.value = await getProductBatchStatus()
      if (batch.value.status !== 'running') stopPolling()
    } catch {
      stopPolling()
    }
  }, 5000)
}

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

async function cancel() {
  const ok = await confirmAction('停止批量创建？已提交的不会回滚。', '停止任务')
  if (!ok) return
  try {
    batch.value = await cancelProductBatch()
    stopPolling()
  } catch (error) {
    notifyError(error)
  }
}

const progress = computed(() => {
  const total = batch.value.total || 0
  if (!total) return 0
  return Math.round(((batch.value.done || 0) / total) * 100)
})

const props = defineProps<{ active?: boolean }>()

async function refreshTemplates() {
  try {
    const r = await listFormTemplates()
    templates.value = r.items
  } catch {
    templates.value = []
  }
}

// 在「手动创建」里保存/删除模板后切回来，必须重新拉一次，
// 否则看到的是页面初次加载时的旧列表（用户会以为没保存成功）
watch(
  () => props.active,
  (now) => {
    if (now) void refreshTemplates()
  },
)

onMounted(async () => {
  try {
    const r = await listFormTemplates()
    templates.value = r.items
  } catch {
    templates.value = []
  }
  try {
    batch.value = await getProductBatchStatus()
    if (batch.value.status === 'running') startPolling()
  } catch {
    /* 没有历史任务 */
  }
  createCards()
})

onUnmounted(stopPolling)
</script>

<template>
  <div class="batch">
    <!-- 工具条 -->
    <el-card shadow="never" class="batch__bar">
      <div class="batch__tools">
        <span class="batch__label">新建任务</span>
        <el-input-number v-model="cardCount" :min="1" :max="50" />
        <el-button @click="createCards">生成</el-button>
        <el-button text @click="addCard">+ 加一个</el-button>
        <el-divider direction="vertical" />
        <span class="batch__label">批量设模板</span>
        <el-select v-model="bulkTemplateId" class="batch__tplselect" clearable filterable placeholder="选择模板">
          <el-option
            v-for="t in templates"
            :key="t.id"
            :label="`${t.name}${t.note ? ` · ${t.note}` : ''}`"
            :value="t.id"
          />
        </el-select>
        <el-button :disabled="!bulkTemplateId || !cards.length" @click="applyTemplateToAll">应用到全部</el-button>
        <el-divider direction="vertical" />
        <el-checkbox
          v-if="cards.length"
          :model-value="allSelected"
          :indeterminate="selectedCount > 0 && !allSelected"
          @change="(v: boolean) => toggleSelectAll(v)"
        >全选</el-checkbox>
        <el-button v-if="selectedCount" type="danger" plain @click="removeSelected">
          删除选中（{{ selectedCount }}）
        </el-button>
      </div>
    </el-card>

    <el-empty v-if="!cards.length" description="先在上方选数量并点「生成」" :image-size="60" />

    <!-- 卡片列表 -->
    <div v-else class="batch__list">
      <el-card v-for="(card, i) in cards" :key="card.key" shadow="never" class="batch__card">
        <template #header>
          <div class="batch__cardhead">
            <el-checkbox v-model="card.selected" class="batch__pick" />
            <span class="batch__idx">#{{ i + 1 }}</span>
            <el-select
              v-model="card.templateId"
              class="batch__cardtpl"
              clearable
              filterable
              placeholder="选择模板"
              :loading="card.busy"
              @change="(v: number | null) => onTemplateChange(i, v)"
            >
              <el-option v-for="t in templates" :key="t.id" :label="t.name" :value="t.id" />
            </el-select>
            <el-tag v-if="!problemsOf(card).length" type="success" size="small" effect="plain">就绪</el-tag>
            <el-tooltip v-else :content="problemsOf(card).join('、')" placement="top">
              <el-tag type="warning" size="small" effect="plain">缺 {{ problemsOf(card).length }} 项</el-tag>
            </el-tooltip>
            <el-button text class="batch__rm" @click="openCardEditor(i)">编辑参数</el-button>
            <el-button text type="danger" @click="removeCard(i)">移除</el-button>
          </div>
        </template>

        <el-form label-width="92px" label-position="left" size="small">
          <el-form-item label="标题" required>
            <el-input v-model="card.name" maxlength="250" show-word-limit placeholder="商品中文名" />
          </el-form-item>
          <el-form-item label="英文名" required>
            <el-input v-model="card.enName" maxlength="250" show-word-limit :placeholder="`至少 ${EN_NAME_MIN} 字符`" />
          </el-form-item>
          <el-form-item label="货号">
            <el-input v-model="card.extCode" placeholder="选填，用于对账" />
          </el-form-item>
          <el-form-item label="颜色" required>
            <ColorPicker v-model="card.colorVid" :groups="card.colorGroups" placeholder="选择颜色" />
          </el-form-item>
          <el-form-item label="价格 / 库存">
            <div class="batch__row">
              <el-input-number v-model="card.skuPrice" :min="0.01" :max="999999" :precision="2" controls-position="right" />
              <el-input-number v-model="card.stock" :min="0" :max="999999" controls-position="right" />
            </div>
          </el-form-item>
          <el-form-item label="轮播图" required>
            <div class="batch__imgs">
              <div
                v-for="(url, k) in card.images"
                :key="`${k}-${url}`"
                class="batch__thumb"
                :class="{
                  'batch__thumb--dragging': dragInfo && dragInfo.card === i && dragInfo.field === 'images' && dragInfo.img === k,
                  'batch__thumb--over': dragOver && dragOver.card === i && dragOver.img === k,
                }"
                draggable="true"
                @dragstart="onImgDragStart(i, 'images', k, $event)"
                @dragover.prevent="onImgDragOver(i, k)"
                @drop.prevent="onImgDrop(i, 'images', k)"
                @dragend="resetImgDrag"
              >
                <img :src="url" alt="" />
                <span class="batch__thumbidx">{{ k + 1 }}</span>
                <button type="button" class="batch__thumbx" @click="removeImage(i, 'images', k)">×</button>
              </div>
              <button type="button" class="batch__imgadd" @click="openPicker(i, 'images')">选图</button>
            </div>
          </el-form-item>
          <el-form-item label="素材图">
            <div class="batch__imgs">
              <div v-if="card.materialImgUrl" class="batch__thumb batch__thumb--sq">
                <img :src="card.materialImgUrl" alt="" />
                <button type="button" class="batch__thumbx" @click="card.materialImgUrl = ''">×</button>
              </div>
              <button type="button" class="batch__imgadd batch__imgadd--sq" @click="openPicker(i, 'materialImgUrl')">
                {{ card.materialImgUrl ? '换图' : '选图' }}
              </button>
            </div>
          </el-form-item>
          <el-form-item label="详情图文">
            <div class="batch__imgs">
              <div
                v-for="(url, k) in card.decorationImages"
                :key="`d-${k}-${url}`"
                class="batch__thumb"
                :class="{
                  'batch__thumb--dragging': dragInfo && dragInfo.card === i && dragInfo.field === 'decorationImages' && dragInfo.img === k,
                  'batch__thumb--over': dragOver && dragOver.card === i && dragOver.img === k,
                }"
                draggable="true"
                @dragstart="onImgDragStart(i, 'decorationImages', k, $event)"
                @dragover.prevent="onImgDragOver(i, k)"
                @drop.prevent="onImgDrop(i, 'decorationImages', k)"
                @dragend="resetImgDrag"
              >
                <img :src="url" alt="" />
                <span class="batch__thumbidx">{{ k + 1 }}</span>
                <button type="button" class="batch__thumbx" @click="removeImage(i, 'decorationImages', k)">×</button>
              </div>
              <button type="button" class="batch__imgadd" @click="openPicker(i, 'decorationImages')">选图</button>
            </div>
          </el-form-item>
        </el-form>
      </el-card>
    </div>

    <!-- 提交 -->
    <div v-if="cards.length" class="batch__submit">
      <span class="batch__meta">
        {{ readyCount }} / {{ cards.length }} 个就绪
        <template v-if="readyCount === cards.length">
          · 预计 {{ Math.ceil((cards.length * 20) / 60) }} 分钟
        </template>
      </span>
      <el-button type="primary" :loading="starting" :disabled="!canStart" @click="start">
        一键开始批量创建（{{ cards.length }} 个）
      </el-button>
    </div>

    <!-- 运行进度 -->
    <el-card v-if="batch.status !== 'idle'" shadow="never" class="batch__progress">
      <div class="batch__proghead">
        <span>批量任务：{{ batch.status === 'running' ? '执行中' : '已结束' }}</span>
        <el-button v-if="batch.status === 'running'" size="small" @click="cancel">停止</el-button>
      </div>
      <el-progress :percentage="progress" :status="batch.status === 'running' ? undefined : 'success'" />
      <p class="batch__meta">
        成功 {{ batch.succeeded || 0 }} · 失败 {{ batch.failed || 0 }} · 共 {{ batch.total || 0 }}
        <template v-if="batch.current">· 当前：{{ batch.current }}</template>
      </p>
      <el-table v-if="batch.items?.length" :data="batch.items" size="small" max-height="240">
        <el-table-column prop="index" label="#" width="52" />
        <el-table-column prop="name" label="商品" min-width="160" show-overflow-tooltip />
        <el-table-column label="状态" width="90">
          <template #default="scope">
            <el-tag
              :type="scope.row.status === 'ok' ? 'success' : scope.row.status === 'failed' ? 'danger' : 'info'"
              size="small"
              effect="plain"
            >{{ scope.row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="error" label="原因" min-width="180" show-overflow-tooltip />
      </el-table>
    </el-card>

    <!-- 单卡参数编辑 -->
    <el-dialog v-model="editCardVisible" :title="`编辑卡片 #${editCardIndex + 1} 的参数`" width="min(760px, calc(100vw - 32px))">
      <div v-loading="editCardLoading" class="batch__editor">
        <el-divider content-position="left">经营站点</el-divider>
        <el-form label-width="112px" label-position="left" size="small">
          <el-form-item label="站点">
            <el-select
              :model-value="editCardRaw.siteId || cards[editCardIndex]?.siteId"
              class="batch__w"
              filterable
              placeholder="选择站点"
              @change="(v: number) => onEditSiteChange(v)"
            >
              <el-option
                v-for="s in editSites"
                :key="String(s.siteId)"
                :label="String(s.siteName || s.siteId)"
                :value="Number(s.siteId)"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="发货仓">
            <el-select
              :model-value="cards[editCardIndex]?.warehouseIds || []"
              multiple
              collapse-tags
              filterable
              class="batch__w"
              placeholder="选择发货仓"
              @update:model-value="(v: string[]) => { if (cards[editCardIndex]) cards[editCardIndex].warehouseIds = v }"
            >
              <el-option
                v-for="w in editEnv?.warehouses || []"
                :key="String(w.warehouseId)"
                :label="String(w.warehouseName || w.warehouseId)"
                :value="String(w.warehouseId)"
              />
            </el-select>
            <p v-if="editEnv?.warehouses?.length" class="batch__meta">
              该站点可用：{{ (editEnv.warehouses || []).map((w: any) => w.warehouseName).join('、') }}
            </p>
          </el-form-item>
        </el-form>

        <el-divider content-position="left">类目</el-divider>
        <div class="batch__catrow">
          <span class="batch__catname">{{ editCatNames.length ? editCatNames.join(' > ') : (editCatPath.length ? editCatPath.join(' > ') : '未选择类目') }}</span>
          <el-button size="small" @click="editCatPickerVisible = true">选择类目</el-button>
        </div>

        <template v-if="editCardFields.length">
          <el-divider content-position="left">商品属性（{{ editCardFields.length }} 项）</el-divider>
          <el-form label-width="112px" label-position="left" size="small">
            <el-form-item v-for="f in editCardFields" :key="f.key" :label="f.label" :required="f.required">
              <el-select
                v-if="f.type === 'select'"
                v-model="editCardRaw.selected[f.key]"
                class="batch__w"
                clearable
                filterable
                placeholder="请选择"
              >
                <el-option v-for="o in f.options" :key="o.value" :label="o.label" :value="o.value" />
              </el-select>
              <div v-else-if="f.type === 'select+number'" class="batch__row">
                <el-select v-model="editCardRaw.selected[f.key]" class="batch__w" clearable filterable>
                  <el-option v-for="o in f.options" :key="o.value" :label="o.label" :value="o.value" />
                </el-select>
                <el-input-number
                  :model-value="f.numberInput?.value ? Number(f.numberInput.value) : undefined"
                  :min="f.numberInput?.min ?? 0"
                  :max="f.numberInput?.max ?? 999999"
                  controls-position="right"
                  @update:model-value="(v: number | undefined) => setEditNumber(f, v)"
                />
                <span v-if="f.numberInput?.unit">{{ f.numberInput.unit }}</span>
              </div>
              <el-input-number
                v-else
                v-model="editCardRaw.selected[f.key] as number"
                :min="f.numberInput?.min ?? 0"
                :max="f.numberInput?.max ?? 999999"
                controls-position="right"
              />
            </el-form-item>
          </el-form>
        </template>

        <el-divider content-position="left">产地与合规</el-divider>
        <el-form label-width="112px" label-position="left" size="small">
          <el-form-item label="商品产地">
            <div class="batch__row">
              <el-select v-model="editCardRaw.origin.countryShortName" class="batch__w" filterable placeholder="国家">
                <el-option
                  v-for="c in editRegions?.origin_countries || []"
                  :key="String(c.shortName || c.code)"
                  :label="String(c.name)"
                  :value="String(c.shortName || c.code)"
                />
              </el-select>
              <el-select
                v-if="editCardRaw.origin.countryShortName === 'CN'"
                v-model="editCardRaw.origin.region2Id"
                class="batch__w"
                filterable
                placeholder="省份"
              >
                <el-option
                  v-for="pr in editRegions?.cn_provinces || []"
                  :key="String(pr.code)"
                  :label="String(pr.name)"
                  :value="Number(pr.code)"
                />
              </el-select>
            </div>
          </el-form-item>
          <el-form-item label="制造地点">
            <el-select v-model="editCardRaw.manufacturing" multiple collapse-tags filterable class="batch__w" placeholder="可多选">
              <el-option
                v-for="c in editRegions?.manufacturing_countries || []"
                :key="String(c.shortName || c.code)"
                :label="String(c.name)"
                :value="String(c.shortName || c.code)"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="敏感属性">
            <el-switch v-model="editCardRaw.isSensitive" active-text="是" inactive-text="否" />
          </el-form-item>
          <el-form-item v-if="editCardRaw.isSensitive" label="敏感项">
            <el-checkbox-group v-model="editCardRaw.sensitiveTypes">
              <el-checkbox v-for="s in editSensitiveTypes" :key="s.value" :value="s.value">{{ s.label }}</el-checkbox>
            </el-checkbox-group>
          </el-form-item>
          <el-form-item label="体积（cm）">
            <div class="batch__row">
              <span class="batch__meta">最长边</span>
              <el-input-number v-model="editCardRaw.volume.inputLen" :min="1" :max="999" controls-position="right" />
              <span class="batch__meta">次长边</span>
              <el-input-number v-model="editCardRaw.volume.inputWidth" :min="1" :max="999" controls-position="right" />
              <span class="batch__meta">最短边</span>
              <el-input-number v-model="editCardRaw.volume.inputHeight" :min="1" :max="999" controls-position="right" />
            </div>
          </el-form-item>
          <el-form-item label="重量（g）">
            <el-input-number v-model="editCardRaw.weight.inputValue" :min="1" :max="999999" controls-position="right" />
          </el-form-item>
        </el-form>

        <el-divider content-position="left">尺码</el-divider>
        <el-form label-width="112px" label-position="left" size="small">
          <el-form-item v-if="Object.keys(editSizeTree).length" label="尺码标准">
            <el-select v-model="editSizeStandard" class="batch__w" @change="editSizeType = Object.keys(editSizeTree[editSizeStandard] || {})[0] || ''; editCardRaw.sizeVids = []">
              <el-option v-for="s in Object.keys(editSizeTree)" :key="s" :label="s" :value="s" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="Object.keys(editSizeTree).length" label="尺码类型">
            <el-radio-group v-model="editSizeType" size="small" @change="editCardRaw.sizeVids = []; editCardRaw.sizeTemplateId = undefined">
              <el-radio-button v-for="ty in Object.keys(editSizeTree[editSizeStandard] || {})" :key="ty" :value="ty">
                {{ ty }}（{{ (editSizeTree[editSizeStandard]?.[ty] || []).length }}）
              </el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item v-if="editSizes.length" label="具体尺码">
            <el-checkbox-group v-model="editCardRaw.sizeVids">
              <el-checkbox v-for="s in editSizes" :key="s.vid" :value="s.vid">{{ s.value }}</el-checkbox>
            </el-checkbox-group>
          </el-form-item>
          <el-form-item label="尺码表">
            <el-select v-model="editCardRaw.sizeTemplateId" class="batch__w" clearable filterable placeholder="选择已保存的尺码表">
              <el-option
                v-for="tp in editSizeTplOptions"
                :key="tp.businessId"
                :label="`${tp.name}（${tp.sizeTypeName || '均码'}）`"
                :value="tp.businessId"
              />
            </el-select>
          </el-form-item>
        </el-form>

        <el-divider content-position="left">服务与承诺</el-divider>
        <el-form label-width="112px" label-position="left" size="small">
          <el-form-item label="承诺发货时效">
            <el-select v-model="editCardRaw.shipmentLimit" class="batch__w">
              <el-option v-for="s in SHIPMENT_OPTIONS" :key="s" :label="s" :value="s" />
            </el-select>
          </el-form-item>
          <el-form-item label="运费模板">
            <el-select v-model="editCardRaw.freightTemplateId" class="batch__w" clearable filterable placeholder="选择运费模板">
              <el-option
                v-for="ft in editEnv?.freightTemplates || []"
                :key="String(ft.freightTemplateId)"
                :label="String(ft.templateName || ft.freightTemplateId)"
                :value="String(ft.freightTemplateId)"
              />
            </el-select>
          </el-form-item>
        </el-form>
      </div>

      <template #footer>
        <el-button @click="editCardVisible = false">取消</el-button>
        <el-button type="primary" @click="saveCardEditor">保存</el-button>
      </template>
    </el-dialog>

    <TemuCategoryPicker
      v-model="editCatPickerVisible"
      :selected="editCatPath"
      @confirm="onEditCategoryConfirm"
    />

    <MaterialPicker
      v-model="pickerVisible"
      :carousel="pickerCarousel"
      :kind="pickerKind"
      :selected="pickerSelection"
      @confirm="onPick"
    />
  </div>
</template>

<style scoped>
.batch {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.batch__tools {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.batch__label {
  flex: none;
  font-size: 13px;
  color: var(--muted, #6b7280);
}

.batch__tplselect {
  width: 240px;
}

.batch__meta {
  margin: 6px 0 0;
  color: var(--muted, #9ca3af);
  font-size: 12px;
  line-height: 1.6;
}

/* 圆角卡片 */
.batch :deep(.el-card) {
  border-radius: 12px;
}

.batch__card {
  border-radius: 12px;
  transition: box-shadow .15s, transform .15s;
}

.batch__card:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, .08);
}

.batch__editor {
  max-height: 62vh;
  overflow-y: auto;
  padding-right: 6px;
}

.batch__catrow {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}

.batch__catname {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: var(--muted, #6b7280);
}

.batch__pick {
  margin-right: 2px;
}

.batch__mc {
  margin-left: auto;
}

.batch__list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(420px, 1fr));
  gap: 12px;
}

.batch__cardhead {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.batch__idx {
  flex: none;
  font-weight: 600;
  color: var(--muted, #6b7280);
}

.batch__cardtpl {
  width: 170px;
}

.batch__rm {
  margin-left: auto;
}

.batch__w {
  width: 100%;
  max-width: 200px;
}

.batch__row {
  display: flex;
  gap: 8px;
}

.batch__imgs {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.batch__thumb--dragging {
  opacity: 0.4;
}

.batch__thumb--over {
  outline: 2px dashed var(--primary, #2563eb);
  outline-offset: 1px;
}

.batch__thumb {
  cursor: grab;
}

.batch__thumb :deep(img) {
  -webkit-user-drag: none;
  user-select: none;
}

.batch__thumb {
  position: relative;
  width: 52px;
  height: 69px;
  overflow: hidden;
  border-radius: 4px;
  background: var(--surface-subtle, #f8fafc);
}

.batch__thumb--sq {
  height: 52px;
}

.batch__thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.batch__thumbidx {
  position: absolute;
  left: 2px;
  bottom: 2px;
  padding: 0 3px;
  color: #fff;
  font-size: 10px;
  background: rgba(0, 0, 0, .55);
  border-radius: 3px;
}

.batch__thumbx {
  position: absolute;
  top: 1px;
  right: 1px;
  width: 16px;
  height: 16px;
  padding: 0;
  color: #fff;
  font-size: 12px;
  line-height: 1;
  background: rgba(0, 0, 0, .6);
  border: 0;
  border-radius: 50%;
  cursor: pointer;
}

.batch__imgadd {
  width: 52px;
  height: 69px;
  color: var(--muted, #9ca3af);
  font-size: 12px;
  background: #fff;
  border: 1px dashed var(--border, #dcdfe6);
  border-radius: 4px;
  cursor: pointer;
}

.batch__imgadd--sq {
  height: 52px;
}

.batch__submit {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  padding: 12px 0;
}

.batch__proghead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
</style>
