<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ArrowDown, ArrowUp, Check, Close, Clock, Download, Loading, Warning } from '@element-plus/icons-vue'
import { useRoute } from 'vue-router'
import { activityDownloadUrl, downloadUrl, getMyActivityTasks, getTasks } from '../api'
import type { ActivityTaskItem, TaskItem } from '../types'

const route = useRoute()

interface NormalizedTask {
  id: string
  kind: 'order' | 'activity'
  title: string
  status: string
  progress: number
  message: string
  downloadReady: boolean
  downloadUrl: string
  createdAt: string
  logs: string[]
}

const isExpanded = ref(false)
const orderTasks = ref<TaskItem[]>([])
const activityTasks = ref<ActivityTaskItem[]>([])
let pollTimer: number | undefined

const allTasks = computed<NormalizedTask[]>(() => {
  const list: NormalizedTask[] = [
    ...orderTasks.value.map((t) => ({
      id: t.id,
      kind: 'order' as const,
      title: '订单成本计算',
      status: t.status,
      progress: t.progress,
      message: t.message,
      downloadReady: t.download_ready,
      downloadUrl: downloadUrl(t.id),
      createdAt: t.created_at,
      logs: t.logs || [],
    })),
    ...activityTasks.value.map((t) => ({
      id: t.id,
      kind: 'activity' as const,
      title: t.filename ? `报活动: ${t.filename}` : '批量报名活动',
      status: t.status,
      progress: t.progress,
      message: t.message,
      downloadReady: t.download_ready,
      downloadUrl: activityDownloadUrl(t.id),
      createdAt: t.created_at || '',
      logs: t.logs || [],
    })),
  ]
  return list.sort((a, b) => (Date.parse(b.createdAt) || 0) - (Date.parse(a.createdAt) || 0))
})

// 当前正在运行的任务
const activeTasks = computed(() =>
  allTasks.value.filter((t) => ['preparing', 'queued', 'running'].includes(t.status)),
)

// 最优先关注的任务（进行中的第一条，或者最新的一条）
const primaryTask = computed(() => activeTasks.value[0] || null)

// 是否有任何活跃任务在执行
const hasActive = computed(() => activeTasks.value.length > 0)

// 用户主动隐藏（关闭本次提醒）
const dismissedIds = ref<Set<string>>(new Set())

const visibleTasks = computed(() =>
  allTasks.value.slice(0, 3).filter((t) => !dismissedIds.value.has(t.id)),
)

async function pollTasks() {
  try {
    const [orders, activities] = await Promise.all([getTasks(), getMyActivityTasks()])
    orderTasks.value = orders
    activityTasks.value = activities
  } catch {
    // 静默忽略网络/鉴权异常
  }
}

function startPolling() {
  if (pollTimer) window.clearInterval(pollTimer)
  // 如果有活跃任务，每 1.5 秒轮询一次；否则每 8 秒轮询一次
  const interval = hasActive.value ? 1600 : 8000
  pollTimer = window.setInterval(async () => {
    await pollTasks()
    // 根据状态动态切换轮询节奏
    if (hasActive.value && interval !== 1600) {
      startPolling()
    } else if (!hasActive.value && interval !== 8000) {
      startPolling()
    }
  }, interval)
}

function dismissTask(id: string) {
  dismissedIds.value.add(id)
}

onMounted(async () => {
  await pollTasks()
  startPolling()
})

onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer)
})
</script>

