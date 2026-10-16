<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { TopRight } from '@element-plus/icons-vue'
import { getAdminUpdateJob, getAdminUpdateStatus, getVersion, rollbackAdminUpdate, runAdminUpdate } from '../api'
import { confirmAction, notifyError, notifySuccess, notifyWarning } from '../feedback'
import type { UpdateJob, UpdateSnapshot, VersionEntry, VersionInfo } from '../types'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()

// 更新期间后端容器会被重建，进度查询会连续失败；容忍若干次后才放弃轮询
const POLL_MS = 2000
const MAX_POLL_FAILURES = 40

const loading = ref(false)
const checking = ref(false)
const starting = ref(false)
const info = ref<VersionInfo | null>(null)
const snapshot = ref<UpdateSnapshot | null>(null)
const job = ref<UpdateJob | null>(null)
const logLines = ref<string[]>([])
const pollFailures = ref(0)

let pollTimer: number | undefined
let pollActive = false

const currentVersion = computed(() => info.value?.version || '')
const remoteVersion = computed(() => snapshot.value?.remote_version || '')
const updaterEnabled = computed(() => Boolean(snapshot.value))
// 边车是服务器上单独构建的镜像，一键更新不会重建它；老边车没有 version 字段
const staleSidecar = computed(() => updaterEnabled.value && !snapshot.value?.version)
const remoteHint = computed(() => {
  if (!updaterEnabled.value) return '未启用更新检查'
  if (staleSidecar.value) return '边车版本过旧：请在服务器执行 ./scripts/enable-updater.sh 重建'
  return '暂时读不到远端版本'
})
const entries = computed<VersionEntry[]>(() => info.value?.changelog || [])
const running = computed(() => job.value?.status === 'running' || Boolean(snapshot.value?.updater.busy))
const ready = computed(() => Boolean(snapshot.value?.updater.ready))
const rollbackAvailable = computed(() => Boolean(snapshot.value?.rollback.available))

function parts(value: string) {
  return value.split(/[.\-+]/).slice(0, 3).map((piece) => Number.parseInt(piece, 10) || 0)
}

const updateAvailable = computed(() => {
  const remote = remoteVersion.value
  if (!remote || !currentVersion.value) return false
  const local = parts(currentVersion.value)
  const latest = parts(remote)
  for (let index = 0; index < 3; index += 1) {
    if (latest[index] !== local[index]) return latest[index] > local[index]
  }
  return false
})

// 只有「发现有新版本 + 边车就绪 + 没有任务在跑」时才允许点升级
const canUpdate = computed(
  () => updateAvailable.value && ready.value && !running.value && !starting.value && updaterEnabled.value,
)

const updateLabel = computed(() => {
  if (running.value) return '更新中…'
  if (starting.value) return '提交中…'
  return '更新版本'
})

const banner = computed(() => {
  if (running.value) return { tone: 'running', title: `正在更新：${job.value?.title || '更新到最新版本'}`, desc: stepText.value }
  if (job.value?.status === 'ok') {
    return { tone: 'done', title: '更新完成', desc: `${job.value.message || ''}，请按 Ctrl+F5 强制刷新页面` }
  }
  if (job.value?.status === 'failed') {
    return { tone: 'failed', title: '更新失败', desc: job.value.message || '展开下方日志查看原因' }
  }
  if (updateAvailable.value) {
    return { tone: 'new', title: `发现新版本: v${remoteVersion.value}`, desc: snapshot.value?.project.remote.subject || '' }
  }
  return null
})

const stepText = computed(() => {
  const steps = job.value?.steps || []
  const index = (job.value?.current || 0) - 1
  return index >= 0 && index < steps.length ? `${index + 1}/${steps.length} ${steps[index].label}` : '正在准备…'
})

function pillType(title: string) {
  if (title.includes('新增')) return 'new'
  if (title.includes('优化')) return 'better'
  if (title.includes('修复')) return 'fix'
  return 'other'
}

function logClass(line: string) {
  if (line.includes('[警告]')) return 'warn'
  if (line.includes('结果：failed') || line.includes('失败')) return 'bad'
  return ''
}

function stepClass(state: string) {
  if (state === 'done') return 'done'
  if (state === 'running') return 'active'
  if (state === 'failed') return 'failed'
  return ''
}

function close() {
  emit('update:modelValue', false)
}

function onVisible(value: boolean) {
  emit('update:modelValue', value)
}

function stopPolling() {
  pollActive = false
  if (pollTimer) window.clearTimeout(pollTimer)
  pollTimer = undefined
}

function schedulePoll() {
  if (!pollActive) return
  if (pollTimer) window.clearTimeout(pollTimer)
  pollTimer = window.setTimeout(poll, POLL_MS)
}

function startPolling() {
  if (pollActive) return
  pollActive = true
  schedulePoll()
}

