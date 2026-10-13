<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { CircleCheckFilled, CircleCloseFilled, Download, InfoFilled, Loading, Refresh, RefreshRight, TopRight } from '@element-plus/icons-vue'
import { errorMessage, getAdminUpdateJob, getAdminUpdateStatus, rollbackAdminUpdate, runAdminUpdate } from '../api'
import { confirmAction, notifyError, notifySuccess, notifyWarning } from '../feedback'
import type { UpdateJob, UpdateJobStep, UpdateSnapshot } from '../types'

const REFRESH_SECONDS = 30
const POLL_MS = 2000
// 更新过程中后端容器会被重建，进度查询会连续失败；容忍次数用完后才放弃轮询
const MAX_POLL_FAILURES = 40

type TagType = 'primary' | 'success' | 'info' | 'warning' | 'danger'
type StepState = 'wait' | 'process' | 'finish' | 'error'

const loading = ref(false)
const starting = ref(false)
const autoRefresh = ref(true)
const countdown = ref(REFRESH_SECONDS)
const lastUpdated = ref('')
const enabled = ref(false)
const sidecarError = ref('')
const data = ref<UpdateSnapshot | null>(null)
const job = ref<UpdateJob | null>(null)
const logLines = ref<string[]>([])
const logBox = ref<HTMLElement | null>(null)
const pollFailures = ref(0)

let statusTimer: number | undefined
let pollTimer: number | undefined
let pollActive = false

const running = computed(() => job.value?.status === 'running' || Boolean(data.value?.updater.busy))
const ready = computed(() => Boolean(data.value?.updater.ready))
const notes = computed(() => data.value?.notes || [])
const updateAvailable = computed(() => Boolean(data.value?.project.update_available))
const rollbackAvailable = computed(() => Boolean(data.value?.rollback.available))
const images = computed(() => Object.entries(data.value?.images || {}))
const detailEntries = computed(() => Object.entries(job.value?.detail || {}))

// 面板状态机：一个状态框 + 一个主按钮，对应参考图的「当前版本」卡片
type PanelKind = 'disabled' | 'offline' | 'running' | 'failed' | 'available' | 'uptodate'

const showDetails = ref(false)

const panelKind = computed<PanelKind>(() => {
  if (!enabled.value) return 'disabled'
  if (sidecarError.value) return 'offline'
  if (running.value) return 'running'
  if (job.value?.status === 'failed') return 'failed'
  if (updateAvailable.value) return 'available'
  return 'uptodate'
})

const versionText = computed(
  () => shortSha(data.value?.project.commit.short || data.value?.project.commit.sha) || '读取中',
)

const versionSub = computed(() => {
  if (panelKind.value === 'available') return `最新版本: ${shortSha(data.value?.project.remote.sha) || '读取中'}`
  if (panelKind.value === 'disabled' || panelKind.value === 'offline') return '未连接到运维边车'
  return '已是最新版本'
})

const lastUpdateOk = computed(
  () => data.value?.last_result.kind === 'update' && data.value?.last_result.status === 'ok',
)

const panelTitle = computed(() => {
  switch (panelKind.value) {
    case 'disabled':
      return '一键热更新未启用'
    case 'offline':
      return '运维边车不可用'
    case 'running':
      return job.value?.title || '正在执行更新'
    case 'failed':
      return '上次更新失败'
    case 'available':
      return '有新版本可用！'
    default:
      return lastUpdateOk.value ? '更新完成' : '已是最新版本'
  }
})

const panelDesc = computed(() => {
  switch (panelKind.value) {
    case 'disabled':
      return sidecarError.value || '请在服务器 .env.docker 中设置 UPDATER_TOKEN，并执行 ./scripts/enable-updater.sh 启动运维边车'
    case 'offline':
      return sidecarError.value
    case 'running': {
      const step = job.value?.steps?.[(job.value?.current || 1) - 1]?.label
      return step ? `正在执行：${step}（约 1-2 分钟）` : '正在执行，请勿关闭页面（约 1-2 分钟）'
    }
    case 'failed':
      return job.value?.message || '展开下方更新日志查看失败原因'
    case 'available':
      return data.value?.project.remote.subject || '远端有新的提交，可一键更新'
    default:
      return lastUpdateOk.value
        ? `${data.value?.last_result.after || versionText.value} 已生效，请按 Ctrl+F5 强制刷新页面`
        : `当前运行 ${versionText.value}，无需更新`
  }
})

