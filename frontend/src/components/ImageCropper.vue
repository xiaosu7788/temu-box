<script setup lang="ts">
/**
 * 交互式图片裁剪（自研，不依赖第三方库）
 *
 * 为什么不用 Cropper.js：它把「容器尺寸 → imageData → 选框」串成一条链，
 * 任一处拿不到有效尺寸，imageData 就是空的，随后 getData() / getCropBoxData()
 * 全返回 NaN，报错信息还是底层字段名（rotate / canvas），很难定位。
 *
 * 这里只做我们真正需要的三件事：固定比例选框、拖动选区域、导出 canvas，
 * 尺寸全部来自我们自己量的数字，出问题也能给出人能看懂的原因。
 *
 * 实现要点：
 * - 图片按 object-fit: contain 显示，自己算出它在容器里的实际占位（letterbox）
 * - 选框用百分比存位置，容器缩放时自动跟随
 * - 导出时把选框的显示坐标换算回原图像素
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { imageProxyUrl } from '../api'
import { notifyError } from '../feedback'

const props = defineProps<{
  modelValue: boolean
  src: string
  /** 可选比例，如 { '3:4': [3,4], '1:1': [1,1] } */
  proportions: Record<string, number[]>
  initialProportion?: string
  /** 导出目标宽高，不给则按选框覆盖的原图像素输出 */
  targetWidth?: number
  targetHeight?: number
  title?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  cropped: [dataUrl: string, size: { width: number; height: number }]
}>()

const STAGE_H = 420

const stageRef = ref<HTMLElement | null>(null)
const imgRef = ref<HTMLImageElement | null>(null)
const loading = ref(false)
const exporting = ref(false)
const activeRatio = ref('')

/** 图片在容器里的实际显示区域（letterbox 后的矩形），单位 px */
const shown = ref({ left: 0, top: 0, width: 0, height: 0 })
/** 选框相对显示区域的比例位置 0~1 */
const sel = ref({ x: 0.1, y: 0.1, w: 0.8, h: 0.8 })

const ratioKeys = computed(() => Object.keys(props.proportions || {}))
/** 直接用 CDN 地址（官网同款：CDN 已开 CORS 且缓存 1 年），代理只作为兜底 */
const directSrc = computed(() => props.src || '')
const proxySrc = ref('')
let triedProxy = false

function onImgError() {
  if (triedProxy) return
  triedProxy = true
  // 极少数情况 CDN 没给 CORS 头会导致加载失败，这时退回同源代理
  proxySrc.value = imageProxyUrl(props.src)
}
const currentRatio = computed<number[] | null>(() => props.proportions[activeRatio.value] || null)

/** 按当前比例把选框调成最大内接矩形并居中 */
function resetSelection(ratio: number[] | null) {
  const W = shown.value.width || 1
  const H = shown.value.height || 1
  // 选框尺寸按「显示区像素」算，再折回 0~1 的比例，
  // 否则拿容器比例去套会跟图片实际显示区对不上（letterbox 会导致选框变形）
  const r = ratio ? ratio[0] / ratio[1] : W / H
  let pw = W
  let ph = pw / r
  if (ph > H) {
    ph = H
    pw = ph * r
  }
  // 留一点边距，方便拖动
  pw *= 0.9
  ph *= 0.9
  const w = pw / W
  const h = ph / H
  sel.value = { x: (1 - w) / 2, y: (1 - h) / 2, w, h }
}

function measure() {
  const el = imgRef.value
  const stage = stageRef.value
  if (!el || !stage) return
  if (!el.naturalWidth || !el.naturalHeight) return
  // object-fit: contain 的实际显示尺寸
  const sw = stage.clientWidth
  const sh = stage.clientHeight
  const scale = Math.min(sw / el.naturalWidth, sh / el.naturalHeight)
  const w = el.naturalWidth * scale
  const h = el.naturalHeight * scale
  shown.value = { left: (sw - w) / 2, top: (sh - h) / 2, width: w, height: h }
}

async function ready() {
  triedProxy = false
  proxySrc.value = ''
  if (!props.src) return
  loading.value = true
  await nextTick()
  const el = imgRef.value
  if (!el) {
    loading.value = false
    return
  }
  if (!el.complete || !el.naturalWidth) {
    await new Promise<void>((resolve) => {
      const done = () => {
        el.removeEventListener('load', done)
        el.removeEventListener('error', done)
        resolve()
      }
      el.addEventListener('load', done, { once: true })
      el.addEventListener('error', done, { once: true })
    })
  }
  if (!el.naturalWidth) {
    loading.value = false
    notifyError(new Error('图片加载失败，请关闭后重试'))
    return
  }
  measure()
  const label = props.initialProportion && props.proportions[props.initialProportion]
    ? props.initialProportion
    : ratioKeys.value[0] || ''
  activeRatio.value = label
  resetSelection(currentRatio.value)
  loading.value = false
}

function pickRatio(label: string) {
  activeRatio.value = label
  resetSelection(props.proportions[label] || null)
}

