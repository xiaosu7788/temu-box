<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { QuestionFilled, Refresh } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { getAdminMonitoring } from '../api'
import { notifyError } from '../feedback'
import AnimatedNumber from '../components/AnimatedNumber.vue'
import type { MonitoringSnapshot, TaskPoolStats } from '../types'

const router = useRouter()
const loading = ref(false)
const data = ref<MonitoringSnapshot | null>(null)
const autoRefresh = ref(true)
const countdown = ref(30)
const pulsing = ref(false)
const lastUpdated = ref('')
let timer: number | undefined
let pulseTimer: number | undefined

const REFRESH_SECONDS = 30
const GB = 1024 ** 3

function human(bytes?: number) {
  if (bytes === undefined) return '-'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  for (const unit of units) {
    if (value < 1024 || unit === 'TB') return `${value.toFixed(1)}${unit}`
    value /= 1024
  }
  return `${value.toFixed(1)}TB`
}

function percentType(percent: number) {
  if (percent >= 90) return 'exception'
  if (percent >= 75) return 'warning'
  return 'success'
}

function poolUse(stats: TaskPoolStats) {
  return stats.queue_limit > 0 ? Math.round((stats.queued / stats.queue_limit) * 100) : 0
}

function statusCount(counts: Record<string, number>, status: string) {
  return counts[status] || 0
}

// 队列状态语义：空闲不再是"白盘"，而是明确的状态徽标
function queueState(stats: TaskPoolStats) {
  if (stats.queued >= stats.queue_limit) return { label: '队列已满', type: 'danger' as const }
  if (stats.queued >= stats.queue_limit * 0.8) return { label: '繁忙', type: 'warning' as const }
  if (stats.active > 0 || stats.queued > 0) return { label: '处理中', type: 'primary' as const }
  return { label: '空闲', type: 'success' as const }
}

const diskPercent = computed(() => data.value?.disk.percent ?? 0)
const memPercent = computed(() => data.value?.memory.percent ?? 0)
const diskAlert = computed(() => diskPercent.value >= 90)
const diskWarn = computed(() => diskPercent.value >= 75)
const memAlert = computed(() => memPercent.value >= 90)
const memWarn = computed(() => memPercent.value >= 75)
const dbOk = computed(() => data.value?.database.status === 'ok')

async function load() {
  loading.value = true
  try {
    data.value = await getAdminMonitoring()
    lastUpdated.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    countdown.value = REFRESH_SECONDS
    // 刷新成功后给卡片一个短暂的高亮脉冲，让用户感知到"数据更新了"
    pulsing.value = true
    if (pulseTimer) window.clearTimeout(pulseTimer)
    pulseTimer = window.setTimeout(() => { pulsing.value = false }, 900)
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
  }
}

// 每秒倒计时驱动自动刷新，替代原来的固定 30s interval
function tick() {
  if (!autoRefresh.value) {
    countdown.value = REFRESH_SECONDS
    return
  }
  countdown.value -= 1
  if (countdown.value <= 0) {
    countdown.value = REFRESH_SECONDS
    load()
  }
}

onMounted(() => {
  load()
  timer = window.setInterval(tick, 1000)
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
  if (pulseTimer) window.clearTimeout(pulseTimer)
})
</script>