const panelIcon = computed(() => {
  switch (panelKind.value) {
    case 'disabled':
      return InfoFilled
    case 'offline':
    case 'failed':
      return CircleCloseFilled
    case 'running':
      return Loading
    case 'available':
      return Download
    default:
      return CircleCheckFilled
  }
})

const panelActionLabel = computed(() => {
  if (panelKind.value === 'running') return '正在执行…'
  if (panelKind.value === 'failed') return '重新尝试更新'
  if (panelKind.value === 'uptodate') return '重新检查'
  return '立即更新'
})

const panelActionIcon = computed(() => {
  if (panelKind.value === 'uptodate') return Refresh
  if (panelKind.value === 'failed') return RefreshRight
  return Download
})

const panelActionDisabled = computed(() => {
  if (panelKind.value === 'disabled' || panelKind.value === 'offline' || panelKind.value === 'running') return true
  if (panelKind.value === 'uptodate') return false
  return !enabled.value || !ready.value
})

async function panelAction() {
  if (panelKind.value === 'uptodate') {
    await loadStatus(true)
    return
  }
  await startUpdate()
}

async function loadStatus(manual = false) {
  if (running.value && !manual) return
  loading.value = true
  try {
    const snapshot = await getAdminUpdateStatus()
    enabled.value = snapshot.enabled
    sidecarError.value = snapshot.error
    data.value = snapshot.data
    if (snapshot.job) adoptJob(snapshot.job)
    lastUpdated.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    countdown.value = REFRESH_SECONDS
  } catch (error) {
    sidecarError.value = errorMessage(error)
    if (manual) notifyError(error)
  } finally {
    loading.value = false
  }
}

function adoptJob(next: UpdateJob) {
  if (job.value?.id !== next.id) logLines.value = next.lines
  else if (!logLines.value.length) logLines.value = next.lines
  job.value = next
  if (next.status === 'running') startPolling()
  else stopPolling()
  scrollLog()
}

function scrollLog() {
  const box = logBox.value
  if (!box) return
  window.requestAnimationFrame(() => { box.scrollTop = box.scrollHeight })
}

function stopPolling() {
  pollActive = false
  if (pollTimer) window.clearTimeout(pollTimer)
  pollTimer = undefined
}

function startPolling() {
  if (pollActive) return
  pollActive = true
  schedulePoll()
}

function schedulePoll() {
  if (!pollActive) return
  if (pollTimer) window.clearTimeout(pollTimer)
  pollTimer = window.setTimeout(poll, POLL_MS)
}

async function poll() {
  pollTimer = undefined
  const current = job.value
  if (!pollActive || !current || current.status !== 'running') return
  try {
    const payload = await getAdminUpdateJob(logLines.value.length)
    pollFailures.value = 0
    const next = payload.job
    if (next) {
      job.value = next
      if (next.lines.length) {
        logLines.value = [...logLines.value, ...next.lines]
        scrollLog()
      }
      if (next.status !== 'running') {
        finishJob(next)
        return
      }
    }
  } catch (error) {
    pollFailures.value += 1
    if (pollFailures.value === 5) notifyWarning('后端正在重启，进度暂时读不到，页面会继续自动重试')
    if (pollFailures.value >= MAX_POLL_FAILURES) {
      stopPolling()
      sidecarError.value = errorMessage(error)
      return
    }
  }
  schedulePoll()
}

function finishJob(result: UpdateJob) {
  stopPolling()
  pollFailures.value = 0
  if (result.status === 'ok') notifySuccess(result.message || '操作完成')
  else notifyError(new Error(result.message || '操作失败'))
  loadStatus(true)
}

async function start(action: 'update' | 'rollback') {
  starting.value = true
  try {
    const payload = action === 'update' ? await runAdminUpdate() : await rollbackAdminUpdate()
    if (payload.job) adoptJob(payload.job)
    else {
      logLines.value = []
      startPolling()
    }
    showDetails.value = true
    notifySuccess(action === 'update' ? '更新任务已开始，正在后台执行' : '回滚任务已开始，正在后台执行')
  } catch (error) {
    notifyError(error)
  } finally {
    starting.value = false
  }
}

async function startUpdate() {
  const target = shortSha(data.value?.project.remote.sha) || '远端最新版本'
  const confirmed = await confirmAction(
    `将执行：备份数据 → 拉取代码 → 拉取镜像 → 重启后端与前端 → 健康检查。目标版本：${target}。期间网站会短暂中断 1-2 分钟。`,
    '确认更新到最新版本',
  )
  if (confirmed) await start('update')
}

