<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Delete, Download, Refresh } from '@element-plus/icons-vue'
import { activityDownloadUrl, deleteActivityTask, deleteTask, downloadUrl, getMyActivityTasks, getTasks } from '../api'
import { confirmAction, notifyError, notifySuccess } from '../feedback'
import type { ActivityTaskItem, TaskItem } from '../types'

const tasks = ref<TaskItem[]>([])
const activityTasks = ref<ActivityTaskItem[]>([])
const loading = ref(false)
const deleting = ref<string | null>(null)

interface HistoryItem {
  id: string
  kind: 'order' | 'activity'
  taskType: '订单计算' | '批量报活动'
  title: string
  createdAt: string
  status: string
  regionName: string
  progress: number
  summary: string
  downloadReady: boolean
}

const historyItems = computed<HistoryItem[]>(() => [
  ...tasks.value.map((task) => ({
    id: task.id,
    kind: 'order' as const,
    taskType: '订单计算' as const,
    title: '订单成本计算',
    createdAt: task.created_at,
    status: task.status,
    regionName: task.region_name || task.region_code || '美国区',
    progress: task.progress,
    summary: task.stats.total === undefined
      ? (task.message || '-')
      : `匹配 ${task.stats.matched ?? 0} / ${task.stats.total}`,
    downloadReady: task.download_ready,
  })),
  ...activityTasks.value.map((task) => ({
    id: task.id,
    kind: 'activity' as const,
    taskType: '批量报活动' as const,
    title: task.filename,
    createdAt: task.created_at || '',
    status: task.status,
    regionName: task.region_name || task.region_code || '美国区',
    progress: task.progress,
    summary: task.stats?.processed_rows === undefined
      ? (task.message || '-')
      : `处理 ${task.stats.processed_rows} 行，删除 ${task.stats.removed_rows ?? 0} 行`,
    downloadReady: task.download_ready,
  })),
].sort((left, right) => (Date.parse(right.createdAt) || 0) - (Date.parse(left.createdAt) || 0)))

function statusText(status: string) {
  return { preparing: '准备中', queued: '排队中', running: '处理中', completed: '已完成', failed: '失败' }[status] || status
}

function statusType(status: string) {
  if (status === 'completed') return 'success'
  if (status === 'failed') return 'danger'
  return 'primary'
}

async function load() {
  loading.value = true
  try {
    ;[tasks.value, activityTasks.value] = await Promise.all([getTasks(), getMyActivityTasks()])
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
  }
}

async function remove(task: HistoryItem) {
  if (['preparing', 'queued', 'running'].includes(task.status)) return
  const deletingKey = `${task.kind}:${task.id}`
  try {
    if (!await confirmAction(`删除这条${task.taskType}任务记录？删除后无法恢复。`, '确认删除')) return
    deleting.value = deletingKey
    if (task.kind === 'order') {
      await deleteTask(task.id)
      tasks.value = tasks.value.filter((item) => item.id !== task.id)
    } else {
      await deleteActivityTask(task.id)
      activityTasks.value = activityTasks.value.filter((item) => item.id !== task.id)
    }
    notifySuccess('任务记录已删除')
  } catch (error) {
    notifyError(error)
  } finally {
    deleting.value = null
  }
}

onMounted(load)
</script>

<template>
  <section class="section-band">
    <div class="section-heading">
      <div><h2>处理记录</h2><p>共 {{ historyItems.length }} 个任务</p></div>
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
    </div>
    <el-table v-loading="loading" :data="historyItems" stripe>
      <el-table-column prop="createdAt" label="创建时间" width="180" />
      <el-table-column label="任务类型" width="125">
        <template #default="scope"><el-tag :type="scope.row.kind === 'order' ? 'primary' : 'warning'" effect="plain">{{ scope.row.taskType }}</el-tag></template>
      </el-table-column>
      <el-table-column label="任务内容" min-width="250">
        <template #default="scope"><div>{{ scope.row.title }}</div><div class="mono table-muted">{{ scope.row.id }}</div></template>
      </el-table-column>
<el-table-column prop="regionName" label="区域" width="110" />
      <el-table-column label="状态" width="100"><template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ statusText(scope.row.status) }}</el-tag></template></el-table-column>
      <el-table-column label="进度" width="160"><template #default="scope"><el-progress :percentage="scope.row.progress" :stroke-width="8" /></template></el-table-column>
      <el-table-column prop="summary" label="处理情况" min-width="190" />
      <el-table-column label="结果" width="100" align="right">
        <template #default="scope">
          <el-button v-if="scope.row.downloadReady" type="success" link :icon="Download" tag="a" :href="scope.row.kind === 'order' ? downloadUrl(scope.row.id) : activityDownloadUrl(scope.row.id)">下载</el-button>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="110" fixed="right">
        <template #default="scope">
          <el-button v-if="!['preparing', 'queued', 'running'].includes(scope.row.status)" link type="danger" :icon="Delete" :loading="deleting === `${scope.row.kind}:${scope.row.id}`" @click="remove(scope.row)">删除</el-button>
          <span v-else class="table-muted">处理中</span>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && !historyItems.length" description="暂无任务" />
  </section>
</template>