function pickFree() {
  activeRatio.value = ''
  resetSelection(null)
}

/* ---------- 拖拽 / 缩放选框 ---------- */
type DragMode = 'move' | 'e' | 'w' | 'n' | 's' | 'nw' | 'ne' | 'sw' | 'se'
let dragging: { mode: DragMode; startX: number; startY: number; origin: typeof sel.value } | null = null

const MIN_SIZE = 0.05

function clamp(v: number, lo: number, hi: number) {
  return Math.min(hi, Math.max(lo, v))
}

function onPointerDown(e: PointerEvent, mode: DragMode) {
  e.preventDefault()
  e.stopPropagation()
  ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
  dragging = { mode, startX: e.clientX, startY: e.clientY, origin: { ...sel.value } }
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', onPointerUp)
}

function onPointerMove(e: PointerEvent) {
  const d = dragging
  const area = shown.value
  if (!d || !area.width || !area.height) return
  const dx = (e.clientX - d.startX) / area.width
  const dy = (e.clientY - d.startY) / area.height
  const o = d.origin
  let { x, y, w, h } = o

  if (d.mode === 'move') {
    x = clamp(o.x + dx, 0, 1 - o.w)
    y = clamp(o.y + dy, 0, 1 - o.h)
    sel.value = { x, y, w: o.w, h: o.h }
    return
  }

  // 边/角缩放：先按拖拽方向改宽高，再按锁定比例修正
  if (d.mode.includes('e')) w = clamp(o.w + dx, MIN_SIZE, 1 - o.x)
  if (d.mode.includes('s')) h = clamp(o.h + dy, MIN_SIZE, 1 - o.y)
  if (d.mode.includes('w')) {
    const nx = clamp(o.x + dx, 0, o.x + o.w - MIN_SIZE)
    w = o.w + (o.x - nx)
    x = nx
  }
  if (d.mode.includes('n')) {
    const ny = clamp(o.y + dy, 0, o.y + o.h - MIN_SIZE)
    h = o.h + (o.y - ny)
    y = ny
  }

  // 锁定比例：以宽为准，或（上下边拖动时）以高为准
  const r = currentRatio.value
  if (r) {
    const target = r[0] / r[1]
    // 像素比例要换算成「相对显示区域」的比例
    const pxRatio = (w * area.width) / Math.max(1e-6, h * area.height)
    if (pxRatio > target) {
      w = (h * area.height * target) / area.width
    } else {
      h = (w * area.width / target) / area.height
    }
    const maxW = 1 - x
    const maxH = 1 - y
    if (w > maxW) {
      w = maxW
      h = (w * area.width / target) / area.height
    }
    if (h > maxH) {
      h = maxH
      w = (h * area.height * target) / area.width
    }
  }

  sel.value = { x: clamp(x, 0, 1 - w), y: clamp(y, 0, 1 - h), w, h }
}

function onPointerUp() {
  dragging = null
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', onPointerUp)
}

/* ---------- 导出 ---------- */
const selStyle = computed(() => ({
  left: `${shown.value.left + sel.value.x * shown.value.width}px`,
  top: `${shown.value.top + sel.value.y * shown.value.height}px`,
  width: `${sel.value.w * shown.value.width}px`,
  height: `${sel.value.h * shown.value.height}px`,
}))

async function confirmCrop() {
  const el = imgRef.value
  const area = shown.value
  if (!el || !el.naturalWidth) {
    notifyError(new Error('图片还没准备好，请稍后重试'))
    return
  }
  if (!area.width || !area.height) {
    notifyError(new Error('裁剪区尺寸异常，请关闭后重试'))
    return
  }
  exporting.value = true
  try {
    // sel 是相对「图片显示区域」的比例，而 object-fit: contain 保持原图比例，
    // 所以这些比例可以直接乘原图尺寸得到像素坐标
    const cropX = Math.round(sel.value.x * el.naturalWidth)
    const cropY = Math.round(sel.value.y * el.naturalHeight)
    const cropW = Math.max(1, Math.round(sel.value.w * el.naturalWidth))
    const cropH = Math.max(1, Math.round(sel.value.h * el.naturalHeight))

    const canvas = document.createElement('canvas')
    canvas.width = props.targetWidth || cropW
    canvas.height = props.targetHeight || cropH
    const ctx = canvas.getContext('2d')
    if (!ctx) {
      notifyError(new Error('无法创建画布'))
      return
    }
    ctx.fillStyle = '#fff'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.imageSmoothingQuality = 'high'
    ctx.drawImage(el, cropX, cropY, cropW, cropH, 0, 0, canvas.width, canvas.height)

    const dataUrl = canvas.toDataURL('image/jpeg', 0.92)
    emit('cropped', dataUrl, { width: canvas.width, height: canvas.height })
    emit('update:modelValue', false)
  } catch (error) {
    notifyError(error)
  } finally {
    exporting.value = false
  }
}

function close() {
  emit('update:modelValue', false)
}

