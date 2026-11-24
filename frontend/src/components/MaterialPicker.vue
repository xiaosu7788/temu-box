<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { InfoFilled, Search, Upload } from '@element-plus/icons-vue'
import { compressMaterial, cropMaterial, getMaterialFolders, getProductImages, removeMaterial, uploadProductImage, uploadProductVideo, type MaterialFolderNode } from '../api'
import { confirmAction, notifyError, notifySuccess, notifyWarning } from '../feedback'
import type { ProductMaterialImage } from '../types'

/**
 * 素材库选图器。
 * 创建商品只能用素材库里的图：轮播图 3:4（≥1340×1785）、素材图 1:1（≥800×800）。
 */
const props = defineProps<{
  modelValue: boolean
  /** carousel = 轮播图（3:4，可多选）；否则素材图（1:1，单选） */
  carousel?: boolean
  /**
   * 校验口径，优先级高于 carousel：
   *   carousel 轮播 3:4 / 5MB / 短边≥600
   *   material 素材 1:1 / 5MB / 短边≥600
   *   detail   详情图文 480×480 / 3MB / 宽高比 0.5~2（官网 goodsLayerDecorationReqs）
   */
  kind?: 'carousel' | 'material' | 'detail'
  /** 已选中的 URL */
  selected?: string[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  confirm: [urls: string[]]
}>()

const loading = ref(false)
const list = ref<ProductMaterialImage[]>([])
const picked = ref<string[]>([])
const query = reactive({ name: '', page: 1, size: 12 })
/** 仅展示可用（符合当前规格） */
const onlyAvailable = ref(false)
/** 素材类型：'' 全部 / 1 图片 / 2 视频 */
const mediaType = ref<'' | '0' | '1'>('')
/** 视图：grid / list */
const viewMode = ref<'grid' | 'list'>('grid')
/** 服务端返回的总数（用于分页） */
const totalCount = ref(0)
/** 诊断：服务端实际返回的素材类型集合 */
const returnedTypes = ref<number[]>([])

/** 文件夹树 */
const folderTree = ref<MaterialFolderNode[]>([])
const currentFolderId = ref(0)
const removingId = ref<number | null>(null)
const multiple = computed(() => !!props.carousel)

/** 规格要求（官网：轮播 3:4 宽>1340 高>1785；素材 1:1 >800） */
const spec = computed(() =>
  props.carousel
    ? { minW: 1340, minH: 1785, ratio: '3:4', maxCount: 10, label: '轮播图' }
    : { minW: 800, minH: 800, ratio: '1:1', maxCount: 1, label: '素材图' },
)

/** 该图是否满足当前规格（接口没给宽高时视为可用） */
const isVideo = (item: ProductMaterialImage) => Number(item.materialType) === 0

function durationText(item: ProductMaterialImage): string {
  if (!item.duration) return ''
  return `${(item.duration / 1000).toFixed(1)}s`
}

function sizeOk(item: ProductMaterialImage): boolean {
  if (isVideo(item)) return false   // 视频不能当轮播图/素材图
  // 接口没给宽高时无法判定，视为可用（否则整屏不可选）
  if (!item.width || !item.height) return true
  return item.width >= spec.value.minW && item.height >= spec.value.minH
}

/** 该图是否已在选中列表里（按 imgUrl 或 md5，防同图重复选中） */
function isPicked(item: ProductMaterialImage): boolean {
  return picked.value.includes(item.imgUrl)
}

const overLimit = computed(() => multiple.value && picked.value.length > spec.value.maxCount)

/** CDN 实时缩略图（不落盘，只用于列表加速；提交商品必须用原图） */
/** 缩略图加载失败过的原图 URL（自动降级用原图） */
const thumbFailed = ref<Set<string>>(new Set())

/**
 * 列表缩略图：`?imageMogr2/thumbnail/{w}x{h}`（上游 1.3.4 实测 286.6KB → 9.7KB，↓29×）
 *
 * ⚠️ 不要用 `imageView2`：它的 mode 1 语义是「限制**最小**尺寸并裁剪」，
 * 拿来做缩略图会把小图放大（实测 328.9KB，比原图还大），冷缓存首次还慢。
 * `imageView2` 只用于生成「合规大图」（轮播图 ≥1340×1785）。
 *
 * URL 已带 query 时原样返回，避免叠加参数（与官网组件短路规则一致）。
 */
function thumbUrl(url: string): string {
  if (!url || !/^https?:/i.test(url) || url.includes('?')) return url
  if (thumbFailed.value.has(url)) return url
  // 卡片约 150px 见方，只限高度（x150）比限宽度更省：
  // 3:4 的图会被压到 112x150，实测 303KB → 17.6KB
  const dpr = Math.min(Math.round(window.devicePixelRatio || 1), 2)
  return `${url}?imageMogr2/thumbnail/x${Math.round(150 * dpr)}`
}

function onThumbError(item: ProductMaterialImage) {
  if (!item.imgUrl || thumbFailed.value.has(item.imgUrl)) return
  thumbFailed.value = new Set([...thumbFailed.value, item.imgUrl])
}

const compressing = ref<string | null>(null)
const cropping = ref<string | null>(null)

/** 目标规格（当前用途决定的裁切尺寸） */
const targetSpec = computed(() =>
  props.carousel ? { w: 1340, h: 1785, name: '轮播图 3:4' } : { w: 800, h: 800, name: '素材图 1:1' },
)

/** 居中裁切为当前规格，生成一张新素材并选中 */
async function onCrop(item: ProductMaterialImage) {
  const { w, h, name } = targetSpec.value
  if (!item.width || !item.height) {
    notifyError(new Error('这张素材没有尺寸信息，无法裁切'))
    return
  }
  if (item.width < w || item.height < h) {
    notifyError(new Error(`原图 ${item.width}×${item.height} 小于目标 ${w}×${h}，无法裁切`))
    return
  }
  const isSame = item.width === w && item.height === h
  if (isSame) {
    notifyError(new Error('尺寸已符合要求，无需裁切'))
    return
  }
  const dx = Math.max(0, Math.round((item.width - w) / 2))
  const dy = Math.max(0, Math.round((item.height - h) / 2))
  const ok = await confirmAction(
    `将 ${item.width}×${item.height} 居中裁切为 ${w}×${h}（${name}）？\n会生成一张新素材，原图保留。`,
    '裁切素材',
  )
  if (!ok) return
  cropping.value = item.imgUrl
  try {
    const r = await cropMaterial(item.imgUrl, { width: w, height: h, dx, dy })
    if (!r.url) {
      notifyError(new Error('裁切失败：未返回新地址'))
      return
    }
    list.value = [
      {
        id: null,
        imgUrl: r.url,
        name: `${item.name || '素材'}-裁切${w}x${h}`,
        width: r.width || w,
        height: r.height || h,
        size: r.size || 0,
        materialType: 1,
      } as ProductMaterialImage,
      ...list.value,
    ]
    notifySuccess(`已裁切为 ${w}×${h}`)
  } catch (error) {
    notifyError(error)
  } finally {
    cropping.value = null
  }
}

const MB = 1024 * 1024
/** 上游 1.3.10：官网前端硬拦是 5MB（5242880），旧的 2MB 是上游写错的 */
const MAX_IMAGE_SIZE = 5 * MB

/**
 * 与上游 temu-ops 1.3.10 `lib/material.js` 的 UPLOAD_LIMITS 保持一致：
 * 官网前端 beforeUpload 的三道校验（体积 / 短边 / 比例）。
 */
/**
 * 与上游 temu-ops lib/material.js 的 UPLOAD_LIMITS 保持一致。
 *
 * ⚠️ 比例容差是「像素」不是百分比（官网 _cdn_9255 的 tn()）：
 *      Math.abs(height - width / ratio) <= (ratio === 3/4 ? 10 : 0)
 *    即 3:4 允许 ±10 像素，1:1 必须严格相等。
 *    之前写成 ±10%（ratioTolerance: 0.1）会把 800×801 判成合格，实际提交会被平台拒绝。
 */
const UPLOAD_LIMITS = {
  maxImageSize: MAX_IMAGE_SIZE,
  minImageSide: 600,
  ratioFloat: 10, // 仅 3:4 使用
  ratioFloatOther: 0,
  carousel: { ratio: [3, 4], label: '3:4' },
  material: { ratio: [1, 1], label: '1:1' },
  // 详情图文（goodsLayerDecorationReqs）的固定规格：480×480、3MB、宽高比 0.5~2
  detail: { minWidth: 480, minHeight: 480, maxSize: 3 * 1024 * 1024, ratioRange: [0.5, 2], label: '1:2~2:1' },
}

interface ImageCheck {
  ok: boolean
  reasons: string[]
}

/** 当前弹窗按哪种规格校验（kind 优先，回退到 carousel 布尔） */
const pickKind = computed<'carousel' | 'material' | 'detail'>(() => {
  if (props.kind) return props.kind
  return props.carousel ? 'carousel' : 'material'
})

/** 提交前自检：尺寸/比例/体积，返回不通过的原因 */
function validateImage(
  img: { width?: number; height?: number; size?: number; name?: string },
  kind: 'carousel' | 'material' | 'detail',
): ImageCheck {
  const reasons: string[] = []
  const w = Number(img.width) || 0
  const h = Number(img.height) || 0
  const size = Number(img.size) || 0

  if (kind === 'detail') {
    const spec = UPLOAD_LIMITS.detail
    if (size > spec.maxSize) reasons.push(`${(size / MB).toFixed(2)}MB 超过 3MB`)
    if (w && h) {
      if (w < spec.minWidth || h < spec.minHeight) {
        reasons.push(`尺寸 ${w}×${h} 小于 ${spec.minWidth}×${spec.minHeight}`)
      }
      const ratio = w / h
      if (ratio < spec.ratioRange[0] || ratio > spec.ratioRange[1]) {
        reasons.push(`宽高比 ${ratio.toFixed(2)} 超出 ${spec.label}`)
      }
    } else {
      reasons.push('读不到图片尺寸')
    }
    return { ok: reasons.length === 0, reasons }
  }

  const spec = kind === 'carousel' ? UPLOAD_LIMITS.carousel : UPLOAD_LIMITS.material
  const tol = kind === 'carousel' ? UPLOAD_LIMITS.ratioFloat : UPLOAD_LIMITS.ratioFloatOther

  if (size > UPLOAD_LIMITS.maxImageSize) {
    reasons.push(`${(size / MB).toFixed(2)}MB 超过 5MB`)
  }
  if (w && h) {
    const short = Math.min(w, h)
    if (short < UPLOAD_LIMITS.minImageSide) {
      reasons.push(`短边 ${short}px 小于 ${UPLOAD_LIMITS.minImageSide}px`)
    }
    // 官网判据：|height - width / ratio| <= 容差（像素）
    const [rw, rh] = spec.ratio
    const expectedH = (w / rw) * rh
    if (Math.abs(h - expectedH) > tol) {
      reasons.push(`比例 ${w}×${h} 不是 ${spec.label}（像素容差 ±${tol}）`)
    }
  } else {
    reasons.push('读不到图片尺寸')
  }
  return { ok: reasons.length === 0, reasons }
}

/** 超 5MB 的素材：确认后压缩，用新 URL 参与提交 */
async function maybeCompress(item: ProductMaterialImage): Promise<string | null> {
  if (!item.size || item.size <= MAX_IMAGE_SIZE) return item.imgUrl
  const ok = await confirmAction(
    `该图 ${sizeText(item)} 超过 5MB 上限，提交商品会失败。\n是否压缩后使用？压缩会生成一张新素材。`,
    '压缩图片',
  )
  if (!ok) return null
  compressing.value = item.imgUrl
  try {
    const r = await compressMaterial([item.imgUrl])
    const next = r.compressMap[item.imgUrl]
    if (!next) {
      notifyError(new Error('压缩失败：未返回新地址'))
      return null
    }
    item.imgUrl = next
    notifySuccess('已压缩')
    return next
  } catch (error) {
    notifyError(error)
    return null
  } finally {
    compressing.value = null
  }
}

/** 实际渲染的列表：可选「仅展示可用」 */
const displayList = computed(() =>
  onlyAvailable.value ? list.value.filter((x) => sizeOk(x)) : list.value,
)

/** 比例：官网直接显示 1350:1800 这种原始比 */
function ratioOf(item: ProductMaterialImage): string {
  if (item.proportion) return item.proportion
  if (!item.width || !item.height) return '—'
  return `${item.width}:${item.height}`
}

/** 大小：KB */
function sizeText(item: ProductMaterialImage): string {
  if (!item.size) return '—'
  return item.size >= 1024 * 1024
    ? `${(item.size / 1024 / 1024).toFixed(1)}MB`
    : `${Math.round(item.size / 1024)}KB`
}

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

async function loadFolders() {
  try {
    const r = await getMaterialFolders()
    folderTree.value = r.root ? [r.root] : []
  } catch {
    folderTree.value = []
  }
}

function onFolderClick(node: MaterialFolderNode) {
  currentFolderId.value = node.id
  query.page = 1
  void load()
}

/** 删除素材（软删除，需二次确认） */
async function onRemove(item: ProductMaterialImage) {
  if (!item.id) return
  const isVid = isVideo(item)
  const ok = await confirmAction(
    `确定删除「${item.name || '未命名'}」？素材会从素材库下架且无法恢复；已经引用它的商品图片不受影响。`,
    '删除素材',
  )
  if (!ok) return
  removingId.value = item.id
  try {
    await removeMaterial(isVid ? [] : [item.id], isVid ? [item.id] : [])
    list.value = list.value.filter((x) => x.id !== item.id)
    picked.value = picked.value.filter((u) => u !== item.imgUrl)
    notifySuccess('已删除')
  } catch (error) {
    notifyError(error)
  } finally {
    removingId.value = null
  }
}

async function load() {
  loading.value = true
  try {
    const data = await getProductImages({
      page_no: query.page,
      page_size: query.size,
      name: query.name.trim() || undefined,
      type: mediaType.value === '' ? undefined : Number(mediaType.value),
      folder_id: currentFolderId.value || undefined,
    })
    totalCount.value = data.total || 0
    returnedTypes.value = (data as { returnedTypes?: number[] }).returnedTypes || []
    // 素材库常有重复文件：按 imgUrl / md5 去重，避免同一张图能选两次
    const seen = new Set<string>()
    list.value = (data.list || []).filter((x) => {
      const key = x.md5 || x.imgUrl
      if (!key || seen.has(key)) return false
      seen.add(key)
      return true
    })
  } catch (error) {
    notifyError(error)
    list.value = []
  } finally {
    loading.value = false
  }
}

const uploading = ref(false)

/** 读图片实际尺寸（上传前按用途校验比例） */
function readImageSize(file: File): Promise<{ width: number; height: number }> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      resolve({ width: img.naturalWidth, height: img.naturalHeight })
      URL.revokeObjectURL(url)
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('图片读取失败'))
    }
    img.src = url
  })
}

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const s = String(reader.result || '')
      resolve(s.includes(',') ? s.split(',')[1] : s)
    }
    reader.onerror = () => reject(new Error('文件读取失败'))
    reader.readAsDataURL(file)
  })
}

