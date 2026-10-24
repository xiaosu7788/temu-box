<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { ArrowDown, ArrowUp, CopyDocument, Download, RefreshRight } from '@element-plus/icons-vue'
import type { UploadFile, UploadFiles, UploadUserFile } from 'element-plus'
import { createTask, downloadUrl, getTask } from '../api'
import { notifyError, notifySuccess } from '../feedback'
import type { TaskItem } from '../types'
import CostRules from '../components/CostRules.vue'
import FileUploadCard from '../components/FileUploadCard.vue'
import CopyableText from '../components/CopyableText.vue'
import { selectedCategoryCode as categoryCode, selectedRegionCode as regionCode } from '../regionState'

const salesFiles = ref<UploadUserFile[]>([])
const deliveryFiles = ref<UploadUserFile[]>([])
const task = ref<TaskItem | null>(null)
const submitting = ref(false)
const showLogs = ref(true)
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

async function copyAllLogs() {
  if (!task.value?.logs?.length) return
  const text = task.value.logs.join('\n')
  await navigator.clipboard.writeText(text)
  notifySuccess('已复制全部任务日志')
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
    task.value = await createTask(form, regionCode.value, categoryCode.value)
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
  <CostRules mode="order" :region-code="regionCode" :category-code="categoryCode" />

  <section class="section-band">
    <div class="section-heading">
      <div>
        <h2>创建计算任务</h2>
        <p>服务器库存表将自动参与匹配</p>
      </div>
      <el-button :icon="RefreshRight" @click="reset">重置</el-button>
    </div>

    <div class="upload-grid">
      <FileUploadCard
        v-model="salesFiles"
        step="01"
        title="销售订单"
        subtitle="由 Temu 平台导出的销售订单表"
        :disabled="submitting || !!isActive"
        @change="salesChanged"
      />
      <FileUploadCard
        v-model="deliveryFiles"
        step="02"
        title="派送订单"
        subtitle="由物流或 Temu 平台导出的派送订单表"
        :disabled="submitting || !!isActive"
        @change="deliveryChanged"
      />
    </div>

    <div class="action-row">
      <el-button type="primary" size="large" :disabled="!canSubmit || !!isActive || !regionCode" :loading="submitting" @click="submit">
        开始计算
      </el-button>
    </div>
  </section>

  <section v-if="task" class="section-band task-panel">
    <div class="section-heading">
      <div>
        <h2>任务进度</h2>
        <p>
          <el-tag size="small" effect="plain">{{ task.region_name }}</el-tag>
          <el-tag v-if="task.category_name" size="small" effect="plain" type="info">{{ task.category_name }}</el-tag>
          <CopyableText :text="task.id" tooltip="点击复制任务ID" />
        </p>
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

    <div class="modern-log-window">
      <div class="modern-log-header">
        <div class="modern-log-dots">
          <span class="dot-red" />
          <span class="dot-yellow" />
          <span class="dot-green" />
          <span class="modern-log-title">任务执行日志</span>
        </div>
        <div class="modern-log-tools">
          <el-button text size="small" :icon="CopyDocument" @click="copyAllLogs">复制日志</el-button>
          <el-button text size="small" :icon="showLogs ? ArrowUp : ArrowDown" @click="showLogs = !showLogs">
            {{ showLogs ? '收起' : '展开' }}
          </el-button>
        </div>
      </div>
      <div v-show="showLogs" class="modern-log-content" aria-live="polite">
        <div v-for="line in task.logs" :key="line" class="log-row">{{ line }}</div>
      </div>
    </div>

    <div v-if="task.download_ready" class="action-row left">
      <el-button type="success" :icon="Download" tag="a" :href="downloadUrl(task.id)">下载汇总表</el-button>
    </div>
  </section>
</template>
