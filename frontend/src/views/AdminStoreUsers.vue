<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import {
  deleteAdminStoreUserTemplate,
  getAdminStoreUsers,
  getAdminStoreUserTemplates,
  getAdminUserStoreAccounts,
  getAdminUserStoreBinding,
  revokeAdminStoreSession,
} from '../api'
import type { AdminStoreUserItem } from '../api'
import type { ProductFormTemplateItem } from '../api'
import { confirmAction, notifyError, notifySuccess } from '../feedback'

const items = ref<AdminStoreUserItem[]>([])
const loading = ref(false)

/* 汇总统计 */
const summary = computed(() => {
  const s = { today: 0, week: 0, total: 0, failed: 0, users: items.value.length, online: 0 }
  for (const u of items.value) {
    s.today += u.stats.today_ok
    s.week += u.stats.week_ok
    s.total += u.stats.total_ok
    s.failed += u.stats.failed
    if (u.store.status === 'active') s.online += 1
  }
  return s
})

async function load() {
  loading.value = true
  try {
    const r = await getAdminStoreUsers()
    items.value = r.items
  } catch (error) {
    notifyError(error)
    items.value = []
  } finally {
    loading.value = false
  }
}

/* 店铺信息弹窗（从系统「用户管理」移植） */
const bindingVisible = ref(false)
const bindingLoading = ref(false)
const binding = ref<Record<string, any> | null>(null)
const bindingUser = ref<AdminStoreUserItem | null>(null)
const savedAccounts = ref<Array<Record<string, any>>>([])
const activeMasked = ref('')
const showAccountPasswords = ref(false)
const accountsLoading = ref(false)

const BINDING_STATUS_TEXT: Record<string, string> = {
  active: '在线',
  logged_out: '未登录',
  invalid: '已失效',
  cooldown: '冷却中',
}

function formatLocalDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  if (!Number.isFinite(d.getTime())) return String(value)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

async function openBinding(row: AdminStoreUserItem) {
  bindingUser.value = row
  binding.value = null
  savedAccounts.value = []
  activeMasked.value = ''
  showAccountPasswords.value = false
  bindingVisible.value = true
  bindingLoading.value = true
  try {
    const [info, accounts] = await Promise.all([
      getAdminUserStoreBinding(row.user_id),
      getAdminUserStoreAccounts(row.user_id, false),
    ])
    binding.value = info as Record<string, any>
    savedAccounts.value = (accounts.items || []) as Array<Record<string, any>>
    activeMasked.value = (accounts as any).active_account_masked || ''
  } catch (error) {
    notifyError(error)
  } finally {
    bindingLoading.value = false
  }
}

async function toggleAccountPasswords() {
  const user = bindingUser.value
  if (!user) return
  accountsLoading.value = true
  try {
    const next = !showAccountPasswords.value
    const accounts = await getAdminUserStoreAccounts(user.user_id, next)
    savedAccounts.value = (accounts.items || []) as Array<Record<string, any>>
    activeMasked.value = (accounts as any).active_account_masked || ''
    showAccountPasswords.value = next
  } catch (error) {
    notifyError(error)
  } finally {
    accountsLoading.value = false
  }
}

/* 模板抽屉 */
const drawerVisible = ref(false)
const drawerUser = ref<AdminStoreUserItem | null>(null)
const templates = ref<ProductFormTemplateItem[]>([])
const templatesLoading = ref(false)

async function openTemplates(row: AdminStoreUserItem) {
  drawerUser.value = row
  drawerVisible.value = true
  templatesLoading.value = true
  try {
    const r = await getAdminStoreUserTemplates(row.user_id)
    templates.value = r.items
  } catch (error) {
    notifyError(error)
    templates.value = []
  } finally {
    templatesLoading.value = false
  }
}

async function removeTemplate(tpl: ProductFormTemplateItem) {
  const user = drawerUser.value
  if (!user) return
  const ok = await confirmAction(`删除「${tpl.name}」？该用户将无法再套用此模板。`, '删除模板')
  if (!ok) return
  try {
    await deleteAdminStoreUserTemplate(user.user_id, tpl.id)
    notifySuccess('已删除')
    await openTemplates(user)
    await load()
  } catch (error) {
    notifyError(error)
  }
}