function adoptJob(next: UpdateJob) {
  if (job.value?.id !== next.id) logLines.value = next.lines
  else if (!logLines.value.length) logLines.value = next.lines
  job.value = next
  if (next.status === 'running') startPolling()
  else stopPolling()
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
      if (next.lines.length) logLines.value = [...logLines.value, ...next.lines]
      if (next.status !== 'running') {
        stopPolling()
        if (next.status === 'ok') notifySuccess(next.message || '更新完成')
        else notifyError(new Error(next.message || '更新失败'))
        await load()
        return
      }
    }
  } catch (error) {
    pollFailures.value += 1
    if (pollFailures.value === 5) notifyWarning('后端正在重启，进度暂时读不到，弹窗会继续自动重试')
    if (pollFailures.value >= MAX_POLL_FAILURES) {
      stopPolling()
      notifyError(error)
      return
    }
  }
  schedulePoll()
}

async function load(manual = false) {
  if (manual) checking.value = true
  else loading.value = true
  try {
    info.value = await getVersion()
  } catch (error) {
    info.value = null
    notifyError(error)
  }
  try {
    // 未启用一键热更新时拿不到快照，这里静默降级：只显示当前版本与本地日志
    const payload = await getAdminUpdateStatus()
    snapshot.value = payload.data
    if (payload.job) adoptJob(payload.job)
  } catch {
    snapshot.value = null
  }
  loading.value = false
  checking.value = false
}

async function startUpdate() {
  const confirmed = await confirmAction(
    `将执行：备份数据 → 拉取代码 → 拉取镜像 → 重启后端与前端 → 健康检查。目标版本：v${remoteVersion.value}。期间网站会短暂中断 1-2 分钟。`,
    '确认更新到最新版本',
  )
  if (!confirmed) return
  starting.value = true
  try {
    const payload = await runAdminUpdate()
    logLines.value = []
    if (payload.job) adoptJob(payload.job)
    else startPolling()
    notifySuccess('更新任务已开始，正在后台执行')
  } catch (error) {
    notifyError(error)
  } finally {
    starting.value = false
  }
}

async function startRollback() {
  const point = snapshot.value?.rollback
  const confirmed = await confirmAction(
    `将回滚到 ${point?.short || '上一次更新前的版本'}：代码 reset 到该提交、镜像换回上一版、重启服务。回滚点只能用一次。`,
    '确认回滚',
  )
  if (!confirmed) return
  starting.value = true
  try {
    const payload = await rollbackAdminUpdate()
    logLines.value = []
    if (payload.job) adoptJob(payload.job)
    else startPolling()
    notifySuccess('回滚任务已开始，正在后台执行')
  } catch (error) {
    notifyError(error)
  } finally {
    starting.value = false
  }
}

function openReleasePage() {
  window.open('https://github.com/xiaosu7788/temu-box/commits/main', '_blank')
}

watch(
  () => props.modelValue,
  (visible) => {
    if (visible) void load()
    else stopPolling()
  },
)

onBeforeUnmount(stopPolling)
</script>

<template>
  <el-dialog :model-value="props.modelValue" width="720px" append-to-body @update:model-value="onVisible">
    <template #header>
      <div class="dialog-head">
        <strong>版本更新</strong>
        <span>查看当前版本和更新日志</span>
      </div>
    </template>

    <div v-loading="loading" class="version-body">
      <div class="version-row">
        <div class="version-box">
          <span class="box-label">当前版本</span>
          <strong class="box-value">{{ currentVersion ? `v${currentVersion}` : '—' }}</strong>
        </div>
        <div class="version-box">
          <div class="box-head">
            <span class="box-label">最新版本</span>
            <button class="check-button" type="button" :disabled="checking" @click="load(true)">
              {{ checking ? '检查中…' : '检查更新' }}
            </button>
          </div>
          <strong v-if="remoteVersion" class="box-value" :class="{ newer: updateAvailable }">v{{ remoteVersion }}</strong>
          <span v-else class="box-muted">{{ remoteHint }}</span>
        </div>
      </div>

      <div v-if="banner" class="banner" :class="`banner-${banner.tone}`">
        <div class="banner-text">
          <strong>{{ banner.title }}</strong>
          <span v-if="banner.desc">{{ banner.desc }}</span>
        </div>
        <span v-if="banner.tone === 'new'" class="banner-pill">可更新</span>
      </div>

      <div v-if="job" class="job-block">
        <div class="job-steps">
          <span v-for="(step, index) in job.steps" :key="step.label" class="job-step" :class="stepClass(step.state)">
            {{ index + 1 }}. {{ step.label }}
          </span>
        </div>
        <div class="job-log">
          <div v-for="(line, index) in logLines" :key="index" class="log-line" :class="logClass(line)">{{ line || ' ' }}</div>
        </div>
      </div>

      <div class="log-list">
        <div v-for="(entry, index) in entries" :key="entry.version" class="log-entry">
          <div class="entry-head">
            <strong>{{ entry.version }}</strong>
            <span class="entry-date">{{ entry.date }}</span>
            <span v-if="index === 0" class="entry-latest">最新</span>
          </div>
          <div v-for="group in entry.groups" :key="group.title" class="entry-group">
            <div v-for="(item, itemIndex) in group.items" :key="itemIndex" class="entry-item">
              <span class="item-pill" :class="pillType(group.title)">{{ group.title }}</span>
              <span class="item-text">{{ item }}</span>
            </div>
          </div>
        </div>
        <el-empty v-if="!loading && !entries.length" description="暂无更新日志" :image-size="60" />
      </div>
    </div>

    <template #footer>
      <div class="dialog-foot">
        <div class="foot-links">
          <button class="release-link" type="button" @click="openReleasePage">
            打开发布页
            <el-icon><TopRight /></el-icon>
          </button>
          <button v-if="rollbackAvailable" class="release-link" type="button" :disabled="running || starting" @click="startRollback">
            回滚到上一版本
          </button>
        </div>
        <el-button type="primary" :loading="running || starting" :disabled="!canUpdate" @click="startUpdate">
          {{ updateLabel }}
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.version-body {
  display: grid;
  gap: 14px;
}

