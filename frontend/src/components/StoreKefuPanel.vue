<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  cancelStoreKefuBatch,
  confirmStoreKefuAction,
  createStoreKefuBatch,
  fetchStoreKefuReply,
  getStoreKefuBatch,
  getStoreKefuCard,
  getStoreKefuMessages,
  getStoreKefuTools,
  queryStoreKefuProductBatch,
} from '../api'
import { confirmAction, notifyError, notifySuccess, notifyWarning } from '../feedback'
import { storeSession } from '../storeState'
import type {
  StoreKefuBatch,
  StoreKefuBatchView,
  StoreKefuCard,
  StoreKefuMessage,
  StoreKefuTool,
} from '../types'

type RowStage =
  | 'idle'
  | 'querying'
  | 'ready'
  | 'queued'
  | 'sent'
  | 'accepted'
  | 'degraded'
  | 'retry_pending'
  | 'failed'
  | 'rejected'
  | 'replied'
  | 'unknown'

interface BatchRow {
  data_id: string
  action_id: string
  product_name: string
  product_picture: string
  stage: RowStage
  error: string
  msg_id: string
  robot_reply: string
}

const MAX_IDS = 50

const tools = ref<StoreKefuTool[]>([])
const toolsLoading = ref(false)
const selectedToolId = ref('')
const rawIds = ref('')
const rows = ref<BatchRow[]>([])
const reasonCode = ref<number | null>(null)
const reasonDetail = ref('')
const querying = ref(false)
const executing = ref(false)
const confirmVisible = ref(false)
const confirmed = ref(false)
const batch = ref<StoreKefuBatch | null>(null)
const progress = ref({ total: 0, processed: 0, succeeded: 0, failed: 0, pending: 0, awaiting_reply: 0 })
const refreshingId = ref('')
const card = ref<StoreKefuCard | null>(null)
const cardHint = ref('')
const messages = ref<StoreKefuMessage[]>([])
const messagesLoading = ref(false)
const showMessages = ref(false)
let pollTimer: number | undefined

const selectedTool = computed(() => tools.value.find((tool) => tool.tool_id === selectedToolId.value) || null)
const writesEnabled = computed(() => storeSession.value?.writes_enabled !== false)
const requiresReason = computed(() => (selectedTool.value?.options?.length ?? 0) > 0)
const needsDetail = computed(() => reasonCode.value === 2008)
const parsedIds = computed(() => parseIds(rawIds.value))
const canQuery = computed(() => !!selectedTool.value && parsedIds.value.length > 0)
const canExecute = computed(
  () => canQuery.value && writesEnabled.value && !!selectedTool.value?.enabled && !executing.value,
)
const running = computed(() => batch.value?.status === 'queued' || batch.value?.status === 'running')
const progressPercent = computed(() =>
  progress.value.total ? Math.round((progress.value.processed / progress.value.total) * 100) : 0,
)

function parseIds(text: string): string[] {
  const seen = new Set<string>()
  const result: string[] = []
  for (const chunk of text.split(/[\s,，;；、|]+/)) {
    const value = chunk.trim()
    if (!value || !/^\d+$/.test(value) || seen.has(value)) continue
    seen.add(value)
    result.push(value)
    if (result.length >= MAX_IDS) break
  }
  return result
}

function syncRowsFromIds() {
  const ids = parsedIds.value
  const existing = new Map(rows.value.map((row) => [row.data_id, row]))
  rows.value = ids.map((id) => existing.get(id) || {
    data_id: id,
    action_id: '',
    product_name: '',
    product_picture: '',
    stage: 'idle' as RowStage,
    error: '',
    msg_id: '',
    robot_reply: '',
  })
  batch.value = null
  progress.value = { total: 0, processed: 0, succeeded: 0, failed: 0, pending: 0, awaiting_reply: 0 }
}

watch(
  () => selectedTool.value?.tool_id,
  () => {
    reasonCode.value = selectedTool.value?.options?.[0]?.code ?? null
    reasonDetail.value = ''
  },
  { immediate: true },
)