async function revoke(row: AdminStoreUserItem) {
  const ok = await confirmAction(`强制「${row.username}」退出店铺？会释放占用的 Temu 账号。`, '强制退出')
  if (!ok) return
  try {
    await revokeAdminStoreSession(row.user_id)
    notifySuccess('已强制退出')
    await load()
  } catch (error) {
    notifyError(error)
  }
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  if (!Number.isFinite(d.getTime())) return value
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** 会话剩余小时数（已过期返回「已过期」） */
function remaining(expiresAt: string | null): string {
  if (!expiresAt) return '—'
  const ms = new Date(expiresAt).getTime() - Date.now()
  if (!Number.isFinite(ms)) return '—'
  if (ms <= 0) return '已过期'
  return `${Math.floor(ms / 3600000)} 小时`
}

function statusTag(status: string): { type: 'success' | 'warning' | 'info' | 'danger'; text: string } {
  if (status === 'active') return { type: 'success', text: '在线' }
  if (status === 'cooldown') return { type: 'warning', text: '冷却中' }
  if (status === 'invalid') return { type: 'danger', text: '已失效' }
  return { type: 'info', text: '未登录' }
}

onMounted(load)
</script>

<template>
  <section class="section-band admin-subpage">
    <div class="section-heading">
      <div class="subpage-title">
        <div>
          <h2>用户管理</h2>
          <p>开通店铺的用户、建品统计、表单模板与店铺会话</p>
        </div>
      </div>
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
    </div>

    <div class="cards">
      <div class="card">
        <span class="card__label">今日新建</span>
        <span class="card__value">{{ summary.today }}</span>
      </div>
      <div class="card">
        <span class="card__label">近 7 天</span>
        <span class="card__value">{{ summary.week }}</span>
      </div>
      <div class="card">
        <span class="card__label">累计成功</span>
        <span class="card__value">{{ summary.total }}</span>
      </div>
      <div class="card">
        <span class="card__label">累计失败</span>
        <span class="card__value card__value--warn">{{ summary.failed }}</span>
      </div>
      <div class="card">
        <span class="card__label">在线店铺</span>
        <span class="card__value">{{ summary.online }} / {{ summary.users }}</span>
      </div>
    </div>

    <el-table v-loading="loading" :data="items" row-key="user_id" stripe>
      <el-table-column label="用户" min-width="160">
        <template #default="scope">
          <div class="user">
            <span class="user__name">{{ scope.row.display_name || scope.row.username }}</span>
            <el-tag v-if="scope.row.is_admin" size="small" effect="plain" type="warning">管理员</el-tag>
          </div>
          <span class="user__sub">{{ scope.row.username }}</span>
        </template>
      </el-table-column>
      <el-table-column label="Temu 账号" min-width="170">
        <template #default="scope">
          <span class="mono">{{ scope.row.store.account_masked || '—' }}</span>
          <div v-if="scope.row.store.mall_name" class="store-sub">{{ scope.row.store.mall_name }}</div>
        </template>
      </el-table-column>
      <el-table-column label="会话" width="96">
        <template #default="scope">
          <el-tag :type="statusTag(scope.row.store.status).type" size="small" effect="plain">
            {{ statusTag(scope.row.store.status).text }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="登录时间" width="150">
        <template #default="scope">
          <span class="mono">{{ formatTime(scope.row.store.login_at) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="会话剩余" width="96">
        <template #default="scope">{{ remaining(scope.row.store.expires_at) }}</template>
      </el-table-column>
      <el-table-column label="今日" width="72" align="right">
        <template #default="scope">{{ scope.row.stats.today_ok }}</template>
      </el-table-column>
      <el-table-column label="近7天" width="80" align="right">
        <template #default="scope">{{ scope.row.stats.week_ok }}</template>
      </el-table-column>
      <el-table-column label="累计" width="80" align="right">
        <template #default="scope">{{ scope.row.stats.total_ok }}</template>
      </el-table-column>
      <el-table-column label="失败" width="72" align="right">
        <template #default="scope">
          <span :class="{ 'text-warn': scope.row.stats.failed > 0 }">{{ scope.row.stats.failed }}</span>
        </template>
      </el-table-column>
      <el-table-column label="模板" width="90" align="right">
        <template #default="scope">
          <el-button link type="primary" @click="openTemplates(scope.row)">
            {{ scope.row.template_count }} 个
          </el-button>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="170">
        <template #default="scope">
          <el-button link type="primary" size="small" @click="openBinding(scope.row)">店铺信息</el-button>
          <el-button
            v-if="scope.row.store.status === 'active'"
            link
            type="danger"
            size="small"
            @click="revoke(scope.row)"
          >强制退出</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!loading && !items.length" description="还没有开通店铺的用户" />

    <el-dialog v-model="bindingVisible" title="店铺绑定信息" width="min(600px, calc(100vw - 32px))">
      <el-skeleton v-if="bindingLoading" :rows="4" animated />
      <template v-else-if="binding">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="用户">
            {{ bindingUser?.display_name || bindingUser?.username }}
          </el-descriptions-item>
          <el-descriptions-item label="模块准入">
            <el-tag :type="bindingUser?.store_access ? 'success' : 'info'" size="small">
              {{ bindingUser?.store_access ? '已开通' : '未开通' }}
            </el-tag>
          </el-descriptions-item>
          <template v-if="binding.bound">
            <el-descriptions-item label="店铺账号">{{ binding.account_masked || '—' }}</el-descriptions-item>
            <el-descriptions-item label="店铺名称">{{ binding.mall_name || '—' }}</el-descriptions-item>
            <el-descriptions-item label="会话状态">
              <el-tag
                size="small"
                :type="binding.status === 'active' ? 'success' : binding.status === 'invalid' ? 'danger' : 'info'"
              >
                {{ BINDING_STATUS_TEXT[binding.status || ''] || binding.status }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="登录时间">
              {{ binding.login_at ? formatLocalDateTime(binding.login_at) : '—' }}
            </el-descriptions-item>
            <el-descriptions-item label="过期时间">
              {{ binding.expires_at ? formatLocalDateTime(binding.expires_at) : '—' }}
            </el-descriptions-item>
            <el-descriptions-item label="最近活跃">
              {{ binding.last_seen_at ? formatLocalDateTime(binding.last_seen_at) : '—' }}
            </el-descriptions-item>
            <el-descriptions-item label="已保存密码">
              <el-tag size="small" :type="binding.has_credential ? 'success' : 'info'">
                {{ binding.has_credential ? '是' : '否' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item v-if="binding.last_error" label="最近错误">
              <span class="text-warn">{{ binding.last_error }}</span>
            </el-descriptions-item>
          </template>
          <el-descriptions-item v-else label="绑定状态">
            <span class="text-muted">该用户尚未绑定店铺</span>
          </el-descriptions-item>
        </el-descriptions>

        <template v-if="savedAccounts.length">
          <el-divider>
            已保存的店铺（{{ savedAccounts.length }}）
            <el-button link type="primary" size="small" :loading="accountsLoading" @click="toggleAccountPasswords">
              {{ showAccountPasswords ? '隐藏密码' : '显示密码' }}
            </el-button>
          </el-divider>
          <el-table v-loading="accountsLoading" :data="savedAccounts" size="small">
            <el-table-column label="店铺" min-width="180">
              <template #default="row">
                <div class="acct-main">
                  <span>{{ row.row.label || row.row.mall_name || row.row.account_masked }}</span>
                  <el-tag v-if="row.row.account_masked === activeMasked" size="small" type="success">当前</el-tag>
                </div>
                <div class="store-sub">{{ row.row.account_masked }}</div>
              </template>
            </el-table-column>
            <el-table-column v-if="showAccountPasswords" label="密码" min-width="140">
              <template #default="row">
                <span v-if="row.row.password" class="mono">{{ row.row.password }}</span>
                <span v-else class="text-warn">{{ row.row.password_error || '未保存' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="保存时间" width="150">
              <template #default="row">{{ row.row.updated_at ? formatLocalDateTime(row.row.updated_at) : '—' }}</template>
            </el-table-column>
            <el-table-column label="最近使用" width="150">
              <template #default="row">{{ row.row.last_used_at ? formatLocalDateTime(row.row.last_used_at) : '—' }}</template>
            </el-table-column>
          </el-table>
        </template>
      </template>
      <template #footer>
        <el-button @click="bindingVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="drawerVisible" size="480px" :title="`${drawerUser?.display_name || drawerUser?.username || ''} 的表单模板`">
      <div v-loading="templatesLoading">
        <el-empty v-if="!templatesLoading && !templates.length" description="该用户还没有保存模板" :image-size="60" />
        <el-table v-else :data="templates" size="small" row-key="id">
          <el-table-column prop="name" label="名称" min-width="120" show-overflow-tooltip />
          <el-table-column prop="note" label="备注" min-width="120" show-overflow-tooltip>
            <template #default="scope">{{ scope.row.note || '—' }}</template>
          </el-table-column>
          <el-table-column label="属性" width="70" align="right">
            <template #default="scope">{{ scope.row.field_count }} 项</template>
          </el-table-column>
          <el-table-column label="操作" width="70">
            <template #default="scope">
              <el-button link type="danger" size="small" @click="removeTemplate(scope.row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-drawer>
  </section>
</template>

<style scoped>
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}

.card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 14px;
  background: var(--surface-subtle, #f8fafc);
  border-radius: 10px;
}

.card__label {
  color: var(--muted, #6b7280);
  font-size: 12px;
}

.card__value {
  font-size: 22px;
  font-weight: 600;
}

.card__value--warn {
  color: var(--el-color-danger, #f56c6c);
}

.user {
  display: flex;
  align-items: center;
  gap: 6px;
}

.user__name {
  font-weight: 500;
}

.user__sub {
  color: var(--muted, #9ca3af);
  font-size: 12px;
}

.acct-main {
  display: flex;
  align-items: center;
  gap: 6px;
}

.store-sub {
  color: var(--muted, #9ca3af);
  font-size: 12px;
}

.mono {
  font-family: Consolas, Menlo, monospace;
  font-size: 12px;
}

.text-warn {
  color: var(--el-color-danger, #f56c6c);
}

.text-muted {
  color: var(--muted, #c0c4cc);
}
</style>