.version-row {
  display: flex;
  gap: 12px;
}

.version-box {
  flex: 1 1 0;
  min-width: 0;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
}

.box-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.box-label {
  color: var(--text-muted);
  font-size: 12.5px;
}

.box-value {
  display: block;
  margin-top: 6px;
  color: var(--text-strong);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 22px;
  font-weight: 700;
}

.box-value.newer {
  color: var(--danger);
}

.box-muted {
  display: block;
  margin-top: 6px;
  color: var(--text-placeholder);
  font-size: 13px;
}

.check-button {
  padding: 0;
  border: 0;
  color: var(--primary);
  background: transparent;
  font-size: 12.5px;
  cursor: pointer;
}

.check-button:disabled {
  color: var(--text-placeholder);
  cursor: default;
}

.banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  border: 1px solid transparent;
  border-radius: 8px;
}

.banner-text strong {
  display: block;
  font-size: 14px;
}

.banner-text span {
  display: block;
  margin-top: 2px;
  color: var(--text-muted);
  font-size: 12.5px;
}

.banner-pill {
  flex: 0 0 auto;
  padding: 2px 10px;
  border-radius: 999px;
  background: var(--success);
  color: #fff;
  font-size: 12px;
}

.banner-new {
  border-color: #f3d19e;
  background: #fdf6ec;
}

.banner-new .banner-text strong {
  color: #b88230;
}

.banner-running {
  border-color: #bfd3f5;
  background: #eef4ff;
}

.banner-running .banner-text strong {
  color: var(--primary);
}

.banner-done {
  border-color: #bfe0d2;
  background: #eef7f2;
}

.banner-done .banner-text strong {
  color: var(--success);
}

.banner-failed {
  border-color: #f3c3bf;
  background: #fdeceb;
}

.banner-failed .banner-text strong {
  color: var(--danger);
}

.job-block {
  display: grid;
  gap: 8px;
}

.job-steps {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  font-size: 12.5px;
}

.job-step {
  color: var(--text-placeholder);
}

.job-step.done {
  color: var(--success);
}

.job-step.active {
  color: var(--primary);
  font-weight: 600;
}

.job-step.failed {
  color: var(--danger);
  font-weight: 600;
}

.job-log {
  max-height: 150px;
  overflow: auto;
  padding: 8px 10px;
  border-radius: 6px;
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

.log-list {
  max-height: 300px;
  overflow: auto;
  padding-right: 4px;
}

.log-entry + .log-entry {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px dashed var(--border);
}

.entry-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.entry-head strong {
  color: var(--text-strong);
  font-size: 15px;
}

.entry-date {
  color: var(--text-muted);
  font-size: 12.5px;
}

.entry-latest {
  padding: 1px 8px;
  border-radius: 999px;
  background: #eef7f2;
  color: var(--success);
  font-size: 12px;
}

.entry-group + .entry-group {
  margin-top: 6px;
}

.entry-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 4px 0;
}

.item-pill {
  flex: 0 0 auto;
  margin-top: 1px;
  padding: 1px 8px;
  border-radius: 4px;
  font-size: 12px;
}

.item-pill.new {
  background: #eef4ff;
  color: var(--primary);
}

.item-pill.better {
  background: #fdf6ec;
  color: #b88230;
}

.item-pill.fix {
  background: #fdeceb;
  color: var(--danger);
}

.item-pill.other {
  background: #f4f6f8;
  color: var(--text-muted);
}

.item-text {
  color: var(--text-regular);
  font-size: 13px;
  line-height: 1.7;
}

.dialog-head strong {
  display: block;
  color: var(--text-strong);
  font-size: 16px;
}

.dialog-head span {
  color: var(--text-muted);
  font-size: 12.5px;
}

.dialog-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.foot-links {
  display: flex;
  align-items: center;
  gap: 16px;
}

.release-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0;
  border: 0;
  color: var(--text-muted);
  background: transparent;
  font-size: 13px;
  cursor: pointer;
}

.release-link:hover {
  color: var(--primary);
}

.release-link:disabled {
  color: var(--text-placeholder);
  cursor: default;
}
</style>