const onResize = () => {
  if (!props.modelValue) return
  measure()
}
watch(
  () => props.modelValue,
  async (open) => {
    if (open) {
      await ready()
      window.addEventListener('resize', onResize)
    } else {
      window.removeEventListener('resize', onResize)
    }
  },
)
watch(() => props.src, async () => {
  if (props.modelValue) await ready()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', onPointerUp)
})
</script>

<template>
  <el-dialog
    align-center
    :model-value="modelValue"
    :title="title || '裁剪图片'"
    width="720px"
    style="max-width: calc(100vw - 32px)"
    :close-on-click-modal="false"
    destroy-on-close
    @opened="ready"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <div v-loading="loading" class="icrop">
      <div ref="stageRef" class="icrop__stage" :style="{ height: STAGE_H + 'px' }">
        <img
          ref="imgRef"
          :src="proxySrc || directSrc"
          crossorigin="anonymous"
          alt=""
          class="icrop__img"
          @load="measure"
          @error="onImgError"
        />
        <!-- 选框 -->
        <div class="icrop__sel" :style="selStyle" @pointerdown="onPointerDown($event, 'move')">
          <div class="icrop__grid" />
          <span class="icrop__handle icrop__handle--nw" @pointerdown.stop="onPointerDown($event, 'nw')" />
          <span class="icrop__handle icrop__handle--ne" @pointerdown.stop="onPointerDown($event, 'ne')" />
          <span class="icrop__handle icrop__handle--sw" @pointerdown.stop="onPointerDown($event, 'sw')" />
          <span class="icrop__handle icrop__handle--se" @pointerdown.stop="onPointerDown($event, 'se')" />
          <span class="icrop__edge icrop__edge--n" @pointerdown.stop="onPointerDown($event, 'n')" />
          <span class="icrop__edge icrop__edge--s" @pointerdown.stop="onPointerDown($event, 's')" />
          <span class="icrop__edge icrop__edge--w" @pointerdown.stop="onPointerDown($event, 'w')" />
          <span class="icrop__edge icrop__edge--e" @pointerdown.stop="onPointerDown($event, 'e')" />
        </div>
      </div>

      <div class="icrop__ratios">
        <button
          v-for="label in ratioKeys"
          :key="label"
          type="button"
          class="icrop__ratio"
          :class="{ 'icrop__ratio--on': activeRatio === label }"
          @click="pickRatio(label)"
        >{{ label }}</button>
        <button
          type="button"
          class="icrop__ratio"
          :class="{ 'icrop__ratio--on': !activeRatio }"
          @click="pickFree"
        >自由</button>
      </div>
      <p class="icrop__hint">拖动选框调整区域，拖四角改大小；确认后会生成一张新素材，原图保留</p>
    </div>

    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="exporting" @click="confirmCrop">裁剪并上传</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.icrop__stage {
  position: relative;
  width: 100%;
  background: #111;
  border-radius: 6px;
  overflow: hidden;
  touch-action: none;
}

.icrop__img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
  user-select: none;
  -webkit-user-drag: none;
  pointer-events: none;
}

.icrop__sel {
  position: absolute;
  border: 2px solid #fff;
  box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.55);
  cursor: move;
  touch-action: none;
}

.icrop__grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(to right, rgba(255, 255, 255, 0.35) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(255, 255, 255, 0.35) 1px, transparent 1px);
  background-size: 33.33% 33.33%;
  pointer-events: none;
}

.icrop__handle {
  position: absolute;
  width: 12px;
  height: 12px;
  background: #fff;
  border-radius: 2px;
}

.icrop__handle--nw { top: -7px; left: -7px; cursor: nwse-resize; }
.icrop__handle--ne { top: -7px; right: -7px; cursor: nesw-resize; }
.icrop__handle--sw { bottom: -7px; left: -7px; cursor: nesw-resize; }
.icrop__handle--se { bottom: -7px; right: -7px; cursor: nwse-resize; }

.icrop__edge { position: absolute; background: transparent; }
.icrop__edge--n { top: -4px; left: 12px; right: 12px; height: 8px; cursor: ns-resize; }
.icrop__edge--s { bottom: -4px; left: 12px; right: 12px; height: 8px; cursor: ns-resize; }
.icrop__edge--w { left: -4px; top: 12px; bottom: 12px; width: 8px; cursor: ew-resize; }
.icrop__edge--e { right: -4px; top: 12px; bottom: 12px; width: 8px; cursor: ew-resize; }

.icrop__ratios {
  display: flex;
  gap: 6px;
  margin-top: 10px;
  flex-wrap: wrap;
}

.icrop__ratio {
  padding: 4px 12px;
  font-size: 13px;
  color: var(--text, #303133);
  background: #fff;
  border: 1px solid var(--border, #dcdfe6);
  border-radius: 4px;
  cursor: pointer;
}

.icrop__ratio:hover,
.icrop__ratio--on {
  color: var(--primary, #2563eb);
  border-color: var(--primary, #2563eb);
  background: #eff6ff;
}

.icrop__hint {
  margin: 8px 0 0;
  color: var(--muted, #9ca3af);
  font-size: 12px;
}
</style>
