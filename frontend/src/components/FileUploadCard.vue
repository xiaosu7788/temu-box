<script setup lang="ts">
import { computed, ref } from 'vue'
import { Check, Close, Document, RefreshRight, UploadFilled } from '@element-plus/icons-vue'
import type { UploadFile, UploadFiles, UploadUserFile } from 'element-plus'

const props = withDefaults(
  defineProps<{
    modelValue: UploadUserFile[]
    title: string
    step?: string
    subtitle?: string
    accept?: string
    disabled?: boolean
  }>(),
  {
    step: '',
    subtitle: '',
    accept: '.xlsx,.xlsm',
    disabled: false,
  },
)

const emit = defineEmits<{
  'update:modelValue': [files: UploadUserFile[]]
  change: [file: UploadFile, files: UploadFiles]
}>()

const uploadRef = ref()
const isDragging = ref(false)

const currentFile = computed(() => props.modelValue[0] || null)
const hasFile = computed(() => Boolean(currentFile.value))

function formatSize(bytes?: number): string {
  if (!bytes) return ''
  if (bytes > 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(2)} MB`
  return `${(bytes / 1024).toFixed(0)} KB`
}

function handleFileChange(file: UploadFile, files: UploadFiles) {
  const last = files.slice(-1)
  emit('update:modelValue', last)
  emit('change', file, files)
}

function removeFile(event: Event) {
  event.stopPropagation()
  emit('update:modelValue', [])
}

function triggerReselect(event: Event) {
  event.stopPropagation()
  // 触发原生 input 点击
  const inputEl = (uploadRef.value?.$el as HTMLElement)?.querySelector('input[type="file"]') as HTMLInputElement | null
  inputEl?.click()
}
</script>

<template>
  <div class="file-upload-card-wrapper" :class="{ 'has-file': hasFile, 'is-disabled': disabled }">
    <div class="card-header-bar">
      <div class="upload-title">
        <span v-if="step" class="step-badge">{{ step }}</span>
        <strong>{{ title }}</strong>
        <small v-if="subtitle">{{ subtitle }}</small>
      </div>
      <el-tag v-if="hasFile" size="small" type="success" effect="plain" class="status-tag">
        <el-icon><Check /></el-icon>已就绪
      </el-tag>
    </div>

    <!-- 已经选了文件后的就绪卡片 -->
    <div v-if="hasFile && currentFile" class="ready-file-card">
      <div class="excel-icon-box">
        <el-icon><Document /></el-icon>
        <span class="file-ext">XLS</span>
      </div>
      <div class="file-info-col">
        <div class="file-name" :title="currentFile.name">{{ currentFile.name }}</div>
        <div class="file-meta">
          <span v-if="currentFile.size" class="meta-size">{{ formatSize(currentFile.size) }}</span>
          <span class="meta-ready">已选定，准备就绪</span>
        </div>
      </div>
      <div class="file-actions-col">
        <el-button size="small" text type="primary" :icon="RefreshRight" :disabled="disabled" @click="triggerReselect">
          替换
        </el-button>
        <el-button size="small" text type="danger" :icon="Close" :disabled="disabled" @click="removeFile">
          移除
        </el-button>
      </div>
    </div>

    <!-- 还没选文件时的拖拽上传框 -->
    <el-upload
      ref="uploadRef"
      class="upload-dragger-wrap"
      :class="{ 'visually-hidden': hasFile }"
      :file-list="modelValue"
      drag
      :auto-upload="false"
      :limit="1"
      :accept="accept"
      :disabled="disabled"
      @change="handleFileChange"
    >
      <div class="dragger-content">
        <div class="upload-icon-circle">
          <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        </div>
        <div class="el-upload__text">
          <span class="primary-text">点击上传</span> 或将 Excel 文件拖拽到此处
        </div>
        <div class="dragger-hint">支持 .xlsx / .xlsm 格式</div>
      </div>
    </el-upload>
  </div>
</template>

<style scoped>
.file-upload-card-wrapper {
  width: 100%;
  display: flex;
  flex-direction: column;
}

.card-header-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.upload-title {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-regular, #344054);
  font-size: 13px;
}

.step-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  font-size: 11px;
  font-weight: 700;
  color: #2563eb;
  background: #eff6ff;
  border-radius: 4px;
}

.upload-title strong {
  font-weight: 600;
}

.upload-title small {
  color: var(--text-muted, #667085);
  font-weight: 400;
  font-size: 12px;
}

.status-tag {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

/* 就绪卡片态 */
.ready-file-card {
  width: 100%;
  height: 142px;
  padding: 16px 20px;
  background: #ffffff;
  border: 1.5px solid #16855b;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(22, 133, 91, 0.08);
  display: flex;
  align-items: center;
  gap: 16px;
  transition: all 0.2s ease;
}

.excel-icon-box {
  width: 52px;
  height: 58px;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: #16855b;
  position: relative;
}

.excel-icon-box .el-icon {
  font-size: 24px;
}

.file-ext {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.5px;
  line-height: 1;
  margin-top: 2px;
}

.file-info-col {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.file-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-strong, #18212f);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.meta-size {
  color: var(--text-muted, #667085);
  background: #f4f6f8;
  padding: 1px 6px;
  border-radius: 4px;
}

.meta-ready {
  color: #16855b;
}

.file-actions-col {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex-shrink: 0;
}

/* 拖拽上传框微调 */
.upload-dragger-wrap :deep(.el-upload) {
  width: 100%;
}

.upload-dragger-wrap :deep(.el-upload-dragger) {
  width: 100%;
  height: 142px;
  padding: 18px 12px;
  border-radius: 8px;
  border: 1.5px dashed var(--border, #dfe3e8);
  background: #ffffff;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: border-color 0.2s ease, background-color 0.2s ease;
}

.upload-dragger-wrap :deep(.el-upload-dragger:hover) {
  border-color: var(--primary, #2563eb);
  background: #fafcff;
}

.dragger-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}

.upload-icon-circle {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: #eff6ff;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #2563eb;
}

.upload-icon-circle .el-icon--upload {
  font-size: 24px !important;
  margin-bottom: 0 !important;
  color: #2563eb !important;
}

.el-upload__text {
  font-size: 13px;
  color: var(--text-regular, #344054);
}

.primary-text {
  color: #2563eb;
  font-weight: 600;
}

.dragger-hint {
  font-size: 11px;
  color: var(--text-placeholder, #98a2b3);
}

.visually-hidden {
  display: none !important;
}
</style>