/** 上传到素材中心：校验比例 → 传素材库 → 自动选中 */
async function onUpload(file: { raw?: File }) {
  const raw = file.raw
  if (!raw) return
  if (!/^image\/(jpeg|png|webp)$/i.test(raw.type)) {
    notifyError(new Error('只支持 JPG / PNG / WebP'))
    return
  }
  // 上传接口本身不限体积（5MB 是「用作商品图」的硬性规格）；
  // 这里只拦过大文件，避免 base64 传输把内存打爆。
  if (raw.size > 20 * MB) {
    notifyError(new Error('图片超过 20MB，请先压缩'))
    return
  }
  uploading.value = true
  try {
    const { width, height } = await readImageSize(raw)
    // 轮播图 3:4 ≥1340×1785；素材图 1:1 ≥800×800
    const need = props.carousel
      ? { minW: 1340, minH: 1785, label: '轮播图' }
      : { minW: 800, minH: 800, label: '素材图' }
    // 尺寸不足：裁切只能截取、不能放大，直接拒绝
    if (width < need.minW || height < need.minH) {
      notifyError(
        new Error(`${need.label}尺寸不足：当前 ${width}×${height}，要求 ≥${need.minW}×${need.minH}`),
      )
      return
    }
    // 尺寸够但比例不对：确认后在上传时按重心裁切（会截掉边缘）
    const targetW = props.carousel ? 1340 : 800
    const targetH = props.carousel ? 1785 : 800
    const offRatio = Math.abs(width / height - targetW / targetH) / (targetW / targetH) > 0.02
    let crop: { width: number; height: number } | undefined
    if (offRatio) {
      const ok = await confirmAction(
        `当前 ${width}×${height}，比例与${need.label}要求（${props.carousel ? '3:4' : '1:1'}）不符。\n上传时将自动裁切为 ${targetW}×${targetH}（可能截掉边缘），是否继续？`,
        '裁切上传',
      )
      if (!ok) return
      crop = { width: targetW, height: targetH }
    }
    const base64 = await readBase64(raw)
    const result = await uploadProductImage(base64, raw.name, crop)
    // 上传成功：插到列表最前并选中
    list.value = [{ imgUrl: result.imgUrl, id: result.materialId } as ProductMaterialImage, ...list.value]
    if (multiple.value) {
      picked.value = Array.from(new Set([...picked.value, result.imgUrl]))
    } else {
      picked.value = [result.imgUrl]
    }
    if (raw.size > MAX_IMAGE_SIZE) {
      notifyWarning('已上传，但这张图超过 5MB，用作商品图时会被要求压缩或裁切')
    } else {
      notifySuccess('已上传到素材中心')
    }
  } catch (error) {
    notifyError(error)
  } finally {
    uploading.value = false
  }
}

