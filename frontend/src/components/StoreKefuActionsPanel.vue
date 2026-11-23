<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { fetchStoreKefuReply, confirmStoreKefuAction, getStoreKefuActions } from '../api'
import { notifyError, notifySuccess } from '../feedback'
import type { StoreKefuAction, StoreKefuActionStage } from '../types'

const items = ref<StoreKefuAction[]>([])
const loading = ref(false)
const stage = ref<StoreKefuActionStage | ''>('')
const active = ref<StoreKefuAction | null>(null)
const drawerVisible = ref(false)
const refreshingId = ref('')

const stageOptions: Array<{ label: string; value: StoreKefuActionStage | '' }> = [
  { label: '全部阶段', value: '' },
  { label: '已预览', value: 'preview' },
  { label: '已提交', value: 'sent' },
  { label: '平台处理中', value: 'accepted' },
  { label: '降级待重试', value: 'degraded' },
  { label: '失败待重试', value: 'retry_pending' },
  { label: '执行失败', value: 'failed' },
  { label: '平台拒绝', value: 'rejected' },
  { label: '已出结果', value: 'replied' },
  { label: '状态未知', value: 'unknown' },
]

const summary = computed(() => {
  const total = items.value.length
  const replied = items.value.filter((item) => item.stage === 'replied').length
  return `共 ${total} 条记录，其中 ${replied} 条已拿到机器人回执`
})

async function load() {
  loading.value = true
  try {
    items.value = await getStoreKefuActions(50, stage.value || undefined)
  } catch (error) {
    notifyError(error)
    items.value = []
  } finally {
    loading.value = false
  }
}

function stageText(value: StoreKefuActionStage): string {
  return {
    preview: '已预览',
    sent: '已提交',
    accepted: '平台处理中',
    degraded: '降级待重试',
    retry_pending: '失败待重试',
    failed: '执行失败',
    rejected: '平台拒绝',
    replied: '已出结果',
    unknown: '状态未知',
  }[value] || value
}

function stageType(value: StoreKefuActionStage): 'success' | 'warning' | 'info' | 'danger' {
  if (value === 'replied') return 'success'
  if (value === 'rejected' || value === 'failed') return 'danger'
  if (value === 'sent' || value === 'accepted' || value === 'retry_pending' || value === 'degraded') return 'warning'
  return 'info'
}

function canRefresh(value: StoreKefuActionStage): boolean {
  return value === 'sent' || value === 'accepted' || value === 'unknown'
}

