<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { confirmStoreKefuAction, deleteAdminKefuActions, fetchStoreKefuReply, getAdminKefuActions } from '../api'
import { confirmAction, notifyError, notifySuccess } from '../feedback'
import type { StoreKefuAction, StoreKefuActionStage } from '../types'

type AdminKefuAction = StoreKefuAction & { username?: string; display_name?: string }

const items = ref<AdminKefuAction[]>([])
const loading = ref(false)
const stage = ref<StoreKefuActionStage | ''>('')
const limit = ref(100)
const active = ref<AdminKefuAction | null>(null)
const drawerVisible = ref(false)
const refreshingId = ref('')
const keyword = ref('')
const range = ref<[string, string] | null>(null)
const picked = ref<AdminKefuAction[]>([])
const deleting = ref(false)

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
  const failed = items.value.filter((item) => item.stage === 'failed').length
  const replied = items.value.filter((item) => item.stage === 'replied').length
  return `共 ${items.value.length} 条：已回执 ${replied}，失败 ${failed}`
})

async function load() {
  loading.value = true
  try {
    items.value = (await getAdminKefuActions({
      limit: limit.value,
      stage: stage.value || undefined,
      keyword: keyword.value.trim() || undefined,
      start: range.value?.[0],
      end: range.value?.[1],
    })) as AdminKefuAction[]
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

async function retryAction(item: AdminKefuAction) {
  if (refreshingId.value) return
  refreshingId.value = item.id
  try {
    const next = await confirmStoreKefuAction(item.id)
    Object.assign(item, next)
    if (active.value?.id === item.id) active.value = { ...next, username: item.username, display_name: item.display_name }
    notifySuccess('已重新提交')
  } catch (error) {
    notifyError(error)
  } finally {
    refreshingId.value = ''
  }
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return value
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function openDetail(item: AdminKefuAction) {
  active.value = item
  drawerVisible.value = true
}

async function refreshReply(item: AdminKefuAction) {
  refreshingId.value = item.id
  try {
    const next = await fetchStoreKefuReply(item.id)
    Object.assign(item, next)
    if (active.value?.id === item.id) active.value = { ...next, username: active.value.username, display_name: active.value.display_name }
    if (next.stage === 'replied') notifySuccess('已拿到机器人回执')
  } catch (error) {
    notifyError(error)
  } finally {
    refreshingId.value = ''
  }
}

function search() {
  void load()
}

function resetFilters() {
  keyword.value = ''
  range.value = null
  stage.value = ''
  void load()
}

async function removePicked() {
  if (!picked.value.length) return
  const ok = await confirmAction(`删除选中的 ${picked.value.length} 条审计记录？不可恢复。`, '批量删除')
  if (!ok) return
  deleting.value = true
  try {
    await deleteAdminKefuActions(picked.value.map((x) => x.id))
    notifySuccess(`已删除 ${picked.value.length} 条`)
    picked.value = []
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    deleting.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="section-band admin-subpage">
    <div class="section-heading">
      <div class="subpage-title">
        <div>
          <h2>客服操作审计</h2>
          <p>所有用户的店铺客服写操作（下架 / 发布 / 价格刷新 / 异常查询），含报文与机器人回执</p>
        </div>
      </div>
      <div class="toolbar-row">
        <el-input v-model="keyword" class="kw-filter" placeholder="账号 / 工具 / 商品ID" clearable @keyup.enter="search" @clear="search" />
        <el-date-picker
          v-model="range"
          type="datetimerange"
          value-format="YYYY-MM-DDTHH:mm:ss"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          class="time-filter"
          @change="search"
        />
        <el-button type="primary" @click="search">搜索</el-button>
        <el-button @click="resetFilters">重置</el-button>
        <el-button type="danger" plain :disabled="!picked.length" :loading="deleting" @click="removePicked">
          删除选中（{{ picked.length }}）
        </el-button>
        <el-select v-model="stage" class="stage-filter" @change="load">
          <el-option v-for="option in stageOptions" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
        <el-select v-model="limit" class="limit-filter" @change="load">
          <el-option :value="50" label="最近 50 条" />
          <el-option :value="100" label="最近 100 条" />
          <el-option :value="200" label="最近 200 条" />
        </el-select>
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <p class="summary">{{ summary }}</p>

    <el-table
      v-loading="loading"
      :data="items"
      row-key="id"
      stripe
      @selection-change="(v: AdminKefuAction[]) => (picked = v)"
    >
      <el-table-column type="selection" width="44" />
      <el-table-column label="时间" width="112">
        <template #default="scope"><span class="mono">{{ formatTime(scope.row.created_at) }}</span></template>
      </el-table-column>
      <el-table-column label="操作人" width="120">
        <template #default="scope">
          {{ scope.row.display_name || scope.row.username || scope.row.account_masked || '—' }}
        </template>
      </el-table-column>
      <el-table-column prop="tool_name" label="工具" width="140" show-overflow-tooltip />
      <el-table-column label="商品" min-width="220" show-overflow-tooltip>
        <template #default="scope">
          <span class="product">{{ scope.row.product_snapshot?.product_name || '—' }}</span>
          <span class="sub">{{ scope.row.data_type_name }} {{ scope.row.data_id }}</span>
        </template>
      </el-table-column>
      <el-table-column label="原因" width="140" show-overflow-tooltip>
        <template #default="scope">
          <span v-if="scope.row.reason_code">
            {{ scope.row.reason_code }}<template v-if="scope.row.reason_detail"> · {{ scope.row.reason_detail }}</template>
          </span>
          <span v-else class="sub">—</span>
        </template>
      </el-table-column>
      <el-table-column label="阶段" width="108">
        <template #default="scope">
          <el-tag :type="stageType(scope.row.stage)" size="small">{{ stageText(scope.row.stage) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="回执 / 错误" min-width="200" show-overflow-tooltip>
        <template #default="scope">
          {{ scope.row.robot_reply?.content || scope.row.error || '—' }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="130" fixed="right">
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

    <el-empty v-if="!loading && !items.length" description="暂无客服写操作记录" />

    <el-drawer v-model="drawerVisible" title="操作详情" size="min(700px, 100vw)" destroy-on-close>
      <template v-if="active">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="操作 ID" :span="2">{{ active.id }}</el-descriptions-item>
          <el-descriptions-item label="操作人">
            {{ active.display_name || active.username || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="Temu 账号">{{ active.account_masked || '—' }}</el-descriptions-item>
          <el-descriptions-item label="工具">{{ active.tool_name }}</el-descriptions-item>
          <el-descriptions-item label="商品">{{ active.data_type_name }} {{ active.data_id }}</el-descriptions-item>
          <el-descriptions-item label="阶段">{{ stageText(active.stage) }}</el-descriptions-item>
          <el-descriptions-item label="msgId">{{ active.msg_id || '—' }}</el-descriptions-item>
          <el-descriptions-item label="拦截码">{{ active.intercept_code ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="父消息">{{ active.parent_msg_id || '—' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatTime(active.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="完成时间">{{ formatTime(active.finished_at) }}</el-descriptions-item>
        </el-descriptions>

        <h4 class="detail-title">预览内容</h4>
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
  </section>
</template>

<style scoped>
.kw-filter {
  width: 190px;
}

.time-filter {
  width: 340px;
}

.toolbar-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.stage-filter {
  width: 150px;
}

.limit-filter {
  width: 120px;
}

.summary {
  margin: 0 0 10px;
  color: var(--text-muted, #6b7280);
  font-size: 12px;
}

.product {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sub {
  display: block;
  color: var(--text-muted, #9ca3af);
  font-size: 11px;
}

.mono {
  font-family: Consolas, Menlo, monospace;
  font-size: 12px;
}

.detail-title {
  margin: 18px 0 8px;
  font-size: 13px;
}

.detail-json {
  margin: 0;
  max-height: 220px;
  overflow: auto;
  padding: 10px;
  border-radius: 8px;
  background: #0f172a;
  color: #e2e8f0;
  font-size: 12px;
  line-height: 1.5;
}
</style>