async function loadTools(force = false) {
  toolsLoading.value = true
  try {
    tools.value = await getStoreKefuTools(force)
    if (!selectedToolId.value && tools.value.length) selectedToolId.value = tools.value[0].tool_id
  } catch (error) {
    notifyError(error)
  } finally {
    toolsLoading.value = false
  }
}

async function loadCard() {
  try {
    // 按当前工具精确找卡片：服务端会自动向前翻页（卡片常被聊天记录挤出最近 50 条）
    card.value = await getStoreKefuCard(selectedTool.value?.tool_id)
    cardHint.value = ''
  } catch (error) {
    card.value = null
    cardHint.value =
      '没有找到该工具的卡片。若确定建立过客服会话，说明卡片已被聊天记录挤出可见范围：'
      + '请在 Temu 后台「联系官方客服 → 自助工具」重新点一次目标工具，让机器人推送新卡片。'
  }
}

// 切换工具时换对应的卡片
watch(selectedToolId, () => { void loadCard() })

async function loadMessages() {
  messagesLoading.value = true
  try {
    messages.value = (await getStoreKefuMessages(50)).items
  } catch (error) {
    messages.value = []
    notifyError(error)
  } finally {
    messagesLoading.value = false
  }
}

async function queryAll() {
  if (!canQuery.value || !selectedTool.value) {
    notifyWarning('请先选择工具并粘贴编号')
    return
  }
  syncRowsFromIds()
  if (!rows.value.length) {
    notifyWarning('没有解析到纯数字编号')
    return
  }
  querying.value = true
  rows.value.forEach((row) => { row.stage = 'querying' })
  try {
    const result = await queryStoreKefuProductBatch({
      type: selectedTool.value.data_type,
      ids: parsedIds.value.join('\n'),
      tool_id: selectedTool.value.tool_id,
    })
    const byId = new Map(result.items.map((item) => [item.data_id, item]))
    rows.value = rows.value.map((row) => {
      const hit = byId.get(row.data_id)
      if (!hit) return { ...row, stage: 'failed' as RowStage, error: '未返回结果' }
      return {
        ...row,
        product_name: hit.product_name || '',
        product_picture: hit.product_picture || '',
        stage: hit.found ? ('ready' as RowStage) : ('failed' as RowStage),
        error: hit.found ? '' : (hit.message || '查不到该商品'),
      }
    })
    const missing = rows.value.filter((row) => row.stage === 'failed').length
    if (missing) notifyWarning(`${rows.value.length} 个编号中有 ${missing} 个查不到商品，将不会执行`)
    else notifySuccess(`已查询 ${rows.value.length} 个商品`)
  } catch (error) {
    notifyError(error)
    rows.value.forEach((row) => { if (row.stage === 'querying') row.stage = 'idle' })
  } finally {
    querying.value = false
  }
}

function openConfirm() {
  if (!selectedTool.value) return
  if (!writesEnabled.value) {
    notifyWarning('客服写操作已被管理员关闭（只读模式）')
    return
  }
  const runnable = rows.value.filter((row) => row.stage === 'ready').length
  if (!runnable) {
    notifyWarning('请先「查询商品」，确认要执行的编号')
    return
  }
  confirmed.value = false
  confirmVisible.value = true
}

async function execute() {
  if (!selectedTool.value || !confirmed.value) return
  const ids = rows.value.filter((row) => row.stage === 'ready').map((row) => row.data_id)
  if (!ids.length) return
  executing.value = true
  try {
    const view = await createStoreKefuBatch({
      tool_id: selectedTool.value.tool_id,
      data_type: selectedTool.value.data_type,
      ids: ids.join('\n'),
      reason_code: requiresReason.value ? reasonCode.value ?? undefined : undefined,
      reason_detail: needsDetail.value ? reasonDetail.value.trim() : undefined,
    })
    confirmVisible.value = false
    applyBatchView(view)
    notifySuccess(`已提交 ${ids.length} 个任务，正在后台执行`)
    startPolling(view.batch.id)
  } catch (error) {
    notifyError(error)
  } finally {
    executing.value = false
  }
}