const uploadingVideo = ref(false)

/** 上传视频：≤30MB MP4，成功后直接插入列表（封面取首帧） */
async function onUploadVideo(file: { raw?: File }) {
  const raw = file.raw
  if (!raw) return
  if (!/^video\//i.test(raw.type) && !/\.mp4$/i.test(raw.name)) {
    notifyError(new Error('只支持 MP4 视频'))
    return
  }
  if (raw.size > 30 * 1024 * 1024) {
    notifyError(new Error('视频超过 30MB 限制'))
    return
  }
  uploadingVideo.value = true
  try {
    const base64 = await readBase64(raw)
    notifySuccess('视频上传中，转码可能需要几十秒…')
    const r = await uploadProductVideo(base64, raw.name)
    list.value = [
      {
        id: r.materialId,
        imgUrl: r.coverUrl,
        videoUrl: r.videoUrl,
        name: raw.name,
        width: r.width,
        height: r.height,
        materialType: 0,
        vid: r.vid,
      } as ProductMaterialImage,
      ...list.value,
    ]
    notifySuccess('视频已上传并转码完成')
  } catch (error) {
    notifyError(error)
  } finally {
    uploadingVideo.value = false
  }
}

/** 某张图被选中的次数（官网允许同一张图占多个轮播位） */
function countOf(url: string): number {
  return picked.value.filter((x) => x === url).length
}