<template>
  <div class="admin-page admin-monitoring-page">
    <section class="section-band">
      <div class="section-heading">
        <div class="subpage-title"><div><h2>系统监控</h2><p>磁盘、内存与后台任务队列运行状态</p></div></div>
        <div class="toolbar-row">
          <el-checkbox v-model="autoRefresh" label="30秒自动刷新" />
          <span v-if="autoRefresh" class="countdown-hint">{{ countdown }}s 后刷新</span>
          <span v-if="lastUpdated" class="last-updated">更新于 {{ lastUpdated }}</span>
          <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
        </div>
      </div>

      <div v-if="data" class="monitor-grid" v-loading="loading">
        <el-card shadow="never" class="monitor-card" :class="{ pulse: pulsing, alert: diskAlert }">
          <template #header>
            <div class="card-header">
              <span class="card-title">磁盘使用（数据目录）</span>
              <el-tag v-if="diskAlert" type="danger" size="small" effect="dark">告警</el-tag>
              <el-tag v-else-if="diskWarn" type="warning" size="small">警告</el-tag>
              <el-tooltip content="使用率 ≥75% 显示警告，≥90% 告警并高亮" placement="top">
                <el-icon class="card-hint"><QuestionFilled /></el-icon>
              </el-tooltip>
            </div>
          </template>
          <el-progress type="dashboard" :percentage="data.disk.percent" :status="percentType(data.disk.percent)">
            <template #default>
              <div class="gauge-center"><AnimatedNumber :value="data.disk.percent" :decimals="1" /><span class="gauge-unit">%</span></div>
              <div class="gauge-label">使用率</div>
            </template>
          </el-progress>
          <ul class="metric-list">
            <li>总容量：{{ human(data.disk.total) }}</li>
            <li>已使用：<AnimatedNumber :value="data.disk.used / GB" :decimals="1" />GB</li>
            <li>可用：<AnimatedNumber :value="data.disk.free / GB" :decimals="1" />GB</li>
            <li class="mono metric-path">{{ data.disk.path }}</li>
          </ul>
        </el-card>

        <el-card shadow="never" class="monitor-card" :class="{ pulse: pulsing, alert: memAlert }">
          <template #header>
            <div class="card-header">
              <span class="card-title">内存</span>
              <el-tag v-if="memAlert" type="danger" size="small" effect="dark">告警</el-tag>
              <el-tag v-else-if="memWarn" type="warning" size="small">警告</el-tag>
              <el-tooltip content="使用率 ≥75% 显示警告，≥90% 告警并高亮" placement="top">
                <el-icon class="card-hint"><QuestionFilled /></el-icon>
              </el-tooltip>
            </div>
          </template>
          <template v-if="data.memory.available">
            <el-progress type="dashboard" :percentage="data.memory.percent || 0" :status="percentType(data.memory.percent || 0)">
              <template #default>
                <div class="gauge-center"><AnimatedNumber :value="data.memory.percent || 0" :decimals="1" /><span class="gauge-unit">%</span></div>
                <div class="gauge-label">使用率</div>
              </template>
            </el-progress>
            <ul class="metric-list">
              <li>总内存：{{ human(data.memory.total) }}</li>
              <li>已使用：<AnimatedNumber :value="(data.memory.used || 0) / GB" :decimals="1" />GB</li>
              <li>可用：<AnimatedNumber :value="(data.memory.free || 0) / GB" :decimals="1" />GB</li>
              <li>本进程占用：{{ human(data.memory.process_rss) }}</li>
            </ul>
          </template>
          <el-empty v-else description="内存指标不可用（服务器未安装 psutil）" :image-size="60" />
        </el-card>

        <el-card shadow="never" class="monitor-card clickable" :class="{ pulse: pulsing }">
          <template #header>
            <div class="card-header">
              <span class="card-title">订单任务队列</span>
              <el-tag :type="queueState(data.task_pools.orders).type" size="small">{{ queueState(data.task_pools.orders).label }}</el-tag>
            </div>
          </template>
          <el-progress type="dashboard" :percentage="poolUse(data.task_pools.orders)" :status="percentType(poolUse(data.task_pools.orders))">
            <template #default>
              <div class="gauge-center">
                <AnimatedNumber :value="data.task_pools.orders.queued" />
                <span class="gauge-unit">/ {{ data.task_pools.orders.queue_limit }}</span>
              </div>
              <div class="gauge-label">排队中</div>
            </template>
          </el-progress>
          <ul class="metric-list">
            <li>执行中：<AnimatedNumber :value="data.task_pools.orders.active" /> / 并发 {{ data.task_pools.orders.workers }}</li>
            <li>队列上限：{{ data.task_pools.orders.queue_limit }}</li>
          </ul>
          <div class="card-link" @click="router.push('/admin/tasks')">查看任务记录 →</div>
        </el-card>

        <el-card shadow="never" class="monitor-card clickable" :class="{ pulse: pulsing }">
          <template #header>
            <div class="card-header">
              <span class="card-title">活动任务队列</span>
              <el-tag :type="queueState(data.task_pools.activities).type" size="small">{{ queueState(data.task_pools.activities).label }}</el-tag>
            </div>
          </template>
          <el-progress type="dashboard" :percentage="poolUse(data.task_pools.activities)" :status="percentType(poolUse(data.task_pools.activities))">
            <template #default>
              <div class="gauge-center">
                <AnimatedNumber :value="data.task_pools.activities.queued" />
                <span class="gauge-unit">/ {{ data.task_pools.activities.queue_limit }}</span>
              </div>
              <div class="gauge-label">排队中</div>
            </template>
          </el-progress>
          <ul class="metric-list">
            <li>执行中：<AnimatedNumber :value="data.task_pools.activities.active" /> / 并发 {{ data.task_pools.activities.workers }}</li>
            <li>队列上限：{{ data.task_pools.activities.queue_limit }}</li>
          </ul>
          <div class="card-link" @click="router.push('/admin/tasks')">查看任务记录 →</div>
        </el-card>
      </div>

      <div v-if="data" class="monitor-footer">
        <el-descriptions :column="3" border>
          <el-descriptions-item label="订单任务记录">
            共 {{ Object.values(data.task_counts.orders).reduce((sum, count) => sum + count, 0) }} 条（失败 {{ statusCount(data.task_counts.orders, 'failed') }} / 处理中 {{ statusCount(data.task_counts.orders, 'running') + statusCount(data.task_counts.orders, 'queued') }}）
          </el-descriptions-item>
          <el-descriptions-item label="活动任务记录">
            共 {{ Object.values(data.task_counts.activities).reduce((sum, count) => sum + count, 0) }} 条（失败 {{ statusCount(data.task_counts.activities, 'failed') }} / 处理中 {{ statusCount(data.task_counts.activities, 'running') + statusCount(data.task_counts.activities, 'queued') }}）
          </el-descriptions-item>
          <el-descriptions-item label="数据库状态">
            <span class="db-status">
              <span class="heartbeat-dot" :class="{ bad: !dbOk }"></span>
              {{ data.database.status }}
            </span>
          </el-descriptions-item>
          <el-descriptions-item label="任务文件占用">{{ data.storage.tasks_size }}</el-descriptions-item>
          <el-descriptions-item label="活动文件占用">{{ data.storage.activities_size }}</el-descriptions-item>
          <el-descriptions-item label="文件合计">{{ human(data.storage.tasks_bytes + data.storage.activities_bytes) }}</el-descriptions-item>
        </el-descriptions>
      </div>
    </section>
  </div>
