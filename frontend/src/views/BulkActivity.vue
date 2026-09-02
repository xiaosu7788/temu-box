<script setup lang="ts">
import { computed, onActivated, onBeforeUnmount, onMounted, ref } from 'vue'
import { Delete, Download, RefreshRight, UploadFilled } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile, UploadFiles, UploadUserFile } from 'element-plus'
import { activityDownloadUrl, deleteActivityTask, errorMessage, getActivityTask, getActivityTasks, getMe, processBulkActivity } from '../api'
import type { ActivityTaskItem } from '../types'

const files = ref<UploadUserFile[]>([])
const tasks = ref<ActivityTaskItem[]>([])
const loading = ref(false)
const loadingTasks = ref(false)
const deleting = ref<string | null>(null)
const currentUserId = ref<number | null>(null)
let pollTimer: ReturnType<typeof setInterval> | undefined
const activityTaskStore = new Map<number, ActivityTaskItem[]>()

const canSubmit = computed(() => files.value.length === 1 && !!files.value[0]?.raw)
const activeTasks = computed(() => tasks.value.filter((task) => task.status === 'queued' || task.status === 'running'))

function fileChanged(_file: UploadFile, uploadFiles: UploadFiles) {
  files.value = uploadFiles.slice(-1)
}

function mergeTask(task: ActivityTaskItem) {
  const index = tasks.value.findIndex((item) => item.id === task.id)
  if (index === -1) tasks.value.unshift(task)
  else tasks.value[index] = task
  if (currentUserId.value !== null) {
    const stored = activityTaskStore.get(currentUserId.value) || []
    const storedIndex = stored.findIndex((item) => item.id === task.id)
    if (storedIndex === -1) stored.unshift(task)
    else stored[storedIndex] = task
    activityTaskStore.set(currentUserId.value, stored)
  }
}

async function loadTasks() {
  loadingTasks.value = true
  try {
    const serverTasks = await getActivityTasks()
    const localActive = (activityTaskStore.get(currentUserId.value || -1) || [])
      .filter((task) => task.status === 'queued' || task.status === 'running')
    const merged = new Map(localActive.map((task) => [task.id, task]))
    serverTasks.forEach((task) => merged.set(task.id, task))
    tasks.value = [...merged.values()].sort((left, right) => String(right.created_at).localeCompare(String(left.created_at)))
    activityTaskStore.set(currentUserId.value || -1, tasks.value)
    startPolling()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loadingTasks.value = false
  }
}

async function refreshActiveTasks() {
  const current = [...activeTasks.value]
  await Promise.all(current.map(async (task) => {
    try {
      mergeTask(await getActivityTask(task.id))
    } catch {
      // A temporary polling failure should not remove a task from the list.
    }
  }))
  if (!activeTasks.value.length) stopPolling()
}

function startPolling() {
  if (pollTimer || !activeTasks.value.length) return
  pollTimer = setInterval(() => { void refreshActiveTasks() }, 1500)
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = undefined
}

async function submit() {
  const file = files.value[0]?.raw
  if (!file) return
  loading.value = true
  try {
    const task = await processBulkActivity(file)
    mergeTask(task)
    files.value = []
    startPolling()
    ElMessage.success('任务已提交，后台正在处理')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function reset() {
  files.value = []
}

async function remove(task: ActivityTaskItem) {
  if (task.status === 'queued' || task.status === 'running') return
  try {
    await ElMessageBox.confirm('删除后将无法恢复这条任务记录，是否继续？', '确认删除', { type: 'warning' })
    deleting.value = task.id
    await deleteActivityTask(task.id)
    tasks.value = tasks.value.filter((item) => item.id !== task.id)
    if (currentUserId.value !== null) activityTaskStore.set(currentUserId.value, tasks.value)
    ElMessage.success('活动任务记录已删除')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(errorMessage(error))
  } finally {
    deleting.value = null
  }
}

function statusText(status: string) {
  return { queued: '排队中', running: '处理中', completed: '已完成', failed: '失败' }[status] || status
}

function statusType(status: string) {
  if (status === 'completed') return 'success'
  if (status === 'failed') return 'danger'
  return 'primary'
}

function formatTime(value?: string) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

async function bootstrap() {
  try {
    currentUserId.value = (await getMe()).id
    await loadTasks()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

onMounted(bootstrap)
onActivated(() => { void loadTasks() })
onBeforeUnmount(stopPolling)
</script>

<template>
  <section class="section-band activity-upload-panel">
    <div class="section-heading">
      <div>
        <h2>上传报名商品表</h2>
        <p>提交后由后台处理，切换模块或刷新页面也可以继续查看任务状态</p>
      </div>
      <el-button :icon="RefreshRight" :disabled="loading" @click="reset">重置</el-button>
    </div>

    <el-upload
      v-model:file-list="files"
      drag
      :auto-upload="false"
      :limit="1"
      accept=".xlsx,.xlsm"
      @change="fileChanged"
    >
      <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
      <div class="el-upload__text">选择报名商品信息表</div>
    </el-upload>

    <div class="action-row left">
      <el-button type="primary" :loading="loading" :disabled="!canSubmit" @click="submit">提交处理任务</el-button>
      <el-button :icon="RefreshRight" :loading="loadingTasks" @click="loadTasks">刷新任务</el-button>
    </div>
  </section>

  <section class="section-band activity-tasks-panel">
    <div class="section-heading">
      <div><h2>活动处理任务</h2><p>共 {{ tasks.length }} 个任务{{ activeTasks.length ? `，${activeTasks.length} 个处理中` : '' }}</p></div>
    </div>
    <el-empty v-if="!loadingTasks && !tasks.length" description="暂无活动处理任务" />
    <div v-else class="activity-task-list">
      <article v-for="task in tasks" :key="task.id" class="activity-task-item">
        <div class="activity-task-main">
          <div class="activity-task-title">
            <strong>{{ task.filename }}</strong>
            <el-tag :type="statusType(task.status)" size="small">{{ statusText(task.status) }}</el-tag>
          </div>
          <p>{{ task.message }} · {{ formatTime(task.created_at) }}</p>
          <el-progress :percentage="task.progress" :status="task.status === 'failed' ? 'exception' : task.status === 'completed' ? 'success' : undefined" />
          <p v-if="task.status === 'completed'" class="activity-task-stats">处理 {{ task.stats.processed_rows }} 行 · 替换 {{ task.stats.updated_rows }} 行 · 保留 {{ task.stats.unchanged_rows }} 行 · 删除 {{ task.stats.removed_rows }} 行</p>
          <p v-if="task.status === 'failed' && task.logs.length" class="activity-error">{{ task.logs[task.logs.length - 1] }}</p>
        </div>
        <div class="activity-task-action">
          <el-button v-if="task.download_ready" type="success" :icon="Download" tag="a" :href="activityDownloadUrl(task.id)">下载结果</el-button>
          <el-button v-if="task.status !== 'queued' && task.status !== 'running'" link type="danger" :icon="Delete" :loading="deleting === task.id" @click="remove(task)">删除</el-button>
          <span v-if="!task.download_ready && (task.status === 'queued' || task.status === 'running')" class="activity-task-id mono">{{ task.id }}</span>
        </div>
      </article>
    </div>
  </section>
</template>