/** 移除该图的一张选中（角标可点） */
function removeOne(url: string) {
  const i = picked.value.lastIndexOf(url)
  if (i < 0) return
  const next = [...picked.value]
  next.splice(i, 1)
  picked.value = next
}

async function toggle(item: ProductMaterialImage) {
  const url = item.imgUrl
  if (!url) return
  if (!sizeOk(item)) {
    notifyError(new Error(`尺寸不符合要求：需 ≥${spec.value.minW}×${spec.value.minH}`))
    return
  }

  // 单选模式（素材图）：只保留一张
  if (!multiple.value) {
    picked.value = [url]
    return
  }

  // 多选模式：每点一次追加一张，同一张可重复（对齐官网）
  if (picked.value.length >= spec.value.maxCount) {
    notifyError(new Error(`最多可选 ${spec.value.maxCount} 张`))
    return
  }

  // 超 5MB 先压缩，用返回的新地址参与提交
  const finalUrl = await maybeCompress(item)
  if (finalUrl === null) return
  picked.value = [...picked.value, finalUrl]
}
/** 卡片上的「压缩」按钮：压完直接选中新图，不用再点一次卡片 */
async function compressAndPick(item: ProductMaterialImage) {
  if (picked.value.length >= spec.value.maxCount) {
    notifyError(new Error(`最多可选 ${spec.value.maxCount} 张`))
    return
  }
  compressing.value = item.imgUrl
  try {
    const finalUrl = await maybeCompress(item)
    if (!finalUrl) return
    picked.value = [...picked.value, finalUrl]
  } finally {
    compressing.value = ''
  }
}

