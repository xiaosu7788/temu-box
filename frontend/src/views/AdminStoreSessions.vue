<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Refresh, SwitchButton } from '@element-plus/icons-vue'
import { getAdminStoreSessions, revokeAdminStoreSession } from '../api'
import { confirmAction, notifyError, notifySuccess } from '../feedback'
import type { AdminStoreSession, StoreSessionStatus } from '../types'

const items = ref<AdminStoreSession[]>([])
const loading = ref(false)
const statusFilter = ref<StoreSessionStatus | ''>('')
const revokingId = ref<number | null>(null)

const statusOptions: Array<{ label: string; value: StoreSessionStatus | '' }> = [
  { label: '全部状态', value: '' },
  { label: '在线', value: 'active' },
  { label: '登录中', value: 'logging_in' },
  { label: '冷却中', value: 'cooldown' },
  { label: '会话失效', value: 'invalid' },
  { label: '已退出', value: 'logged_out' },
]

const filtered = computed(() =>
  statusFilter.value ? items.value.filter((item) => item.status === statusFilter.value) : items.value,
)

const stats = computed(() => ({
  active: items.value.filter((item) => item.status === 'active').length,
  cooldown: items.value.filter((item) => item.status === 'cooldown').length,
  invalid: items.value.filter((item) => item.status === 'invalid').length,
  total: items.value.length,
}))

async function load() {
  loading.value = true
  try {
    items.value = await getAdminStoreSessions()
  } catch (error) {
    notifyError(error)
    items.value = []
  } finally {
    loading.value = false
  }
}

function statusText(status: StoreSessionStatus): string {
  return {
    active: '在线',
    logging_in: '登录中',
    cooldown: '冷却中',
    invalid: '会话失效',
    logged_out: '已退出',
  }[status] || status
}

function statusType(status: StoreSessionStatus): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'active') return 'success'
  if (status === 'cooldown' || status === 'logging_in') return 'warning'
  if (status === 'invalid') return 'danger'
  return 'info'
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return value
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function remainingHours(item: AdminStoreSession): string {
  if (item.status !== 'active') return '—'
  return `${Math.max(0, Math.round(item.expires_in_seconds / 3600))} 小时`
}

async function revoke(item: AdminStoreSession) {
  if (!await confirmAction(
    `强制退出「${item.display_name || item.username}」的店铺会话？该 Temu 账号会立即释放，用户需要重新登录。`,
    '强制退出店铺会话',
  )) return
  revokingId.value = item.user_id
  try {
    const result = await revokeAdminStoreSession(item.user_id)
    notifySuccess(result.revoked ? '已强制退出该用户的店铺会话' : '该用户当前没有活跃会话')
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    revokingId.value = null
  }
}

onMounted(load)
</script>

<template>
  <section class="section-band admin-subpage">
    <div class="section-heading">
      <div class="subpage-title">
        <div>
          <h2>店铺会话</h2>
          <p>每个用户一个 Temu 店铺会话；同一账号全系统只允许一个活跃会话</p>
        </div>
      </div>
      <div class="toolbar-row">
        <el-select v-model="statusFilter" class="status-filter">
          <el-option v-for="option in statusOptions" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <div class="stats-row">
      <div class="stat-card"><span>在线</span><strong>{{ stats.active }}</strong></div>
      <div class="stat-card"><span>冷却中</span><strong>{{ stats.cooldown }}</strong></div>
      <div class="stat-card"><span>会话失效</span><strong>{{ stats.invalid }}</strong></div>
      <div class="stat-card"><span>有记录用户</span><strong>{{ stats.total }}</strong></div>
    </div>

    <el-alert class="hint" type="info" :closable="false" show-icon>
      强制退出会销毁 bridge 侧的会话文件并释放该 Temu 账号；用户下次使用需要重新输入账号密码。
    </el-alert>

    <el-table v-loading="loading" :data="filtered" row-key="user_id" stripe>
      <el-table-column label="用户" min-width="150">
        <template #default="scope">
          <strong>{{ scope.row.display_name || scope.row.username }}</strong>
          <span class="sub">{{ scope.row.username }}</span>
        </template>
      </el-table-column>
      <el-table-column label="Temu 账号" min-width="160">
        <template #default="scope">{{ scope.row.account_masked || '—' }}</template>
      </el-table-column>
      <el-table-column label="店铺" min-width="140" show-overflow-tooltip>
        <template #default="scope">{{ scope.row.mall_name || '—' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="104">
        <template #default="scope">
          <el-tag :type="statusType(scope.row.status)" size="small">{{ statusText(scope.row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="登录时间" width="150">
        <template #default="scope"><span class="mono">{{ formatTime(scope.row.login_at) }}</span></template>
      </el-table-column>
      <el-table-column label="会话剩余" width="96">
        <template #default="scope"><span class="mono">{{ remainingHours(scope.row) }}</span></template>
      </el-table-column>
      <el-table-column label="记住密码" width="96">
        <template #default="scope">
          <el-tag v-if="scope.row.remember" size="small" effect="plain">已保存</el-tag>
          <span v-else class="sub">—</span>
        </template>
      </el-table-column>
      <el-table-column label="最后错误" min-width="180" show-overflow-tooltip>
        <template #default="scope">
          <span v-if="scope.row.last_error">{{ scope.row.last_error }}</span>
          <span v-else class="sub">—</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="130" fixed="right">
        <template #default="scope">
          <el-button
            v-if="scope.row.account_masked"
            link
            type="danger"
            size="small"
            :icon="SwitchButton"
            :loading="revokingId === scope.row.user_id"
            @click="revoke(scope.row)"
          >强制退出</el-button>
          <span v-else class="sub">无会话</span>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!loading && !filtered.length" description="没有匹配的店铺会话" />
  </section>
</template>

<style scoped>
.status-filter {
  width: 140px;
}

.toolbar-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}

.stat-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 14px;
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 12px;
  background: #fff;
}

.stat-card span {
  color: var(--text-muted, #6b7280);
  font-size: 12px;
}

.stat-card strong {
  font-size: 20px;
  font-variant-numeric: tabular-nums;
}

.hint {
  margin-bottom: 12px;
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
</style>