function applyBatchView(view: StoreKefuBatchView) {
  batch.value = view.batch
  progress.value = view.progress
  const byId = new Map(view.items.map((item) => [item.data_id, item]))
  rows.value = rows.value.map((row) => {
    const hit = byId.get(row.data_id)
    if (!hit) {
      return row.stage === 'ready' ? { ...row, stage: 'queued' as RowStage } : row
    }
    return {
      ...row,
      action_id: hit.action_id || row.action_id,
      product_name: hit.product_name || row.product_name,
      product_picture: hit.product_picture || row.product_picture,
      stage: hit.stage as RowStage,
      error: hit.error,
      msg_id: hit.msg_id,
      robot_reply: hit.robot_reply?.content || '',
    }
  })
}

/** 单行手动拉回执（机器人推送可能晚于批量任务结束） */
async function refreshRow(row: BatchRow) {
  if (!row.action_id || refreshingId.value) return
  refreshingId.value = row.action_id
  try {
    const action = await fetchStoreKefuReply(row.action_id)
    row.stage = action.stage as RowStage
    row.robot_reply = action.robot_reply?.content || ''
    row.error = action.error
    if (action.stage === 'replied') notifySuccess('已拿到最终结果')
    else if (action.stage === 'accepted') notifyWarning('平台已受理，最终结果还没出（稍后再刷新）')
    else notifyWarning('平台还没推送回执，稍后再试')
  } catch (error) {
    notifyError(error)
  } finally {
    refreshingId.value = ''
  }
}

function startPolling(batchId: string) {
  stopPolling()
  const tick = async () => {
    try {
      const view = await getStoreKefuBatch(batchId)
      applyBatchView(view)
      if (view.batch.status === 'done' || view.batch.status === 'cancelled') {
        stopPolling()
        notifySuccess(
          `执行结束：成功 ${view.progress.succeeded}，失败 ${view.progress.failed}`,
        )
        return
      }
    } catch (error) {
      notifyError(error)
      stopPolling()
      return
    }
    pollTimer = window.setTimeout(tick, 1500)
  }
  // 首轮快一点：后端每项提交后已内联拉过一次回执，通常这时就有结果了
  pollTimer = window.setTimeout(tick, 1200)
}

function stopPolling() {
  if (pollTimer) window.clearTimeout(pollTimer)
  pollTimer = undefined
}

async function stopBatch() {
  if (!batch.value) return
  if (!await confirmAction('停止后剩余未执行的编号将不再处理（已提交的无法撤回）。', '停止批量执行')) return
  try {
    const view = await cancelStoreKefuBatch(batch.value.id)
    applyBatchView(view)
    stopPolling()
    notifySuccess('已停止批量任务')
  } catch (error) {
    notifyError(error)
  }
}

function stageText(stage: RowStage): string {
  return {
    idle: '待查询',
    querying: '查询中',
    ready: '待执行',
    queued: '排队中',
    sent: '已提交',
    accepted: '平台处理中',
    degraded: '降级待重试',
    retry_pending: '失败待重试',
    failed: '执行失败',
    rejected: '平台拒绝',
    replied: '已完成',
    unknown: '状态未知',
  }[stage]
}

function stageType(stage: RowStage): 'success' | 'warning' | 'info' | 'danger' {
  if (stage === 'replied') return 'success'
  if (stage === 'rejected' || stage === 'failed') return 'danger'
  if (
    stage === 'sent'
    || stage === 'queued'
    || stage === 'querying'
    || stage === 'accepted'
    || stage === 'retry_pending'
    || stage === 'degraded'
  ) {
    return 'warning'
  }
  return 'info'
}

/** 能手动拉回执的状态 */
function canRefreshReply(stage: RowStage): boolean {
  return stage === 'sent' || stage === 'accepted' || stage === 'unknown'
}

/** 能立即重试的状态（平台降级 / 临时失败 / 自动重试排队中） */
function canRetry(stage: RowStage): boolean {
  return stage === 'retry_pending' || stage === 'failed' || stage === 'degraded'
}

function rowResult(row: BatchRow): string {
  return row.robot_reply || row.error || (row.stage === 'ready' ? '待执行' : '—')
}