/** 已选图里按规格不合格的（用 uploads 缓存里的宽高信息） */
function badPicked(): Array<{ url: string; reasons: string[] }> {
  const kind = pickKind.value
  const out: Array<{ url: string; reasons: string[] }> = []
  for (const url of picked.value) {
    const meta = list.value.find((x: ProductMaterialImage) => x.imgUrl === url)
    const r = validateImage(
      { width: meta?.width, height: meta?.height, size: meta?.size },
      kind,
    )
    if (!r.ok) out.push({ url, reasons: r.reasons })
  }
  return out
}

async function applySelection() {
  if (!picked.value.length) return
  const bad = badPicked()
  if (bad.length) {
    const detail = bad
      .slice(0, 3)
      .map((b, i) => `${i + 1}. ${b.reasons.join('；')}`)
      .join('\n')
    const more = bad.length > 3 ? `\n… 另有 ${bad.length - 3} 张` : ''
    const ok = await confirmAction(
      `${bad.length} 张图不满足${
        pickKind.value === 'detail' ? '详情图文' : pickKind.value === 'carousel' ? '轮播图 3:4' : '素材图 1:1'
      }规格：\n${detail}${more}\n\n仍然使用吗？（提交商品时可能被平台拒绝）`,
      '图片规格不通过',
    )
    if (!ok) return
  }
  emit('confirm', picked.value)
  visible.value = false
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    picked.value = [...(props.selected || [])]
    query.page = 1
    void loadFolders()
    void load()
  },
)
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="carousel ? '选择轮播图（3:4）' : '选择素材图（1:1）'"
    width="min(980px, calc(100vw - 32px))"
  >
    <div class="picker-main">
      <aside class="picker-side">
        <div
          class="picker-side__all"
          :class="{ 'picker-side__all--active': currentFolderId === 0 }"
          @click="currentFolderId = 0; query.page = 1; load()"
        >全部素材</div>
        <el-tree
          v-if="folderTree.length"
          :data="folderTree"
          node-key="id"
          :props="{ label: 'name', children: 'children' }"
          default-expand-all
          highlight-current
          :expand-on-click-node="false"
          @node-click="onFolderClick"
        />
        <p v-else class="picker-side__empty">无文件夹</p>
      </aside>

      <div class="picker-content">
    <div class="picker-bar">
      <el-input
        v-model="query.name"
        placeholder="按名称搜索素材"
        clearable
        class="picker-search"
        :prefix-icon="Search"
        @keyup.enter="query.page = 1; load()"
        @clear="query.page = 1; load()"
      />
      <el-button :loading="loading" @click="query.page = 1; load()">搜索</el-button>
      <el-upload
        v-if="mediaType !== '0'"
        :auto-upload="false"
        :show-file-list="false"
        accept="image/jpeg,image/png,image/webp"
        :on-change="onUpload"
      >
        <el-button type="primary" :loading="uploading" :icon="Upload">上传图片</el-button>
      </el-upload>
      <el-upload
        v-else
        :auto-upload="false"
        :show-file-list="false"
        accept="video/mp4"
        :on-change="onUploadVideo"
      >
        <el-button type="primary" :loading="uploadingVideo" :icon="Upload">上传视频</el-button>
      </el-upload>
      <el-checkbox v-model="onlyAvailable">仅展示可用</el-checkbox>
      <el-radio-group v-model="mediaType" size="small" @change="query.page = 1; load()">
        <el-radio-button value="">全部</el-radio-button>
        <el-radio-button value="1">图片</el-radio-button>
        <el-radio-button value="0">视频</el-radio-button>
      </el-radio-group>
      <el-radio-group v-model="viewMode" size="small">
        <el-radio-button value="grid">网格</el-radio-button>
        <el-radio-button value="list">列表</el-radio-button>
      </el-radio-group>
      <span class="picker-hint">
        {{ carousel ? '轮播图需 3:4、≥1340×1785、≤5MB' : '素材图需 1:1、≥800×800、≤5MB' }}
      </span>
    </div>

    <el-table
      v-if="viewMode === 'list'"
      v-loading="loading"
      :data="displayList"
      size="small"
      max-height="52vh"
      @selection-change="() => {}"
    >
      <el-table-column width="46">
        <template #default="scope">
          <el-checkbox
            :model-value="countOf(scope.row.imgUrl) > 0"
            :disabled="!sizeOk(scope.row)"
            @change="toggle(scope.row)"
          />
        </template>
      </el-table-column>
      <el-table-column label="素材信息" min-width="260">
        <template #default="scope">
          <div class="picker-row">
            <img :src="thumbUrl(scope.row.imgUrl)" class="picker-row__thumb" />
            <div class="picker-row__info">
              <span class="picker-card__name" :title="scope.row.name">{{ scope.row.name || '未命名' }}</span>
              <span class="picker-card__id">ID: {{ scope.row.id || '—' }}</span>
            </div>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="类型" width="70">
        <template #default="scope">{{ isVideo(scope.row) ? '视频' : '图片' }}</template>
      </el-table-column>
      <el-table-column label="尺寸" width="110">
        <template #default="scope">{{ scope.row.width ? `${scope.row.width}×${scope.row.height}` : '—' }}</template>
      </el-table-column>
      <el-table-column label="比例" width="90">
        <template #default="scope">{{ ratioOf(scope.row) }}</template>
      </el-table-column>
      <el-table-column label="大小" width="80">
        <template #default="scope">{{ sizeText(scope.row) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="110">
        <template #default="scope">
          <el-button
            link
            type="primary"
            size="small"
            :loading="cropping === scope.row.imgUrl"
            @click.stop="onCrop(scope.row)"
          >裁切</el-button>
          <el-button
            link
            type="danger"
            size="small"
            :loading="removingId === scope.row.id"
            @click.stop="onRemove(scope.row)"
          >删除</el-button>
        </template>
      </el-table-column>
      <el-table-column label="可用" width="70">
        <template #default="scope">
          <el-tag :type="sizeOk(scope.row) ? 'success' : 'danger'" size="small" effect="plain">
            {{ sizeOk(scope.row) ? '是' : '否' }}
          </el-tag>
        </template>
      </el-table-column>
    </el-table>

    <div v-else v-loading="loading" class="picker-grid">
      <div
        v-for="item in displayList"
        :key="item.imgUrl"
        class="picker-card"
        :class="{ 'picker-card--active': isPicked(item), 'picker-card--bad': !sizeOk(item) }"
        role="button"
        tabindex="0"
        @click="toggle(item)"
        @keyup.enter="toggle(item)"
      >
        <div class="picker-card__img">
          <el-image
            :src="thumbUrl(item.imgUrl)"
            :preview-src-list="[item.imgUrl]"
            :alt="item.name || ''"
            fit="cover"
            preview-teleported
            hide-on-click-modal
            class="picker-card__pic"
            @click.stop
            @error="onThumbError(item)"
          />
          <span v-if="isVideo(item)" class="picker-card__badge">
            <strong>视频素材</strong>
            <span>不能用作轮播图</span>
          </span>
          <span v-else-if="!sizeOk(item)" class="picker-card__badge">
            <strong>尺寸不符合要求</strong>
            <span>需 ≥{{ spec.minW }}×{{ spec.minH }}</span>
          </span>
          <span v-else-if="isVideo(item)" class="picker-card__vid">▶ {{ durationText(item) }}</span>
          <span
            v-else-if="countOf(item.imgUrl) > 0"
            class="picker-item__mark picker-item__mark--count"
            title="再次点击卡片可继续添加；点这里移除一张"
            @click.stop="removeOne(item.imgUrl)"
          >✓ {{ countOf(item.imgUrl) }}</span>
          <button
            v-if="item.id"
            type="button"
            class="picker-card__del"
            :disabled="removingId === item.id"
            @click.stop="onRemove(item)"
          >×</button>
        </div>
        <div class="picker-card__meta">
          <span class="picker-card__name" :title="item.name">{{ item.name || '未命名' }}</span>
          <span class="picker-card__size" :title="`${item.width || '?'}×${item.height || '?'} · ${sizeText(item)}`">
            {{ item.width ? `${item.width}×${item.height}` : '尺寸未知' }}
            <template v-if="item.width"> · {{ ratioOf(item) }}</template>
            <template v-if="item.size"> · {{ sizeText(item) }}</template>
          </span>
        </div>
        <div class="picker-card__ops">
          <button
            type="button"
            class="picker-card__op"
            :disabled="cropping === item.imgUrl"
            @click.stop="onCrop(item)"
          >{{ cropping === item.imgUrl ? '裁剪中' : '裁剪' }}</button>
          <button
            type="button"
            class="picker-card__op"
            :disabled="compressing === item.imgUrl"
            @click.stop="compressAndPick(item)"
          >{{ compressing === item.imgUrl ? '压缩中' : '压缩' }}</button>
          <button
            v-if="item.id"
            type="button"
            class="picker-card__op picker-card__op--danger"
            :disabled="removingId === item.id"
            @click.stop="onRemove(item)"
          >删除</button>
        </div>
      </div>
      <el-empty
        v-if="!loading && !list.length"
        :image-size="60"
        :description="
          mediaType === '0'
            ? '该店铺没有视频素材'
            : mediaType === '1'
              ? '没有图片素材'
              : '素材库里没有素材'
        "
      />
    </div>

    <div class="picker-foot">
      <div class="picker-foot__left">
        <el-pagination
          v-model:current-page="query.page"
          :page-size="query.size"
          :hide-on-single-page="false"
          layout="prev, pager, next"
          :total="totalCount"
          @current-change="load"
        />
        <el-select v-model="query.size" size="small" class="picker-size" @change="query.page = 1; load()">
          <el-option :value="12" label="12 条/页" />
          <el-option :value="24" label="24 条/页" />
          <el-option :value="50" label="50 条/页" />
        </el-select>
      </div>
      <div class="picker-foot__right">
        <span class="picker-picked" :class="{ 'picker-picked--warn': overLimit }">
          已选 <b>{{ picked.length }}</b> / {{ spec.maxCount }}
        </span>
        <el-tooltip
          :content="`规格：宽高比例 ${spec.ratio}，宽 ≥${spec.minW}px，高 ≥${spec.minH}px，≤5MB`"
          placement="top"
        >
          <el-icon class="picker-info"><InfoFilled /></el-icon>
        </el-tooltip>
        <span v-if="returnedTypes.length" class="picker-dbg">
          类型 {{ returnedTypes.join('/') }}
        </span>
      </div>
    </div>
    </div>
    </div>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :disabled="!picked.length || overLimit" @click="applySelection">
        确定{{ overLimit ? '（超出可选数量）' : '' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.picker-main {
  display: flex;
  gap: 12px;
  align-items: stretch;
}

.picker-side {
  flex: 0 0 150px;
  padding: 6px;
  overflow: auto;
  background: var(--surface-subtle, #f8fafc);
  border-radius: 6px;
  max-height: 62vh;
}

.picker-side__all {
  padding: 5px 8px;
  margin-bottom: 4px;
  font-size: 13px;
  border-radius: 4px;
  cursor: pointer;
}

.picker-side__all--active,
.picker-side__all:hover {
  color: var(--primary, #2563eb);
  background: #eff6ff;
}

.picker-side :deep(.el-tree) {
  background: transparent;
  font-size: 13px;
}

.picker-side__empty {
  margin: 8px;
  color: var(--muted, #9ca3af);
  font-size: 12px;
}

.picker-content {
  flex: 1 1 auto;
  min-width: 0;
}

.picker-card__crop {
  position: absolute;
  top: 4px;
  right: 26px;
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  color: #fff;
  font-size: 12px;
  line-height: 1;
  background: rgba(0, 0, 0, .55);
  border: 0;
  border-radius: 50%;
  cursor: pointer;
  opacity: 0;
  transition: opacity .15s;
}

.picker-card:hover .picker-card__crop {
  opacity: 1;
}

.picker-card__crop:hover {
  background: var(--primary, #2563eb);
}

.picker-card__del {
  position: absolute;
  top: 4px;
  left: 4px;
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  color: #fff;
  font-size: 14px;
  line-height: 1;
  background: rgba(0, 0, 0, .55);
  border: 0;
  border-radius: 50%;
  cursor: pointer;
  opacity: 0;
  transition: opacity .15s;
}

.picker-card:hover .picker-card__del {
  opacity: 1;
}

.picker-card__del:hover {
  background: #dc2626;
}

.picker-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 10px;
  margin-bottom: 12px;
}

.picker-search {
  width: 240px;
  flex: none;
}

.picker-size {
  width: 120px;
  flex: none;
}

.picker-card__pic {
  width: 100%;
  height: 100%;
  display: block;
}

.picker-hint {
  color: var(--muted, #6b7280);
  font-size: 12px;
}

.picker-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(148px, 1fr));
  align-items: start;
  gap: 10px;
  min-height: 200px;
  max-height: 60vh;
  overflow-y: auto;
}


.picker-card__ops {
  display: flex;
  flex: none;
  align-items: center;
  gap: 2px;
  padding: 0 4px 5px;
}

.picker-card__op {
  flex: 1 1 0;
  padding: 3px 0;
  color: var(--primary, #2563eb);
  font-size: 12px;
  background: transparent;
  border: 0;
  border-radius: 4px;
  cursor: pointer;
}

.picker-card__op:hover:not(:disabled) {
  background: #eff6ff;
}

.picker-card__op:disabled {
  color: var(--muted, #c0c4cc);
  cursor: not-allowed;
}

.picker-card__op--danger {
  color: var(--el-color-danger, #f56c6c);
}

.picker-card__op--danger:hover:not(:disabled) {
  background: #fef2f2;
}

.picker-card {
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
  background: #fff;
  border: 2px solid var(--border, #e5e7eb);
  border-radius: 8px;
  cursor: pointer;
  /* 固定高度：图片 1:1 + 两行文字 + 操作条，避免名称换行把卡片撑成长条 */
  height: 100%;
}

.picker-card__meta {
  flex: 1 1 auto;
  min-height: 0;
}

.picker-card--active {
  border-color: var(--primary, #2563eb);
}

/* 卡片整体浮起，和官网素材中心的观感靠近 */
.picker-card:hover {
  border-color: var(--primary, #93c5fd);
  box-shadow: 0 4px 16px rgba(0, 0, 0, .1);
}

.picker-card--bad {
  cursor: not-allowed;
}

.picker-card__img {
  position: relative;
  flex: none;
  /* 素材多为 3:4，用同比例容器：既贴合内容，又比 1:1 矮 1/4 */
  aspect-ratio: 3 / 4;
  background: var(--surface-subtle, #f8fafc);
}

.picker-card__img img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* 不合规的图仍需可见（可预览），只盖一层半透明遮罩，不要再压暗 */
.picker-card--bad .picker-card__img img {
  filter: none;
}

.picker-card__vid {
  position: absolute;
  left: 4px;
  bottom: 4px;
  padding: 1px 6px;
  color: #fff;
  font-size: 11px;
  background: rgba(0, 0, 0, .6);
  border-radius: 4px;
}

.picker-card__badge {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  pointer-events: none;
  align-items: center;
  justify-content: center;
  padding: 8px;
  color: #fff;
  font-size: 11px;
  line-height: 1.5;
  text-align: center;
  background: rgba(0, 0, 0, .42);
  text-shadow: 0 1px 3px rgba(0, 0, 0, .8);
}

.picker-card__badge strong {
  font-size: 12px;
  font-weight: 600;
}

.picker-card__meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 4px 6px;
  overflow: hidden;
}

.picker-card__name {
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.picker-card__size {
  overflow: hidden;
  color: var(--muted, #9ca3af);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.picker-card__id {
  color: var(--muted, #c0c4cc);
  font-size: 11px;
}

.picker-item__mark--count {
  width: auto;
  min-width: 22px;
  padding: 0 5px;
  font-size: 11px;
  border-radius: 11px;
  cursor: pointer;
}

.picker-item__mark--count:hover {
  background: #dc2626;
}

.picker-item__mark {
  position: absolute;
  top: 4px;
  right: 4px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 13px;
  background: var(--primary, #2563eb);
  border-radius: 50%;
}

.picker-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
}

.picker-foot__left,
.picker-foot__right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.picker-picked {
  padding: 3px 10px;
  color: var(--primary, #2563eb);
  font-size: 12px;
  background: #eff6ff;
  border-radius: 999px;
}

.picker-picked b {
  font-size: 14px;
}

.picker-picked--warn {
  color: #dc2626;
  background: #fef2f2;
}

.picker-dbg {
  color: var(--muted, #c0c4cc);
  font-size: 11px;
}

.picker-info {
  color: var(--muted, #9ca3af);
  font-size: 15px;
  cursor: help;
}

.picker-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.picker-row__thumb {
  width: 44px;
  height: 44px;
  object-fit: cover;
  border-radius: 4px;
  flex: none;
}

.picker-row__thumb--bad {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #dc2626;
  font-size: 11px;
  background: #fef2f2;
  border: 1px solid #fecaca;
}

.picker-row__info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.picker-count--warn {
  color: #dc2626;
}
</style>