async function startRollback() {
  const point = data.value?.rollback
  const confirmed = await confirmAction(
    `将回滚到 ${point?.short || '上一次更新前的版本'}${point?.at ? `（${point.at}）` : ''}：代码 reset 到该提交、镜像换回上一版、重启服务。回滚点只能用一次。`,
    '确认回滚',
  )
  if (confirmed) await start('rollback')
}

function tick() {
  if (!autoRefresh.value || running.value || !enabled.value) {
    countdown.value = REFRESH_SECONDS
    return
  }
  countdown.value -= 1
  if (countdown.value <= 0) {
    countdown.value = REFRESH_SECONDS
    loadStatus()
  }
}

function stepStatus(state: UpdateJobStep['state']): StepState {
  if (state === 'done') return 'finish'
  if (state === 'running') return 'process'
  if (state === 'failed') return 'error'
  return 'wait'
}

function stateType(state: string): TagType {
  if (state === 'running') return 'success'
  if (state === 'restarting' || state === 'created' || state === 'starting') return 'warning'
  return 'info'
}

function healthType(health: string): TagType {
  if (health === 'healthy') return 'success'
  if (health === 'unhealthy') return 'danger'
  if (health === 'starting') return 'warning'
  return 'info'
}

function resultType(status?: string): TagType {
  if (status === 'ok') return 'success'
  if (status === 'failed') return 'danger'
  return 'info'
}

function imageLabel(key: string) {
  return key.endsWith('_previous') ? `${key.slice(0, -9)}（上一版）` : key
}

function shortImage(id: string) {
  return id ? id.replace('sha256:', '').slice(0, 12) : '-'
}

function shortSha(value?: string) {
  return value ? value.slice(0, 7) : ''
}

function timeText(value?: string) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'
}

function jobStatusText(status: string) {
  if (status === 'running') return '执行中'
  if (status === 'ok') return '成功'
  if (status === 'failed') return '失败'
  return status
}

// 日志按内容着色：警告黄色，失败红色，其余默认
function logClass(line: string) {
  if (line.includes('[警告]')) return 'warn'
  if (line.includes('结果：failed') || line.includes('失败')) return 'bad'
  return ''
}

onMounted(() => {
  loadStatus()
  statusTimer = window.setInterval(tick, 1000)
})

onBeforeUnmount(() => {
  if (statusTimer) window.clearInterval(statusTimer)
  stopPolling()
})
</script>