function canRetry(value: StoreKefuActionStage): boolean {
  return value === 'retry_pending' || value === 'failed' || value === 'degraded'
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return value
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function openDetail(item: StoreKefuAction) {
  active.value = item
  drawerVisible.value = true
}

async function refreshReply(item: StoreKefuAction) {  refreshingId.value = item.id
  try {
    const next = await fetchStoreKefuReply(item.id)
    Object.assign(item, next)
    if (active.value?.id === item.id) active.value = next
    if (next.stage === 'replied') notifySuccess('已拿到机器人回执')
  } catch (error) {
    notifyError(error)
  } finally {
    refreshingId.value = ''
  }
}

async function retryAction(item: StoreKefuAction) {
  if (refreshingId.value) return
  refreshingId.value = item.id
  try {
    const next = await confirmStoreKefuAction(item.id)
    Object.assign(item, next)
    if (active.value?.id === item.id) active.value = next
    notifySuccess('已重新提交，等待平台回执')
  } catch (error) {
    notifyError(error)
  } finally {
    refreshingId.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div class="actions-panel">
    <div class="actions-toolbar">
      <el-select v-model="stage" class="actions-stage" @change="load">
        <el-option v-for="option in stageOptions" :key="option.value" :label="option.label" :value="option.value" />
      </el-select>
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      <span class="actions-summary">{{ summary }}</span>
    </div>

    <el-table v-loading="loading" :data="items" row-key="id" stripe>
      <el-table-column label="时间" width="118">
        <template #default="scope">{{ formatTime(scope.row.created_at) }}</template>
      </el-table-column>
      <el-table-column prop="tool_name" label="工具" width="150" show-overflow-tooltip />
      <el-table-column label="商品" min-width="240" show-overflow-tooltip>
        <template #default="scope">
          <span class="actions-product">{{ scope.row.product_snapshot?.product_name || '—' }}</span>
          <span class="actions-id">{{ scope.row.data_type_name }} {{ scope.row.data_id }}</span>
        </template>
      </el-table-column>
      <el-table-column label="原因" width="150" show-overflow-tooltip>
        <template #default="scope">
          <span v-if="scope.row.reason_code">
            {{ scope.row.reason_code }}
            <template v-if="scope.row.reason_detail"> · {{ scope.row.reason_detail }}</template>
          </span>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column label="阶段" width="112">
        <template #default="scope">
          <el-tag :type="stageType(scope.row.stage)" size="small">{{ stageText(scope.row.stage) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="回执 / 错误" min-width="220" show-overflow-tooltip>
        <template #default="scope">
          {{ scope.row.robot_reply?.content || scope.row.error || '—' }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="132" fixed="right">
        <template #default="scope">
          <el-button link type="primary" size="small" @click="openDetail(scope.row)">详情</el-button>
          <el-button
            v-if="canRefresh(scope.row.stage)"
            link
            size="small"
            :loading="refreshingId === scope.row.id"
            @click="refreshReply(scope.row)"
          >拉回执</el-button>
          <el-button
            v-else-if="canRetry(scope.row.stage)"
            link
            type="warning"
            size="small"
            :loading="refreshingId === scope.row.id"
            @click="retryAction(scope.row)"
          >立即重试</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!loading && !items.length" description="还没有操作记录" />

    <el-drawer v-model="drawerVisible" title="操作详情" size="min(680px, 100vw)" destroy-on-close>
      <template v-if="active">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="操作 ID" :span="2">{{ active.id }}</el-descriptions-item>
          <el-descriptions-item label="工具">{{ active.tool_name }}</el-descriptions-item>
          <el-descriptions-item label="商品">{{ active.data_type_name }} {{ active.data_id }}</el-descriptions-item>
          <el-descriptions-item label="阶段">{{ stageText(active.stage) }}</el-descriptions-item>
          <el-descriptions-item label="msgId">{{ active.msg_id || '—' }}</el-descriptions-item>
          <el-descriptions-item label="拦截码">{{ active.intercept_code ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="父消息">{{ active.parent_msg_id || '—' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatTime(active.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="完成时间">{{ formatTime(active.finished_at) }}</el-descriptions-item>
        </el-descriptions>

        <h4 class="detail-title">商品快照</h4>
        <pre class="detail-json">{{ JSON.stringify(active.product_snapshot, null, 2) }}</pre>

        <h4 class="detail-title">预览内容（contentType=7 的 content）</h4>
        <pre class="detail-json">{{ JSON.stringify(active.preview, null, 2) }}</pre>

        <h4 class="detail-title">请求报文</h4>
        <pre class="detail-json">{{ JSON.stringify(active.request_body, null, 2) }}</pre>

        <h4 class="detail-title">上游响应</h4>
        <pre class="detail-json">{{ JSON.stringify(active.response, null, 2) }}</pre>

        <h4 class="detail-title">机器人回执</h4>
        <pre class="detail-json">{{ JSON.stringify(active.robot_reply, null, 2) }}</pre>

        <el-button
          v-if="active.stage === 'sent' || active.stage === 'accepted'"
          type="primary"
          :loading="refreshingId === active.id"
          @click="refreshReply(active)"
        >拉取回执</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.actions-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.actions-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.actions-stage {
  width: 150px;
}

.actions-summary {
  color: var(--text-muted, #6b7280);
  font-size: 12px;
}

.actions-product {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.actions-id {
  display: block;
  color: var(--text-muted, #9ca3af);
  font-size: 11px;
  font-family: Consolas, Menlo, monospace;
}

.detail-title {
  margin: 18px 0 8px;
  font-size: 13px;
}

.detail-json {
  margin: 0;
  max-height: 240px;
  overflow: auto;
  padding: 10px;
  border-radius: 8px;
  background: #0f172a;
  color: #e2e8f0;
  font-size: 12px;
  line-height: 1.5;
}
</style>
