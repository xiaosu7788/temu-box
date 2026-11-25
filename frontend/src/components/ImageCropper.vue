<script setup lang="ts">
/**
 * 交互式图片裁剪（对齐官网素材中心的 multi-cropper-galerie-sdk）
 *
 * 官网用 Cropper.js v4，这里用 v1.6 —— API 同源，能力一致：
 * 拖框选区域、切换固定比例、导出指定尺寸。
 *
 * CDN 图带 `access-control-allow-origin: *`，所以 canvas 不会被污染，
 * 可以直接 toBlob 拿到裁剪结果再上传成新素材。
 */
import Cropper from 'cropperjs'
import 'cropperjs/dist/cropper.css'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { imageProxyUrl } from '../api'
import { notifyError } from '../feedback'

const props = defineProps<{
  modelValue: boolean
  /** 原图 URL */
  src: string
  /** 固定比例选项，如 { '3:4': [3,4], '1:1': [1,1] } */
  proportions: Record<string, number[]>
  /** 初始比例标签 */
  initialProportion?: string
  /** 导出目标宽高（固定比例时用），不给则按选框尺寸导出 */
  targetWidth?: number
  targetHeight?: number
  /** 弹窗标题 */
  title?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  /** 裁剪完成，返回 dataURL */
  cropped: [dataUrl: string, size: { width: number; height: number }]
}>()

const imgRef = ref<HTMLImageElement | null>(null)
const loading = ref(false)
const exporting = ref(false)
const activeProportion = ref('')
let cropper: Cropper | null = null

const proportionKeys = computed(() => Object.keys(props.proportions || {}))

/**
 * 用同源代理加载原图。
 * 直接给 CDN 图加 crossOrigin 会在 CDN 缺少 CORS 头时整张图都加载不出来
 * （表现就是弹窗一直转圈、点确认时 canvas 为 null）。
 */
const proxySrc = computed(() => (props.src ? imageProxyUrl(props.src) : ''))

function destroy() {
  if (cropper) {
    cropper.destroy()
    cropper = null
  }
}

async function init() {
  if (!proxySrc.value) return
  loading.value = true
  destroy()
  await nextTick()
  const img = imgRef.value
  if (!img) {
    loading.value = false
    return
  }
  // 等图片真正解码完再交给 Cropper：CDN 图 + crossOrigin 会走二次请求，
  // 不等的话 ready 迟迟不触发，弹窗一直转圈，此时点确认 canvas 还是 null
  if (!img.complete || !img.naturalWidth) {
    await new Promise<void>((resolve) => {
      const done = () => {
        img.removeEventListener('load', done)
        img.removeEventListener('error', done)
        resolve()
      }
      img.addEventListener('load', done, { once: true })
      img.addEventListener('error', done, { once: true })
    })
  }
  try {
    const ratios = props.proportions || {}
    const label = props.initialProportion || proportionKeys.value[0] || ''
    activeProportion.value = label
    const ratio = ratios[label]
    cropper = new Cropper(img, {
      viewMode: 1,
      dragMode: 'move',
      autoCropArea: 1,
      background: false,
      responsive: true,
      checkCrossOrigin: false, // CDN 已开 CORS，避免 cropper 自己再拼 ?crossorigin
      aspectRatio: ratio ? ratio[0] / ratio[1] : NaN,
      ready() {
        loading.value = false
      },
    })
  } catch (error) {
    loading.value = false
    notifyError(error)
  }
}

function pickProportion(label: string) {
  activeProportion.value = label
  const ratio = props.proportions[label]
  if (!cropper || !ratio) return
  cropper.setAspectRatio(ratio[0] / ratio[1])
}

/** 自由比例：允许任意宽高 */
function pickFree() {
  activeProportion.value = ''
  cropper?.setAspectRatio(NaN)
}

function close() {
  emit('update:modelValue', false)
}

async function confirmCrop() {
  if (!cropper) {
    notifyError(new Error('裁剪器还没准备好，请稍等图片加载完成'))
    return
  }
  exporting.value = true
  try {
    const canvas = cropper.getCroppedCanvas({
      width: props.targetWidth,
      height: props.targetHeight,
      imageSmoothingQuality: 'high',
      // 官网裁剪后是严格相等，不给 fillColor 会产生透明边
      fillColor: '#fff',
    })
    if (!canvas) {
      notifyError(new Error('裁剪失败：无法生成画布'))
      return
    }
    const dataUrl = canvas.toDataURL('image/jpeg', 0.92)
    emit('cropped', dataUrl, { width: canvas.width, height: canvas.height })
    emit('update:modelValue', false)
  } catch (error) {
    notifyError(error)
  } finally {
    exporting.value = false
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) destroy()
    // 打开时由 el-dialog 的 @opened 触发 init：那时 DOM 才挂载完
  },
)

watch(
  () => props.src,
  async () => {
    if (props.modelValue) await init()
  },
)

onBeforeUnmount(destroy)
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="title || '裁剪图片'"
    width="min(720px, calc(100vw - 32px))"
    :close-on-click-modal="false"
    destroy-on-close
    @opened="init"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <div v-loading="loading" class="cropper">
      <div class="cropper__stage">
        <img ref="imgRef" :src="proxySrc" alt="" />
      </div>

      <div class="cropper__ratios">
        <button
          v-for="label in proportionKeys"
          :key="label"
          type="button"
          class="cropper__ratio"
          :class="{ 'cropper__ratio--active': activeProportion === label }"
          @click="pickProportion(label)"
        >{{ label }}</button>
        <button
          type="button"
          class="cropper__ratio"
          :class="{ 'cropper__ratio--active': !activeProportion }"
          @click="pickFree"
        >自由</button>
      </div>
      <p class="cropper__hint">
        拖动选框调整区域，滚轮缩放；确认后会生成一张新素材，原图保留
      </p>
    </div>

    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="exporting" @click="confirmCrop">裁剪并上传</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.cropper__stage {
  height: min(52vh, 420px);
  background: #111;
  border-radius: 6px;
  overflow: hidden;
}

.cropper__stage img {
  display: block;
  max-width: 100%;
}

.cropper__ratios {
  display: flex;
  gap: 6px;
  margin-top: 10px;
  flex-wrap: wrap;
}

.cropper__ratio {
  padding: 4px 12px;
  font-size: 13px;
  color: var(--text, #303133);
  background: #fff;
  border: 1px solid var(--border, #dcdfe6);
  border-radius: 4px;
  cursor: pointer;
}

.cropper__ratio:hover,
.cropper__ratio--active {
  color: var(--primary, #2563eb);
  border-color: var(--primary, #2563eb);
  background: #eff6ff;
}

.cropper__hint {
  margin: 8px 0 0;
  color: var(--muted, #9ca3af);
  font-size: 12px;
}
</style>
