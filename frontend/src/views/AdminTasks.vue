<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ArrowLeft, Delete, Download, Refresh } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { activityDownloadUrl, deleteActivityTask, deleteTask, downloadUrl, getAdminActivityTasks, getAdminTasks } from '../api'
import { confirmAction, notifyError, notifySuccess } from '../feedback'
import type { ActivityTaskItem, TaskItem } from '../types'

const router = useRouter()
const orderTasks = ref<TaskItem[]>([])
const activityTasks = ref<ActivityTaskItem[]>([])
const loading = ref(false)
const deleting = ref<string | null>(null)

async function load() {
  loading.value = true
  try {
    ;[orderTasks.value, activityTasks.value] = await Promise.all([getAdminTasks(), getAdminActivityTasks()])
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
  }
}

function statusText(status: string) {
  return { preparing: '准备中', queued: '排队中', running: '处理中', completed: '已完成', failed: '失败' }[status] || status
}

function statusType(status: string) {
  if (status === 'completed') return 'success'
  if (status === 'failed') return 'danger'
  return 'primary'
}

async function removeOrderTask(task: TaskItem) {
  if (['preparing', 'queued', 'running'].includes(task.status)) return
  const key = `order:${task.id}`
  try {
    if (!await confirmAction(`删除用户 ${task.owner_username || task.owner_name || '-'} 的订单计算任务？删除后无法恢复。`, '管理员删除任务')) return
    deleting.value = key
    await deleteTask(task.id)
    orderTasks.value = orderTasks.value.filter((item) => item.id !== task.id)
    notifySuccess('订单计算任务已删除')
  } catch (error) {
    notifyError(error)
  } finally {
    deleting.value = null
  }
}

async function removeActivityTask(task: ActivityTaskItem) {
  if (['queued', 'running'].includes(task.status)) return
  const key = `activity:${task.id}`
  try {
    if (!await confirmAction(`删除用户 ${task.owner_username || task.owner_name || '-'} 的批量报活动任务？删除后无法恢复。`, '管理员删除任务')) return
    deleting.value = key
    await deleteActivityTask(task.id)
    activityTasks.value = activityTasks.value.filter((item) => item.id !== task.id)
    notifySuccess('批量报活动任务已删除')
  } catch (error) {
    notifyError(error)
  } finally {
    deleting.value = null
  }
}

onMounted(load)
</script>

<template>
  <div class="admin-page admin-tasks-page">
    <section class="section-band">
      <div class="section-heading">
        <div class="subpage-title"><el-button text :icon="ArrowLeft" @click="router.push('/admin')">后台管理</el-button><div><h2>任务记录</h2><p>查看所有用户的订单计算和批量报名活动任务</p></div></div>
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </div>
      <el-tabs>
        <el-tab-pane label="订单计算任务">
          <el-table v-loading="loading" :data="orderTasks" stripe>
            <el-table-column prop="owner_name" label="用户" min-width="130" />
            <el-table-column prop="owner_username" label="用户名" min-width="140" />
            <el-table-column prop="created_at" label="创建时间" width="180" />
            <el-table-column label="任务编号" min-width="190"><template #default="scope"><span class="mono">{{ scope.row.id }}</span></template></el-table-column>
            <el-table-column label="状态" width="100"><template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ statusText(scope.row.status) }}</el-tag></template></el-table-column>
            <el-table-column label="进度" width="150"><template #default="scope"><el-progress :percentage="scope.row.progress" :stroke-width="8" /></template></el-table-column>
            <el-table-column label="结果" width="90"><template #default="scope"><el-button v-if="scope.row.download_ready" type="success" link :icon="Download" tag="a" :href="downloadUrl(scope.row.id)">下载</el-button><span v-else>-</span></template></el-table-column>
            <el-table-column label="操作" width="110" fixed="right"><template #default="scope"><el-button v-if="!['preparing', 'queued', 'running'].includes(scope.row.status)" link type="danger" :icon="Delete" :loading="deleting === `order:${scope.row.id}`" @click="removeOrderTask(scope.row)">删除</el-button><span v-else class="table-muted">处理中</span></template></el-table-column>
          </el-table>
          <el-empty v-if="!loading && !orderTasks.length" description="暂无订单计算任务" />
        </el-tab-pane>
        <el-tab-pane label="批量报名活动任务">
          <el-table v-loading="loading" :data="activityTasks" stripe>
            <el-table-column prop="owner_name" label="用户" min-width="130" />
            <el-table-column prop="owner_username" label="用户名" min-width="140" />
            <el-table-column prop="created_at" label="创建时间" width="180" />
            <el-table-column prop="filename" label="文件名" min-width="190" />
            <el-table-column label="状态" width="100"><template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ statusText(scope.row.status) }}</el-tag></template></el-table-column>
            <el-table-column label="进度" width="150"><template #default="scope"><el-progress :percentage="scope.row.progress" :stroke-width="8" /></template></el-table-column>
            <el-table-column label="结果" width="90"><template #default="scope"><el-button v-if="scope.row.download_ready" type="success" link :icon="Download" tag="a" :href="activityDownloadUrl(scope.row.id)">下载</el-button><span v-else>-</span></template></el-table-column>
            <el-table-column label="操作" width="110" fixed="right"><template #default="scope"><el-button v-if="!['queued', 'running'].includes(scope.row.status)" link type="danger" :icon="Delete" :loading="deleting === `activity:${scope.row.id}`" @click="removeActivityTask(scope.row)">删除</el-button><span v-else class="table-muted">处理中</span></template></el-table-column>
          </el-table>
          <el-empty v-if="!loading && !activityTasks.length" description="暂无报名活动任务" />
        </el-tab-pane>
      </el-tabs>
    </section>
  </div>
</template>