<template>
  <aside
    v-if="hasActive || isExpanded"
    class="floating-task-center"
    :class="{ 'is-expanded': isExpanded, 'has-active': hasActive }"
    aria-label="后台任务浮窗"
  >
    <!-- 折叠态胶囊条 -->
    <div
      v-if="!isExpanded && primaryTask"
      class="task-capsule"
      role="button"
      tabindex="0"
      @click="isExpanded = true"
    >
      <span class="capsule-icon">
        <el-icon class="spinning"><Loading /></el-icon>
      </span>
      <div class="capsule-content">
        <span class="capsule-title">{{ primaryTask.title }}</span>
        <span class="capsule-progress">{{ primaryTask.progress }}%</span>
      </div>
      <el-icon class="capsule-arrow"><ArrowUp /></el-icon>
    </div>

    <!-- 展开态浮窗面板 -->
    <div v-else-if="isExpanded" class="task-panel-card">
      <div class="panel-header">
        <div class="header-title">
          <el-icon><Clock /></el-icon>
          <strong>后台任务中心</strong>
          <span v-if="hasActive" class="active-badge">{{ activeTasks.length }} 进行中</span>
        </div>
        <div class="header-actions">
          <el-button text size="small" circle :icon="ArrowDown" title="收起" @click="isExpanded = false" />
        </div>
      </div>

      <div class="task-list-body">
        <div v-if="!visibleTasks.length" class="empty-hint">暂无最近任务</div>
        <div
          v-for="item in visibleTasks"
          :key="item.id"
          class="task-card-item"
          :class="`status-${item.status}`"
        >
          <div class="task-meta-line">
            <span class="task-type-tag" :class="item.kind">
              {{ item.kind === 'order' ? '订单计算' : '批量活动' }}
            </span>
            <span class="task-title" :title="item.title">{{ item.title }}</span>
            <button
              v-if="!['preparing', 'queued', 'running'].includes(item.status)"
              type="button"
              class="dismiss-btn"
              title="忽略此条"
              @click="dismissTask(item.id)"
            >
              <el-icon><Close /></el-icon>
            </button>
          </div>

          <div class="task-progress-line">
            <el-progress
              :percentage="item.progress"
              :status="item.status === 'failed' ? 'exception' : item.status === 'completed' ? 'success' : undefined"
              :stroke-width="6"
              :show-text="false"
            />
            <span class="progress-val">{{ item.progress }}%</span>
          </div>

          <div class="task-status-row">
            <span class="status-msg" :title="item.message">{{ item.message || '运行中...' }}</span>
            <el-button
              v-if="item.downloadReady"
              type="success"
              size="small"
              link
              :icon="Download"
              tag="a"
              :href="item.downloadUrl"
            >
              下载结果
            </el-button>
          </div>
        </div>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.floating-task-center {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 1000;
  font-family: inherit;
  transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}

/* 胶囊条样式 */
.task-capsule {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  background: #18212f;
  color: #ffffff;
  border-radius: 999px;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.2), 0 2px 6px rgba(15, 23, 42, 0.1);
  cursor: pointer;
  user-select: none;
  border: 1px solid rgba(255, 255, 255, 0.12);
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}

.task-capsule:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.28);
}

.capsule-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #60a5fa;
  font-size: 15px;
}

.spinning {
  animation: spin 1.2s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.capsule-content {
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: 220px;
}

.capsule-title {
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.capsule-progress {
  font-size: 12px;
  font-weight: 700;
  color: #93c5fd;
  font-variant-numeric: tabular-nums;
}

.capsule-arrow {
  font-size: 13px;
  opacity: 0.7;
}

/* 展开态浮窗面板 */
.task-panel-card {
  width: 320px;
  background: #ffffff;
  border: 1px solid var(--border, #dfe3e8);
  border-radius: 10px;
  box-shadow: 0 16px 36px rgba(16, 24, 40, 0.14), 0 4px 12px rgba(16, 24, 40, 0.06);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.panel-header {
  padding: 12px 14px;
  background: #fafbfc;
  border-bottom: 1px solid var(--border, #dfe3e8);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.header-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text-strong, #18212f);
}

.active-badge {
  font-size: 11px;
  font-weight: 600;
  background: #eff6ff;
  color: #2563eb;
  padding: 1px 6px;
  border-radius: 999px;
  margin-left: 4px;
}

.task-list-body {
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 360px;
  overflow-y: auto;
}

.empty-hint {
  padding: 20px;
  text-align: center;
  font-size: 12px;
  color: var(--text-muted, #667085);
}

.task-card-item {
  padding: 10px 12px;
  background: #fafbfc;
  border: 1px solid #eef1f5;
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.task-meta-line {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.task-type-tag {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 3px;
  font-weight: 600;
  white-space: nowrap;
}

.task-type-tag.order {
  background: #eff6ff;
  color: #2563eb;
}

.task-type-tag.activity {
  background: #fef3c7;
  color: #b45309;
}

.task-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-strong, #18212f);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1 1 auto;
}

.dismiss-btn {
  background: none;
  border: none;
  padding: 2px;
  cursor: pointer;
  color: var(--text-muted, #98a2b3);
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
}

.dismiss-btn:hover {
  color: var(--text-strong, #18212f);
  background: #e4e7eb;
}

.task-progress-line {
  display: flex;
  align-items: center;
  gap: 8px;
}

.task-progress-line .el-progress {
  flex: 1 1 auto;
  margin: 0;
}

.progress-val {
  font-size: 11px;
  color: var(--text-muted, #667085);
  font-variant-numeric: tabular-nums;
  width: 32px;
  text-align: right;
}

.task-status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 11px;
}

.status-msg {
  color: var(--text-muted, #667085);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1 1 auto;
}

@media (max-width: 720px) {
  .floating-task-center {
    right: 14px;
    bottom: 14px;
  }
  .task-panel-card {
    width: calc(100vw - 28px);
  }
}
</style>
