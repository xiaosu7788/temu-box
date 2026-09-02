<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { Download, RefreshRight, UploadFilled } from '@element-plus/icons-vue'
import type { UploadFile, UploadFiles, UploadUserFile } from 'element-plus'
import { createTask, downloadUrl, getTask } from '../api'
import { notifyError } from '../feedback'
import type { TaskItem } from '../types'

const salesFiles = ref<UploadUserFile[]>([])
const deliveryFiles = ref<UploadUserFile[]>([])
const task = ref<TaskItem | null>(null)
const submitting = ref(false)
let pollTimer: number | undefined

const canSubmit = computed(() => salesFiles.value.length === 1 && deliveryFiles.value.length === 1)
const isActive = computed(() => task.value && ['preparing', 'queued', 'running'].includes(task.value.status))

function salesChanged(_file: UploadFile, files: UploadFiles) {
  salesFiles.value = files.slice(-1)
}

function deliveryChanged(_file: UploadFile, files: UploadFiles) {
  deliveryFiles.value = files.slice(-1)
}

function rawFile(files: UploadUserFile[]): File | null {
  return files[0]?.raw || null
}

async function submit() {
  const sales = rawFile(salesFiles.value)
  const delivery = rawFile(deliveryFiles.value)
  if (!sales || !delivery) return
  submitting.value = true
  const form = new FormData()
  form.append('sales', sales)
  form.append('delivery', delivery)
  try {
    task.value = await createTask(form)
    startPolling()
  } catch (error) {
    notifyError(error)
  } finally {
    submitting.value = false
  }
}

function startPolling() {
  if (pollTimer) window.clearInterval(pollTimer)
  pollTimer = window.setInterval(refreshTask, 1200)
  refreshTask()
}

async function refreshTask() {
  if (!task.value) return
  try {
    task.value = await getTask(task.value.id)
    if (!isActive.value && pollTimer) {
      window.clearInterval(pollTimer)
      pollTimer = undefined
    }
  } catch (error) {
    notifyError(error)
  }
}

function reset() {
  salesFiles.value = []
  deliveryFiles.value = []
  task.value = null
}

onBeforeUnmount(() => pollTimer && window.clearInterval(pollTimer))
</script>

<template>
  <section class="section-band">
    <div class="section-heading">
      <div>
        <h2>创建计算任务</h2>
        <p>服务器库存表将自动参与匹配</p>
      </div>
      <el-button :icon="RefreshRight" @click="reset">重置</el-button>
    </div>

    <div class="upload-grid">
      <div class="upload-panel">
        <div class="upload-title"><span>01</span>销售订单</div>
        <el-upload
          v-model:file-list="salesFiles"
          drag
          :auto-upload="false"
          :limit="1"
          accept=".xlsx,.xlsm"
          @change="salesChanged"
        >
          <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
          <div class="el-upload__text">选择销售订单表</div>
        </el-upload>
      </div>
      <div class="upload-panel">
        <div class="upload-title"><span>02</span>派送订单</div>
        <el-upload
          v-model:file-list="deliveryFiles"
          drag
          :auto-upload="false"
          :limit="1"
          accept=".xlsx,.xlsm"
          @change="deliveryChanged"
        >
          <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
          <div class="el-upload__text">选择派送订单表</div>
        </el-upload>
      </div>
    </div>

    <div class="action-row">
      <el-button type="primary" size="large" :disabled="!canSubmit || !!isActive" :loading="submitting" @click="submit">
        开始计算
      </el-button>
    </div>
  </section>

  <section v-if="task" class="section-band task-panel">
    <div class="section-heading">
      <div>
        <h2>任务进度</h2>
        <p class="mono">{{ task.id }}</p>
      </div>
      <el-tag :type="task.status === 'completed' ? 'success' : task.status === 'failed' ? 'danger' : 'primary'">
        {{ task.message }}
      </el-tag>
    </div>
    <el-progress :percentage="task.progress" :status="task.status === 'failed' ? 'exception' : task.status === 'completed' ? 'success' : undefined" />

    <div v-if="task.stats.total !== undefined" class="metric-strip">
      <div><span>订单行</span><strong>{{ task.stats.total }}</strong></div>
      <div><span>已匹配</span><strong>{{ task.stats.matched }}</strong></div>
      <div><span>未匹配</span><strong>{{ task.stats.unmatched }}</strong></div>
      <div><span>缺价格 SKU</span><strong>{{ task.stats.missing_skus?.length || 0 }}</strong></div>
    </div>

    <div class="log-view" aria-live="polite">
      <div v-for="line in task.logs" :key="line">{{ line }}</div>
    </div>

    <div v-if="task.download_ready" class="action-row left">
      <el-button type="success" :icon="Download" tag="a" :href="downloadUrl(task.id)">下载汇总表</el-button>
    </div>
  </section>
</template>