/** 手动立即重试（复用已存报文，不会重新拦截） */
async function retryRow(row: BatchRow) {
  if (!row.action_id || refreshingId.value) return
  refreshingId.value = row.action_id
  try {
    const action = await confirmStoreKefuAction(row.action_id)
    row.stage = action.stage as RowStage
    row.error = action.error
    row.msg_id = action.msg_id
    row.robot_reply = action.robot_reply?.content || row.robot_reply
    notifySuccess('已重新提交，等待平台回执')
  } catch (error) {
    notifyError(error)
  } finally {
    refreshingId.value = ''
  }
}

onMounted(async () => {
  await loadTools()
  await Promise.all([loadCard(), loadMessages()])
})

onUnmounted(stopPolling)
</script>

<template>
  <div class="kefu-panel">
    <el-alert
      class="kefu-readonly"
      :type="writesEnabled ? 'info' : 'warning'"
      :closable="false"
      show-icon
      :title="writesEnabled
        ? '批量模式：粘贴编号 → 查询商品 → 一次确认后后台逐个执行；每项间隔 5 秒，50 个约需 4 分钟。失败/降级会自动唤醒卡片后重试。'
        : '客服写操作已被管理员关闭（只读模式）：可以查询商品，但不能执行。'"
    />

    <!-- 工具选择 -->
    <div v-loading="toolsLoading" class="tool-grid">
      <button
        v-for="tool in tools"
        :key="tool.tool_id"
        type="button"
        class="tool-card"
        :class="{ active: selectedToolId === tool.tool_id, muted: !tool.enabled }"
        @click="selectedToolId = tool.tool_id"
      >
        <div class="tool-card__head">
          <strong>{{ tool.name }}</strong>
          <el-tag size="small" effect="plain">{{ tool.data_type }}</el-tag>
        </div>
        <p class="tool-card__meta">
          <span v-if="tool.rate_limit_ms">限流 {{ (tool.rate_limit_ms / 1000).toFixed(0) }}s</span>
          <span v-if="tool.options.length">{{ tool.option_title || '选项' }} {{ tool.options.length }} 项</span>
          <span v-if="!tool.enabled" class="tool-card__off">暂不可用</span>
        </p>
      </button>
      <el-empty v-if="!toolsLoading && !tools.length" description="没有拿到工具定义" :image-size="72">
      <el-button type="primary" size="small" @click="loadTools(true)">重新加载</el-button>
    </el-empty>
    </div>

    <!-- 批量执行区 -->
    <el-card shadow="never" class="batch-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">批量执行{{ selectedTool ? `：${selectedTool.name}` : '' }}</span>
          <span class="card-hint">
            已解析 {{ parsedIds.length }} 个{{ selectedTool ? ` ${selectedTool.data_type}` : '' }}编号（上限 {{ MAX_IDS }} 个）
          </span>
        </div>
      </template>

      <div class="batch-form">
        <el-input
          v-model="rawIds"
          type="textarea"
          :rows="4"
          resize="vertical"
          placeholder="粘贴编号，每行一个或用逗号/空格分隔，例如：&#10;418931287377&#10;418931287380, 418931287381"
          @blur="syncRowsFromIds"
        />

        <div class="batch-options">
          <el-select
            v-if="requiresReason"
            v-model="reasonCode"
            class="reason-select"
            :placeholder="selectedTool?.option_title || '选项'"
          >
            <el-option
              v-for="option in selectedTool?.options || []"
              :key="`${option.code}`"
              :label="`${option.code} ${option.desc}`"
              :value="option.code ?? 0"
            />
          </el-select>

          <el-input
            v-if="needsDetail"
            v-model="reasonDetail"
            class="detail-input"
            maxlength="200"
            show-word-limit
            placeholder="选择「其他」时必填：具体原因"
          />

          <el-button :loading="querying" :disabled="!canQuery" @click="queryAll">查询商品</el-button>
          <el-button
            type="danger"
            :loading="executing"
            :disabled="!canExecute || !rows.some((row) => row.stage === 'ready')"
            @click="openConfirm"
          >
            执行操作{{ rows.filter((row) => row.stage === 'ready').length ? `（${rows.filter((row) => row.stage === 'ready').length}）` : '' }}
          </el-button>
          <el-button v-if="running" type="warning" plain @click="stopBatch">停止</el-button>
        </div>

        <div v-if="batch" class="batch-progress">
          <el-progress :percentage="progressPercent" :status="running ? undefined : 'success'" />
          <div class="progress-meta">
            <span>任务 {{ batch.id.slice(0, 8) }} · {{ batch.status }}</span>
            <span>已处理 {{ progress.processed }} / {{ progress.total }}</span>
            <span class="ok">成功 {{ progress.succeeded }}</span>
            <span class="bad">失败 {{ progress.failed }}</span>
            <span v-if="progress.awaiting_reply" class="awaiting">
              {{ progress.awaiting_reply }} 条等待回执（可点行内「刷新回执」）
            </span>
          </div>
        </div>
      </div>
    </el-card>

    <!-- 商品列表 -->
    <el-table v-if="rows.length" :data="rows" row-key="data_id" stripe class="rows-table">
      <el-table-column label="图" width="64">
        <template #default="scope">
          <el-image
            v-if="scope.row.product_picture"
            :src="scope.row.product_picture"
            fit="cover"
            class="row-thumb"
            :preview-src-list="[scope.row.product_picture]"
            preview-teleported
          />
          <span v-else class="row-thumb row-thumb--empty" />
        </template>
      </el-table-column>
      <el-table-column label="编号" width="170">
        <template #default="scope"><span class="mono">{{ scope.row.data_id }}</span></template>
      </el-table-column>
      <el-table-column label="商品名称" min-width="240" show-overflow-tooltip>
        <template #default="scope">{{ scope.row.product_name || '—' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="108">
        <template #default="scope">
          <el-tag :type="stageType(scope.row.stage)" size="small">{{ stageText(scope.row.stage) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="结果 / 回执" min-width="240" show-overflow-tooltip>
        <template #default="scope">
          <span :class="{ 'row-error': scope.row.error && scope.row.stage === 'failed' }">
            {{ rowResult(scope.row) }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="120" fixed="right">
        <template #default="scope">
          <el-button
            v-if="canRefreshReply(scope.row.stage)"
            link
            type="primary"
            size="small"
            :loading="refreshingId === scope.row.action_id"
            @click="refreshRow(scope.row)"
          >刷新回执</el-button>
          <el-button
            v-else-if="canRetry(scope.row.stage)"
            link
            type="warning"
            size="small"
            :loading="refreshingId === scope.row.action_id"
            @click="retryRow(scope.row)"
          >立即重试</el-button>
          <span v-else class="mono">—</span>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-else-if="!querying" description="粘贴编号后点「查询商品」，这里会列出待执行商品" :image-size="80" />

    <!-- 排障信息 -->
    <el-card shadow="never" class="misc-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">排障信息</span>
          <el-button link @click="showMessages = !showMessages">
            {{ showMessages ? '收起会话消息' : '展开会话消息' }}
          </el-button>
        </div>
      </template>

      <div v-if="cardHint" class="misc-hint">{{ cardHint }}</div>
      <div v-else-if="card" class="card-meta">
        <span>卡片 msgId {{ card.msg_id }}</span>
        <span>toolId {{ card.tool_id }}</span>
        <span>{{ card.data_type }}</span>
        <span v-if="card.options.length">{{ card.option_title }} {{ card.options.length }} 项</span>
      </div>

      <div v-if="showMessages" v-loading="messagesLoading" class="msg-list">
        <div v-for="message in messages" :key="message.msg_id" class="msg-row">
          <el-tag size="small" effect="plain">{{ message.content_type_name }}</el-tag>
          <span class="msg-content">{{ message.content }}</span>
        </div>
        <el-empty v-if="!messagesLoading && !messages.length" description="暂无会话消息" :image-size="60" />
      </div>
    </el-card>

    <!-- 执行确认 -->
    <el-dialog v-model="confirmVisible" title="确认批量执行" width="min(680px, calc(100vw - 32px))" :close-on-click-modal="false">
      <div class="confirm-body">
        <p class="confirm-line">
          即将对 <strong>{{ rows.filter((row) => row.stage === 'ready').length }}</strong> 个商品执行
          <strong>{{ selectedTool?.name }}</strong>
          <template v-if="requiresReason && reasonCode">
            ，原因 <strong>{{ reasonCode }} {{ selectedTool?.options?.find((item) => item.code === reasonCode)?.desc }}</strong>
          </template>
        </p>

        <el-alert
          type="warning"
          :closable="false"
          show-icon
          title="下架不可撤销，只能靠「发布到站点」恢复。"
        />

        <div class="confirm-list">
          <div v-for="row in rows.filter((item) => item.stage === 'ready')" :key="row.data_id" class="confirm-item">
            <el-image v-if="row.product_picture" :src="row.product_picture" fit="cover" class="confirm-thumb" />
            <span class="confirm-name">{{ row.product_name }}</span>
            <span class="mono">{{ row.data_id }}</span>
          </div>
        </div>

        <div class="confirm-check">
          <el-switch v-model="confirmed" />
          <span>我已核对以上商品与操作无误，确认执行</span>
        </div>
      </div>

      <template #footer>
        <el-button @click="confirmVisible = false">取消</el-button>
        <el-button
        type="danger"
        :loading="executing"
        :disabled="!confirmed || !writesEnabled"
        :title="writesEnabled ? '每项间隔 5 秒，失败会自动重试' : '客服写操作已被管理员关闭（只读）'"
        @click="execute"
      >
          确认执行
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.kefu-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.kefu-readonly {
  margin: 0;
}

.tool-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 10px;
  min-height: 72px;
}

.tool-card {
  text-align: left;
  padding: 12px 14px;
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 12px;
  background: #fff;
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease, transform 0.15s ease;
}

.tool-card:hover {
  border-color: var(--primary, #2563eb);
  transform: translateY(-1px);
}

.tool-card.active {
  border-color: var(--primary, #2563eb);
  box-shadow: 0 4px 14px rgba(37, 99, 235, 0.12);
}

.tool-card.muted {
  opacity: 0.62;
}

.tool-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 13px;
}

.tool-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 8px 0 0;
  color: var(--text-muted, #6b7280);
  font-size: 12px;
}

.tool-card__off {
  color: #b45309;
}

.batch-card,
.misc-card {
  border-radius: 12px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.card-title {
  font-size: 13px;
  font-weight: 600;
}

.card-hint {
  color: var(--text-muted, #6b7280);
  font-size: 12px;
}

.batch-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.batch-options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.reason-select {
  width: 210px;
}

.detail-input {
  width: min(320px, 100%);
}

.batch-progress {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 6px;
  border-top: 1px dashed var(--border, #e5e7eb);
}

.progress-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  color: var(--text-muted, #6b7280);
  font-size: 12px;
}

.progress-meta .ok {
  color: #16855b;
}

.progress-meta .bad {
  color: #c2410c;
}

.progress-meta .awaiting {
  color: #b45309;
}

.rows-table {
  width: 100%;
}

.row-thumb {
  width: 40px;
  height: 40px;
  border-radius: 8px;
  background: #f3f4f6;
  display: block;
}

.row-thumb--empty {
  border: 1px dashed var(--border, #e5e7eb);
}

.mono {
  font-family: Consolas, Menlo, monospace;
  font-size: 12px;
}

.row-error {
  color: #c2410c;
}

.misc-hint {
  color: #b45309;
  font-size: 12px;
  line-height: 1.6;
}

.card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  color: var(--text-muted, #6b7280);
  font-size: 12px;
  font-family: Consolas, Menlo, monospace;
}

.msg-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 10px;
  max-height: 260px;
  overflow-y: auto;
}

.msg-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 6px 8px;
  border-radius: 8px;
  background: #f9fafb;
  font-size: 12px;
}

.msg-content {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
  line-height: 1.5;
}

.confirm-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.confirm-line {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
}

.confirm-check {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.confirm-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 240px;
  overflow-y: auto;
  padding: 8px;
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 10px;
}

.confirm-item {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.confirm-thumb {
  width: 32px;
  height: 32px;
  border-radius: 6px;
  background: #f3f4f6;
  flex-shrink: 0;
}

.confirm-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
