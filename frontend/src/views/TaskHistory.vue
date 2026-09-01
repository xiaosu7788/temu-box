<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Download, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { downloadUrl, errorMessage, getTasks } from '../api'
import type { TaskItem } from '../types'

const tasks = ref<TaskItem[]>([])
const loading = ref(false)

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
    tasks.value = await getTasks()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="section-band">
    <div class="section-heading">
      <div><h2>处理记录</h2><p>最近 {{ tasks.length }} 个任务</p></div>
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
    </div>
    <el-table v-loading="loading" :data="tasks" stripe>
      <el-table-column prop="created_at" label="创建时间" width="170" />
      <el-table-column label="任务编号" min-width="190"><template #default="scope"><span class="mono">{{ scope.row.id }}</span></template></el-table-column>
      <el-table-column label="状态" width="100"><template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ statusText(scope.row.status) }}</el-tag></template></el-table-column>
      <el-table-column label="进度" width="160"><template #default="scope"><el-progress :percentage="scope.row.progress" :stroke-width="8" /></template></el-table-column>
      <el-table-column label="匹配" width="110"><template #default="scope">{{ scope.row.stats.matched ?? '-' }} / {{ scope.row.stats.total ?? '-' }}</template></el-table-column>
      <el-table-column label="结果" width="100" align="right">
        <template #default="scope">
          <el-button v-if="scope.row.download_ready" type="success" link :icon="Download" tag="a" :href="downloadUrl(scope.row.id)">下载</el-button>
          <span v-else>-</span>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && !tasks.length" description="暂无任务" />
  </section>
</template>