</template>

<style scoped>
.monitor-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}
.monitor-card {
  text-align: center;
  transition: transform .25s ease, box-shadow .25s ease;
}
.monitor-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 8px 24px rgba(16, 24, 40, .1);
}
.monitor-card.clickable:hover {
  cursor: pointer;
}
.monitor-card :deep(.el-card__header) {
  padding: 10px 16px;
  text-align: left;
}
/* 仪表盘弧线随数值平滑过渡 */
.monitor-card :deep(.el-progress-circle path),
.monitor-card :deep(.el-progress-circle circle) {
  transition: stroke-dasharray .8s cubic-bezier(.4, 0, .2, 1);
}
.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
}
.card-title {
  font-weight: 600;
}
.card-hint {
  color: #98a2b3;
  cursor: help;
  margin-left: auto;
}
.gauge-center {
  font-size: 22px;
  font-weight: 600;
  line-height: 1.1;
}
.gauge-unit {
  font-size: 12px;
  color: #98a2b3;
  margin-left: 2px;
}
.gauge-label {
  font-size: 12px;
  color: #98a2b3;
}
.metric-list {
  list-style: none;
  margin: 14px 0 0;
  padding: 0;
  text-align: left;
  font-size: 13px;
  color: #475467;
  display: grid;
  gap: 4px;
}
.metric-path {
  word-break: break-all;
  color: #98a2b3;
  font-size: 12px;
}
.card-link {
  margin-top: 12px;
  font-size: 13px;
  color: var(--el-color-primary);
  cursor: pointer;
  text-align: center;
}
.card-link:hover {
  text-decoration: underline;
}
/* 刷新成功的短暂脉冲 */
.monitor-card.pulse {
  animation: card-pulse .9s ease;
}
@keyframes card-pulse {
  0% { box-shadow: 0 0 0 0 rgba(64, 158, 255, .35); }
  100% { box-shadow: 0 0 0 10px rgba(64, 158, 255, 0); }
}
/* 达到告警阈值时的呼吸高亮 */
.monitor-card.alert {
  animation: alert-breathe 2.2s ease-in-out infinite;
}
.monitor-card.alert:hover {
  animation: none;
}
@keyframes alert-breathe {
  0%, 100% { box-shadow: 0 0 0 1px rgba(245, 108, 108, .45); }
  50% { box-shadow: 0 0 0 5px rgba(245, 108, 108, .12); }
}
.countdown-hint {
  font-size: 12px;
  color: #98a2b3;
  min-width: 62px;
}
.last-updated {
  font-size: 12px;
  color: #98a2b3;
}
/* 数据库心跳指示点 */
.db-status {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.heartbeat-dot {
  position: relative;
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #67c23a;
}
.heartbeat-dot::after {
  content: '';
  position: absolute;
  inset: -3px;
  border-radius: 50%;
  border: 2px solid #67c23a;
  animation: heartbeat 1.8s ease-out infinite;
}
.heartbeat-dot.bad {
  background: #f56c6c;
}
.heartbeat-dot.bad::after {
  border-color: #f56c6c;
}
@keyframes heartbeat {
  0% { transform: scale(.5); opacity: .9; }
  100% { transform: scale(1.6); opacity: 0; }
}
.monitor-footer {
  margin-top: 4px;
}
</style>
