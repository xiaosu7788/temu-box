<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { deleteAdminStoreLogs, getAdminStoreLogs } from '../api'
import { confirmAction, notifyError, notifySuccess } from '../feedback'
import type { StoreLoginLog } from '../types'

const items = ref<StoreLoginLog[]>([])
const loading = ref(false)
const action = ref('')
const limit = ref(100)
const keyword = ref('')
const range = ref<[string, string] | null>(null)
const picked = ref<StoreLoginLog[]>([])
const deleting = ref(false)

const actionOptions = [
  { label: '全部动作', value: '' },
  { label: '登录成功', value: 'login' },
  { label: '开始登录', value: 'login_start' },
  { label: '登录失败', value: 'login_failed' },
  { label: '退出登录', value: 'logout' },
  { label: '会话失效', value: 'session_invalid' },
  { label: '权限收回', value: 'access_revoked' },
  { label: '管理员强制退出', value: 'admin_revoked' },
]

const actionLabels: Record<string, string> = {
  login: '登录成功',
  login_start: '开始登录',
  login_failed: '登录失败',
  logout: '退出登录',
  session_invalid: '会话失效',
  access_revoked: '权限收回',
  admin_revoked: '管理员强制退出',
}

const limitOptions = [50, 100, 200, 500]

async function load() {
  loading.value = true
  try {
    items.value = await getAdminStoreLogs({
      limit: limit.value,
      action: action.value || undefined,
      keyword: keyword.value.trim() || undefined,
      start: range.value?.[0],
      end: range.value?.[1],
    })
  } catch (error) {
    notifyError(error)
    items.value = []
  } finally {
    loading.value = false
  }
}

function search() {
  void load()
}

function resetFilters() {
  keyword.value = ''
  range.value = null
  action.value = ''
  void load()
}

async function removePicked() {
  if (!picked.value.length) return
  const ok = await confirmAction(`删除选中的 ${picked.value.length} 条日志？不可恢复。`, '批量删除')
  if (!ok) return
  deleting.value = true
  try {
    await deleteAdminStoreLogs(picked.value.map((x) => x.id))
    notifySuccess(`已删除 ${picked.value.length} 条`)
    picked.value = []
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    deleting.value = false
  }
}

function actionLabel(value: string): string {
  return actionLabels[value] || value || '—'
}

function actionType(value: string): 'success' | 'danger' | 'warning' | 'info' {
  if (value === 'login') return 'success'
  if (value === 'login_failed' || value === 'session_invalid') return 'danger'
  if (value === 'access_revoked' || value === 'admin_revoked') return 'warning'
  return 'info'
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return value
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

onMounted(load)
</script>

<template>
  <section class="section-band admin-subpage">
    <div class="section-heading">
      <div class="subpage-title">
        <div>
          <h2>店铺登录日志</h2>
          <p>登录、退出、会话失效与 54001 冷却（用于排障与追责）</p>
        </div>
      </div>
      <div class="toolbar-row">
        <el-input
          v-model="keyword"
          class="kw-filter"
          placeholder="账号 / 消息 / IP"
          clearable
          @keyup.enter="search"
          @clear="search"
        />
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
        <el-select v-model="action" class="action-filter" @change="load">
          <el-option v-for="option in actionOptions" :key="option.value" :label="option.label" :value="option.value" />
        </el-select>
        <el-select v-model="limit" class="limit-filter" @change="load">
          <el-option v-for="value in limitOptions" :key="value" :label="`最近 ${value} 条`" :value="value" />
        </el-select>
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
        <el-button type="danger" plain :disabled="!picked.length" :loading="deleting" @click="removePicked">
          删除选中（{{ picked.length }}）
        </el-button>
      </div>
    </div>

    <el-table v-loading="loading" :data="items" row-key="id" stripe @selection-change="(v: StoreLoginLog[]) => (picked = v)">
      <el-table-column type="selection" width="44" />
      <el-table-column label="时间" width="168">
        <template #default="scope"><span class="mono">{{ formatTime(scope.row.created_at) }}</span></template>
      </el-table-column>
      <el-table-column label="用户" width="100">
        <template #default="scope">{{ scope.row.user_id ?? '—' }}</template>
      </el-table-column>
      <el-table-column label="Temu 账号" min-width="170">
        <template #default="scope">{{ scope.row.account_masked || '—' }}</template>
      </el-table-column>
      <el-table-column label="动作" width="130">
        <template #default="scope">
          <el-tag :type="actionType(scope.row.action)" size="small">{{ actionLabel(scope.row.action) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="代码" width="110">
        <template #default="scope"><span class="mono">{{ scope.row.code || '—' }}</span></template>
      </el-table-column>
      <el-table-column label="消息" min-width="240" show-overflow-tooltip>
        <template #default="scope">{{ scope.row.message || '—' }}</template>
      </el-table-column>
      <el-table-column label="IP" width="130">
        <template #default="scope"><span class="mono">{{ scope.row.ip || '—' }}</span></template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!loading && !items.length" description="暂无登录日志" />
  </section>
</template>

<style scoped>
.toolbar-row {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.kw-filter {
  width: 180px;
}

.time-filter {
  width: 340px;
}

.action-filter {
  width: 150px;
}

.limit-filter {
  width: 120px;
}

.mono {
  font-family: Consolas, Menlo, monospace;
  font-size: 12px;
}
</style>