<template>
  <div class="admin-page admin-update-page">
    <section class="section-band">
      <div class="section-heading">
        <div class="subpage-title">
          <div>
            <h2>系统更新</h2>
            <p>在后台一键完成备份、拉取代码、更新镜像与重启，不用再登录服务器</p>
          </div>
        </div>
        <div class="toolbar-row">
          <el-checkbox v-model="autoRefresh" label="自动刷新" />
          <span v-if="autoRefresh && !running" class="countdown-hint">{{ countdown }}s 后刷新</span>
          <span v-if="lastUpdated" class="last-updated">更新于 {{ lastUpdated }}</span>
        </div>
      </div>

      <el-alert v-if="notes.length" class="update-alert" type="warning" show-icon :closable="false" title="更新前的检查项">
        <ul class="note-list">
          <li v-for="note in notes" :key="note">{{ note }}</li>
        </ul>
      </el-alert>

      <div class="update-stage">
        <div v-loading="loading" class="update-panel">
          <div class="panel-head">
            <span class="panel-title">当前版本</span>
            <button class="panel-refresh" type="button" title="刷新状态" @click="loadStatus(true)">
              <el-icon :class="{ spinning: loading }"><Refresh /></el-icon>
            </button>
          </div>

          <div class="version-block">
            <div class="version-line">
              <span class="version-text">{{ versionText }}</span>
              <el-icon v-if="panelKind === 'uptodate'" class="version-badge"><CircleCheckFilled /></el-icon>
            </div>
            <div class="version-sub">{{ versionSub }}</div>
          </div>

          <div class="state-box" :class="`state-${panelKind}`">
            <el-icon class="state-icon" :class="{ spinning: panelKind === 'running' }">
              <component :is="panelIcon" />
            </el-icon>
            <div class="state-text">
              <div class="state-title">{{ panelTitle }}</div>
              <div v-if="panelDesc" class="state-desc">{{ panelDesc }}</div>
            </div>
          </div>

          <el-button
            class="panel-action"
            type="primary"
            size="large"
            :icon="panelActionIcon"
            :loading="running || starting"
            :disabled="panelActionDisabled"
            @click="panelAction"
          >
            {{ panelActionLabel }}
          </el-button>

          <button class="panel-link" type="button" @click="showDetails = !showDetails">
            {{ showDetails ? '收起更新日志' : '查看更新日志' }}
            <el-icon><TopRight /></el-icon>
          </button>
        </div>
      </div>

      <div v-if="showDetails" class="update-details">
        <el-card v-if="data" shadow="never" class="update-card">
          <template #header>
            <div class="card-header">
              <span class="card-title">版本信息</span>
              <el-tag v-if="updateAvailable" type="warning" size="small">{{ data?.project.behind || 0 }} 个新提交</el-tag>
              <el-tag v-else type="success" size="small">已是最新</el-tag>
              <el-tag v-if="running" type="danger" size="small" effect="dark">任务进行中</el-tag>
            </div>
          </template>
          <el-descriptions v-if="data" :column="1" border size="small">
            <el-descriptions-item label="当前版本">
              <span class="mono">{{ data.project.commit.short || '-' }}</span> {{ data.project.commit.subject }}
            </el-descriptions-item>
            <el-descriptions-item label="提交时间">{{ timeText(data.project.commit.date) }}</el-descriptions-item>
            <el-descriptions-item label="远端最新">
              <span class="mono">{{ shortSha(data.project.remote.sha) || '读取中' }}</span> {{ data.project.remote.subject || data.project.remote.error || '' }}
            </el-descriptions-item>
            <el-descriptions-item label="落后提交">
              {{ data.project.behind }} 个（{{ data.project.fast_forward ? '可快进更新' : '已分叉，无法自动更新' }}）
            </el-descriptions-item>
            <el-descriptions-item label="分支 / 目录">
              <span class="mono">{{ data.project.branch }}</span> · <span class="mono">{{ data.project.dir }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="本地改动">{{ data.project.dirty ? '有未提交改动（会阻塞更新）' : '无' }}</el-descriptions-item>
            <el-descriptions-item label="数据库">{{ data.env.internal_db ? '内置数据库（db 一并维护）' : '外部数据库（只维护前后端）' }}</el-descriptions-item>
            <el-descriptions-item label="访问端口">{{ data.env.web_port }}</el-descriptions-item>
          </el-descriptions>
          <div v-if="data?.project.incoming.length" class="incoming">
            <div class="incoming-title">待更新的提交</div>
            <ul class="incoming-list">
              <li v-for="item in data.project.incoming" :key="item.short">
                <span class="mono">{{ item.short }}</span> {{ item.subject }}
              </li>
            </ul>
          </div>
        </el-card>

      <el-card v-if="job" shadow="never" class="update-card job-card">
        <template #header>
          <div class="card-header">
            <span class="card-title">{{ job.title }}</span>
            <el-tag :type="job.status === 'ok' ? 'success' : job.status === 'failed' ? 'danger' : 'warning'" size="small">
              {{ jobStatusText(job.status) }}
            </el-tag>
            <span class="last-updated">开始于 {{ timeText(job.started_at) }}</span>
          </div>
        </template>
        <el-steps
          :active="job.current"
          align-center
          finish-status="success"
          :process-status="job.status === 'failed' ? 'error' : 'process'"
        >
          <el-step v-for="step in job.steps" :key="step.label" :title="step.label" :status="stepStatus(step.state)" />
        </el-steps>
        <div v-if="job.message" class="job-message" :class="{ bad: job.status === 'failed', good: job.status === 'ok' }">
          {{ job.message }}
        </div>
        <el-descriptions v-if="detailEntries.length" class="job-detail" :column="3" border size="small">
          <el-descriptions-item v-for="[key, value] in detailEntries" :key="key" :label="key">{{ value }}</el-descriptions-item>
        </el-descriptions>
        <div ref="logBox" class="job-log">
          <div v-for="(line, index) in logLines" :key="index" class="log-line" :class="logClass(line)">{{ line || ' ' }}</div>
        </div>
      </el-card>

      <el-card v-if="data" shadow="never" class="update-card">
        <template #header>
          <div class="card-header">
            <span class="card-title">容器状态</span>
            <span class="last-updated">来自 docker compose ps / docker inspect</span>
          </div>
        </template>
        <el-table :data="data.services" size="small" empty-text="没有服务信息">
          <el-table-column prop="name" label="服务" width="100" />
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag :type="stateType(row.state)" size="small">{{ row.state }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="status" label="运行信息" min-width="170" show-overflow-tooltip />
          <el-table-column label="健康" width="100">
            <template #default="{ row }">
              <el-tag v-if="row.health" :type="healthType(row.health)" size="small">{{ row.health }}</el-tag>
              <span v-else class="muted">无探针</span>
            </template>
          </el-table-column>
          <el-table-column prop="image" label="镜像" min-width="240" show-overflow-tooltip />
          <el-table-column label="镜像 ID" width="120">
            <template #default="{ row }"><span class="mono">{{ shortImage(row.image_id) }}</span></template>
          </el-table-column>
          <el-table-column label="启动时间" width="180">
            <template #default="{ row }"><span class="mono">{{ timeText(row.started_at) }}</span></template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card v-if="data" shadow="never" class="update-card">
        <template #header>
          <div class="card-header">
            <span class="card-title">镜像与工具</span>
            <span class="last-updated">上一版镜像留作回滚用</span>
          </div>
        </template>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item v-for="[key, image] in images" :key="key" :label="imageLabel(key)">
            <span class="mono">{{ image.short || image.id || '未找到' }}</span>
            <span v-if="!image.present" class="muted">（本地不存在）</span>
            <span v-else-if="image.created" class="muted"> · {{ timeText(image.created) }}</span>
          </el-descriptions-item>
        </el-descriptions>
        <ul class="metric-list">
          <li>
            Docker {{ data.tools.docker || '未知' }} · Compose {{ data.tools.compose || '未知' }} · Git {{ data.tools.git || '未知' }}
          </li>
          <li>运维边车 v{{ data.updater.version }} · 状态目录 <span class="mono">{{ data.env.state_dir }}</span></li>
        </ul>
      </el-card>

      <el-card v-if="data?.last_result.message" shadow="never" class="update-card">
        <template #header>
          <div class="card-header">
            <span class="card-title">上次执行结果</span>
            <el-tag :type="resultType(data.last_result.status)" size="small">
              {{ data.last_result.kind === 'rollback' ? '回滚' : data.last_result.kind === 'update' ? '更新' : '操作' }}
            </el-tag>
            <span class="last-updated">{{ timeText(data.last_result.at) }}</span>
          </div>
        </template>
        <div class="job-message" :class="{ bad: data.last_result.status === 'failed', good: data.last_result.status === 'ok' }">
          {{ data.last_result.message }}
        </div>
        <el-descriptions :column="2" border size="small" class="job-detail">
          <el-descriptions-item v-if="data.last_result.before" label="更新前">
            <span class="mono">{{ data.last_result.before }}</span>
          </el-descriptions-item>
          <el-descriptions-item v-if="data.last_result.after" label="更新后">
            <span class="mono">{{ data.last_result.after }}</span>
          </el-descriptions-item>
          <el-descriptions-item v-if="data.last_result.target" label="目标">{{ data.last_result.target }}</el-descriptions-item>
          <el-descriptions-item v-if="data.last_result.step" label="阶段">{{ data.last_result.step }}</el-descriptions-item>
          <el-descriptions-item v-if="data.last_result.images" label="镜像">{{ data.last_result.images }}</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <el-card shadow="never" class="update-card">
        <template #header>
          <div class="card-header">
            <span class="card-title">回滚</span>
            <span class="last-updated">回滚点只能用一次</span>
          </div>
        </template>
        <el-button
          size="large"
          class="action-button"
          :icon="RefreshRight"
          :loading="starting"
          :disabled="!enabled || running || !rollbackAvailable"
          @click="startRollback"
        >
          回滚上一次更新
        </el-button>
        <ul class="metric-list">
          <li>流程：备份数据 → 拉取代码 → 拉取镜像 → 重启服务 → 健康检查，由运维边车串行执行</li>
          <li>更新会同时重建后端与前端，避免新旧接口字段不一致</li>
          <li>更新期间网站中断约 1-2 分钟，完成后请按 Ctrl+F5 强制刷新页面</li>
          <li>回滚点：{{ rollbackAvailable ? `${data?.rollback.short}（${timeText(data?.rollback.at)}）` : '暂无，在本页执行过一次更新后才会生成' }}</li>
        </ul>
      </el-card>
    </div>
    </section>
  </div>
</template>

<style scoped>
.update-alert {
  margin-top: 12px;
}

.note-list {
  margin: 4px 0 0;
  padding-left: 18px;
  line-height: 1.7;
}


.update-card {
  margin-top: 16px;
}

.update-grid .update-card {
  margin-top: 0;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.card-title {
  margin-right: auto;
  font-weight: 600;
}

.countdown-hint,
.last-updated {
  color: #909399;
  font-size: 12px;
}

.muted {
  color: #909399;
}

.metric-list {
  margin: 12px 0 0;
  padding-left: 18px;
  color: #606266;
  font-size: 13px;
  line-height: 1.8;
}

.incoming {
  margin-top: 12px;
}

.incoming-title {
  margin-bottom: 4px;
  color: #606266;
  font-size: 13px;
}

.incoming-list {
  margin: 0;
  padding-left: 18px;
  max-height: 180px;
  overflow: auto;
  font-size: 13px;
  line-height: 1.7;
}

.action-button {
  width: 100%;
  margin: 0 0 10px;
}

.action-button + .action-button {
  margin-left: 0;
}

.job-message {
  margin-top: 16px;
  padding: 10px 12px;
  border-radius: 4px;
  background: #f4f4f5;
  color: #606266;
  font-size: 13px;
  line-height: 1.6;
}

.job-message.good {
  background: #f0f9eb;
  color: #529b2e;
}

.job-message.bad {
  background: #fef0f0;
  color: #c45656;
}

.job-detail {
  margin-top: 12px;
}

.job-log {
  margin-top: 12px;
  padding: 10px 12px;
  max-height: 260px;
  overflow: auto;
  border-radius: 4px;
  background: #1f1f1f;
  color: #d7d7d7;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
}

.log-line {
  white-space: pre-wrap;
  word-break: break-all;
}

.log-line.warn {
  color: #f0c674;
}

.log-line.bad {
  color: #ff8080;
}
.update-stage {
  display: flex;
  justify-content: center;
  margin-top: 16px;
}

.update-panel {
  width: 100%;
  max-width: 420px;
  padding: 18px 22px 14px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 6px 20px rgba(24, 33, 47, .06);
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.panel-title {
  color: var(--text-regular);
  font-size: 14px;
}

.panel-refresh {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  color: var(--text-muted);
  background: transparent;
  cursor: pointer;
}

.panel-refresh:hover {
  color: var(--primary);
  background: #f1f5fb;
}

.version-block {
  padding: 16px 0 14px;
  text-align: center;
}

.version-line {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.version-text {
  color: var(--text-strong);
  font-size: 30px;
  font-weight: 700;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.version-badge {
  color: var(--success);
  font-size: 20px;
}

.version-sub {
  margin-top: 6px;
  color: var(--text-muted);
  font-size: 13px;
}

.state-box {
  display: flex;
  gap: 10px;
  padding: 12px 14px;
  border: 1px solid transparent;
  border-radius: 8px;
}

.state-icon {
  flex: 0 0 auto;
  margin-top: 1px;
  font-size: 18px;
}

.state-text {
  min-width: 0;
}

.state-title {
  font-size: 14px;
  font-weight: 600;
}

.state-desc {
  margin-top: 3px;
  color: var(--text-muted);
  font-size: 12.5px;
  line-height: 1.6;
  word-break: break-word;
}

.state-available {
  border-color: #f3d19e;
  background: #fdf6ec;
}

.state-available .state-icon,
.state-available .state-title {
  color: #b88230;
}

.state-uptodate {
  border-color: #bfe0d2;
  background: #eef7f2;
}

.state-uptodate .state-icon,
.state-uptodate .state-title {
  color: var(--success);
}

.state-running {
  border-color: #bfd3f5;
  background: #eef4ff;
}

.state-running .state-icon,
.state-running .state-title {
  color: var(--primary);
}

.state-failed {
  border-color: #f3c3bf;
  background: #fdeceb;
}

.state-failed .state-icon,
.state-failed .state-title {
  color: var(--danger);
}

.state-disabled,
.state-offline {
  border-color: var(--border);
  background: var(--surface-subtle);
}

.state-disabled .state-icon,
.state-disabled .state-title,
.state-offline .state-icon,
.state-offline .state-title {
  color: var(--text-muted);
}

.panel-action {
  width: 100%;
  margin-top: 14px;
  height: 44px;
  border-radius: 8px;
  font-size: 15px;
}

.panel-link {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  width: 100%;
  margin-top: 8px;
  padding: 6px;
  border: 0;
  color: var(--text-muted);
  background: transparent;
  font-size: 13px;
  cursor: pointer;
}

.panel-link:hover {
  color: var(--primary);
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.update-details {
  margin-top: 20px;
}


</style>
