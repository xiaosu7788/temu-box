<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ArrowDown, Check, CloseBold, Delete, EditPen, MoreFilled, Refresh, Select, Shop } from '@element-plus/icons-vue'
import {
  bulkDeleteAdminUsers,
  bulkUpdateAdminUsers,
  deleteAdminUser,
  getAdminUsers,
  updateAdminUser,
  updateUserStatus,
  getCategories,
} from '../api'
import { confirmAction, notifyError, notifySuccess } from '../feedback'
import type { User } from '../types'

const users = ref<User[]>([])
const selectedUsers = ref<User[]>([])
const loading = ref(false)
const saving = ref(false)
const deleting = ref<number | null>(null)
const bulkSaving = ref(false)
const editing = ref<User | null>(null)
const editDialogVisible = ref(false)
const bulkDialogVisible = ref(false)
const MODULE_OPTIONS = [
  { key: 'orders', label: '订单计算' },
  { key: 'inventory', label: '库存管理' },
  { key: 'activities', label: '批量报名活动' },
  { key: 'tasks', label: '任务记录' },
  { key: 'store', label: '店铺管理' },
]
const STORE_MODULE_OPTIONS = [
  { key: 'goods', label: '商品查询' },
  { key: 'create', label: '创建商品' },
  { key: 'kefu', label: '客服自助' },
  { key: 'actions', label: '操作记录' },
]

const editForm = reactive({
  username: '',
  display_name: '',
  note: '',
  password: '',
  modules: [] as string[],
  store_modules: [] as string[],
  // 品类权限：空 = 全部品类
  categories: [] as string[],
})
const bulkForm = reactive({ setDisplayName: false, display_name: '', setNote: false, note: '' })
/** 可供分配的品类（管理员接口一次拿全量） */
const categoryOptions = ref<Array<{ code: string; name: string }>>([])
const selectedIds = computed(() => selectedUsers.value.filter((user) => user.role !== 'admin').map((user) => user.id))
const canBulkEdit = computed(() => bulkForm.setDisplayName || bulkForm.setNote)

// 店铺管理模块准入筛选：默认关闭，管理员逐个开通
const storeFilter = ref<'all' | 'granted' | 'revoked'>('all')
const storeSwitchSaving = ref<number | null>(null)
const filteredUsers = computed(() => {
  if (storeFilter.value === 'all') return users.value
  const wantGranted = storeFilter.value === 'granted'
  return users.value.filter((user) => (user.role === 'admin' ? true : !!user.store_access) === wantGranted)
})
const storeNotGrantedCount = computed(() => users.value.filter((user) => user.role !== 'admin' && !user.store_access).length)

async function load() {
  loading.value = true
  try {
    users.value = await getAdminUsers()
    const currentIds = new Set(users.value.map((user) => user.id))
    selectedUsers.value = selectedUsers.value.filter((user) => currentIds.has(user.id))
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
  }
}

function canSelect(row: User) {
  return row.role !== 'admin'
}

function onSelectionChange(rows: User[]) {
  selectedUsers.value = rows.filter((user) => user.role !== 'admin')
}

function formatLocalDateTime(value?: string | null) {
  if (!value) return '-'
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return value
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

async function changeStatus(user: User, action: 'approve' | 'enable' | 'disable') {
  try {
    Object.assign(user, await updateUserStatus(user.id, action))
    notifySuccess(action === 'disable' ? '用户已禁用' : '用户已启用')
  } catch (error) {
    notifyError(error)
  }
}

function statusText(status: User['status']) {
  return { pending: '待审核', approved: '已启用', rejected: '已禁用' }[status] || status
}

function statusType(status: User['status']) {
  return status === 'approved' ? 'success' : status === 'rejected' ? 'info' : 'warning'
}

function openEdit(user: User) {
  editing.value = user
  editForm.username = user.username
  editForm.display_name = user.display_name || ''
  editForm.note = user.note || ''
  editForm.password = ''
  // 老用户没配过 modules 时按默认（除店铺外全开）
  editForm.modules = Array.isArray(user.modules)
    ? [...user.modules]
    : user.store_access
      ? ['orders', 'inventory', 'activities', 'tasks', 'store']
      : ['orders', 'inventory', 'activities', 'tasks']
  editForm.categories = Array.isArray(user.categories) ? [...user.categories] : []
  editForm.store_modules = Array.isArray(user.store_modules) && user.store_modules.length
    ? [...user.store_modules]
    : ['goods', 'create', 'kefu', 'actions']
  editDialogVisible.value = true
}

/** 拉品类列表（/api/categories 已按当前用户过滤，管理员是全量） */
async function loadCategoryOptions() {
  try {
    const list = await getCategories()
    categoryOptions.value = (list || []).map((c) => ({ code: c.code, name: c.name || c.code }))
  } catch {
    categoryOptions.value = []
  }
}

async function saveUser() {
  if (!editing.value) return
  saving.value = true
  try {
    const targetId = editing.value.id
    const updated = await updateAdminUser(targetId, {
      username: editForm.username.trim(),
      display_name: editForm.display_name.trim(),
      modules: editForm.modules,
      store_modules: editForm.store_modules,
      categories: editForm.categories,
      note: editForm.note.trim(),
      password: editForm.password || undefined,
    })
    Object.assign(editing.value, updated)
    // 列表里的对象也要同步：openEdit 读的是 users 里的那一份，
    // 只更新 editing 的话，重新打开弹窗看到的还是旧值（品类权限勾选丢失就是这么来的）
    const idx = users.value.findIndex((u) => u.id === targetId)
    if (idx >= 0) users.value[idx] = { ...users.value[idx], ...updated }
    editDialogVisible.value = false
    editing.value = null
    notifySuccess('用户信息已更新')
  } catch (error) {
    notifyError(error)
  } finally {
    saving.value = false
  }
}

async function removeUser(user: User) {
  if (user.role === 'admin') return
  if (!await confirmAction(`删除用户 ${user.username}？用户将无法继续登录，历史任务记录会保留。`, '确认删除用户')) return
  deleting.value = user.id
  try {
    await deleteAdminUser(user.id)
    users.value = users.value.filter((item) => item.id !== user.id)
    selectedUsers.value = selectedUsers.value.filter((item) => item.id !== user.id)
    notifySuccess('用户已删除')
  } catch (error) {
    notifyError(error)
  } finally {
    deleting.value = null
  }
}


function handleRowCommand(command: string, user: User) {
  if (command === 'edit') openEdit(user)
  if (command === 'delete') void removeUser(user)
}

function openBulkEdit() {
  bulkForm.setDisplayName = false
  bulkForm.display_name = ''
  bulkForm.setNote = false
  bulkForm.note = ''
  bulkDialogVisible.value = true
}

async function saveBulkEdit() {
  if (!selectedIds.value.length || !canBulkEdit.value) return
  bulkSaving.value = true
  const payload: { user_ids: number[]; display_name?: string; note?: string } = { user_ids: selectedIds.value }
  if (bulkForm.setDisplayName) payload.display_name = bulkForm.display_name.trim()
  if (bulkForm.setNote) payload.note = bulkForm.note.trim()
  try {
    await bulkUpdateAdminUsers(payload)
    bulkDialogVisible.value = false
    selectedUsers.value = []
    await load()
    notifySuccess(`已批量编辑 ${payload.user_ids.length} 个用户`)
  } catch (error) {
    notifyError(error)
  } finally {
    bulkSaving.value = false
  }
}

async function bulkSetStatus(status: 'approved' | 'rejected') {
  if (!selectedIds.value.length) return
  const label = status === 'approved' ? '启用' : '禁用'
  if (!await confirmAction(`确定${label}选中的 ${selectedIds.value.length} 个用户吗？`, `批量${label}`)) return
  bulkSaving.value = true
  try {
    const result = await bulkUpdateAdminUsers({ user_ids: selectedIds.value, status })
    selectedUsers.value = []
    await load()
    notifySuccess(`已${label} ${result.updated} 个用户`)
  } catch (error) {
    notifyError(error)
  } finally {
    bulkSaving.value = false
  }
}

async function bulkDelete() {
  const ids = [...selectedIds.value]
  if (!ids.length || !await confirmAction(`确定删除选中的 ${ids.length} 个用户吗？历史任务记录会保留。`, '批量删除用户')) return
  bulkSaving.value = true
  try {
    const result = await bulkDeleteAdminUsers(ids)
    selectedUsers.value = []
    await load()
    notifySuccess(`已删除 ${result.deleted} 个用户`)
  } catch (error) {
    notifyError(error)
  } finally {
    bulkSaving.value = false
  }
}

function handleBulkCommand(command: string) {
  if (command === 'edit') openBulkEdit()
  if (command === 'enable') void bulkSetStatus('approved')
  if (command === 'disable') void bulkSetStatus('rejected')
  if (command === 'store-on') void bulkSetStoreAccess(true)
  if (command === 'store-off') void bulkSetStoreAccess(false)
  if (command === 'delete') void bulkDelete()
}

/** 行内开关：关闭时必须二次确认（会强制退出该用户的店铺会话） */
async function toggleStoreAccess(user: User, enabled: boolean) {
  if (user.role === 'admin') return
  if (!enabled && !await confirmAction(
    `关闭「${user.username}」的店铺管理模块？该用户当前的店铺会话会被强制退出登录，前端入口也会隐藏。`,
    '关闭店铺管理模块',
  )) {
    return
  }
  storeSwitchSaving.value = user.id
  try {
    Object.assign(user, await updateAdminUser(user.id, { store_access: enabled }))
    notifySuccess(enabled ? '已开通店铺管理模块' : '已关闭店铺管理模块')
  } catch (error) {
    notifyError(error)
    await load()
  } finally {
    storeSwitchSaving.value = null
  }
}

async function bulkSetStoreAccess(enabled: boolean) {
  if (!selectedIds.value.length) return
  const label = enabled ? '开通' : '关闭'
  const extra = enabled ? '' : '，并强制退出这些用户当前的店铺会话'
  if (!await confirmAction(
    `确定${label}选中的 ${selectedIds.value.length} 个用户的店铺管理模块${extra}吗？`,
    `批量${label}店铺管理模块`,
  )) return
  bulkSaving.value = true
  try {
    const result = await bulkUpdateAdminUsers({ user_ids: selectedIds.value, store_access: enabled })
    const revoked = result.store_sessions_revoked || 0
    selectedUsers.value = []
    await load()
    notifySuccess(`已${label} ${result.updated} 个用户${revoked ? `，强制退出 ${revoked} 个店铺会话` : ''}`)
  } catch (error) {
    notifyError(error)
  } finally {
    bulkSaving.value = false
  }
}

onMounted(() => {
  void load()
  void loadCategoryOptions()
})
</script>

<template>
  <section class="section-band admin-subpage admin-users-page">
    <div class="section-heading">
      <div class="subpage-title"><div><h2>用户管理</h2><p>审核并管理普通用户账号、显示名称与内部备注</p></div></div>
      <div class="user-toolbar">
        <el-tag v-if="selectedIds.length" type="primary" effect="plain">已选 {{ selectedIds.length }} 人</el-tag>
        <el-select v-model="storeFilter" class="store-filter" aria-label="按店铺管理开通状态筛选">
          <el-option label="店铺管理：全部" value="all" />
          <el-option label="已开通店铺管理" value="granted" />
          <el-option label="未开通店铺管理" value="revoked" />
        </el-select>
        <el-dropdown v-if="selectedIds.length" trigger="click" @command="handleBulkCommand">
          <el-button type="primary" :icon="MoreFilled" :loading="bulkSaving">
            批量操作<el-icon class="el-icon--right"><ArrowDown /></el-icon>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="edit" :icon="EditPen">批量编辑名称/备注</el-dropdown-item>
              <el-dropdown-item command="enable" :icon="Check">批量启用</el-dropdown-item>
              <el-dropdown-item command="disable" :icon="CloseBold">批量禁用</el-dropdown-item>
              <el-dropdown-item command="store-on" :icon="Shop" divided>批量开通店铺管理</el-dropdown-item>
              <el-dropdown-item command="store-off" :icon="Shop">批量关闭店铺管理</el-dropdown-item>
              <el-dropdown-item command="delete" :icon="Delete" divided>批量删除</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <el-alert class="users-hint" type="info" :closable="false" show-icon>
      勾选表头可全选普通用户；管理员账号不可批量编辑或删除。备注仅管理员可见，不会显示给用户。
      店铺管理模块默认关闭，需逐个开通（当前 {{ storeNotGrantedCount }} 个用户未开通）；关闭时会强制退出该用户已登录的店铺会话。
    </el-alert>

    <el-table
      v-loading="loading"
      :data="filteredUsers"
      row-key="id"
      stripe
      @selection-change="onSelectionChange"
    >
      <el-table-column type="selection" width="48" :selectable="canSelect" />
      <el-table-column prop="username" label="用户名" min-width="140" />
      <el-table-column prop="display_name" label="显示名称" min-width="135">
        <template #default="scope">{{ scope.row.display_name || '—' }}</template>
      </el-table-column>
      <el-table-column label="备注" min-width="160" show-overflow-tooltip>
        <template #default="scope">{{ scope.row.note || '—' }}</template>
      </el-table-column>
      <el-table-column prop="role" label="角色" width="88" />
      <el-table-column label="状态" width="100">
        <template #default="scope"><el-tag :type="statusType(scope.row.status)" size="small">{{ statusText(scope.row.status) }}</el-tag></template>
      </el-table-column>
      <el-table-column label="店铺管理" width="118">
        <template #default="scope">
          <el-switch
            v-if="scope.row.role !== 'admin'"
            :model-value="!!scope.row.store_access"
            :loading="storeSwitchSaving === scope.row.id"
            size="small"
            @update:model-value="(value: boolean | string | number) => toggleStoreAccess(scope.row, Boolean(value))"
          />
          <span v-else class="admin-row-hint">始终开通</span>
        </template>
      </el-table-column>
      <el-table-column label="注册时间" min-width="165">
        <template #default="scope"><span class="user-date">{{ formatLocalDateTime(scope.row.created_at) }}</span></template>
      </el-table-column>
      <el-table-column label="操作" width="190" fixed="right">
        <template #default="scope">
          <div class="row-actions">
            <template v-if="scope.row.role !== 'admin'">
              <el-button v-if="scope.row.status === 'pending'" size="small" type="success" :icon="Select" @click="changeStatus(scope.row, 'approve')">通过</el-button>
              <el-button v-if="scope.row.status !== 'rejected'" size="small" plain type="warning" :icon="CloseBold" @click="changeStatus(scope.row, 'disable')">禁用</el-button>
              <el-button v-else size="small" plain type="success" :icon="Check" @click="changeStatus(scope.row, 'enable')">启用</el-button>
              <el-dropdown trigger="click" @command="(command: string) => handleRowCommand(command, scope.row)">
                <el-button size="small" circle :icon="MoreFilled" aria-label="更多操作" />
                <template #dropdown>
                  <el-dropdown-menu>
                                      <el-dropdown-item command="edit" :icon="EditPen" divided>编辑资料</el-dropdown-item>
                    <el-dropdown-item command="delete" :icon="Delete" divided>删除用户</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </template>
          </div>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && !users.length" description="暂无用户" />

    <el-dialog v-model="editDialogVisible" title="编辑用户" width="min(520px, calc(100vw - 32px))" :close-on-click-modal="false" destroy-on-close>
      <el-form label-position="top" @submit.prevent="saveUser">
        <el-form-item label="用户名"><el-input v-model="editForm.username" maxlength="80" autocomplete="off" /></el-form-item>
        <el-form-item label="显示名称"><el-input v-model="editForm.display_name" maxlength="120" placeholder="请输入显示名称" /></el-form-item>
        <el-form-item label="管理员备注"><el-input v-model="editForm.note" type="textarea" :rows="3" maxlength="500" show-word-limit placeholder="仅管理员可见" /></el-form-item>
        <el-form-item label="重置密码"><el-input v-model="editForm.password" type="password" show-password maxlength="128" autocomplete="new-password" placeholder="不修改密码请留空" /></el-form-item>
        <el-form-item label="模块权限">
          <el-checkbox-group v-model="editForm.modules">
            <el-checkbox v-for="m in MODULE_OPTIONS" :key="m.key" :value="m.key">{{ m.label }}</el-checkbox>
          </el-checkbox-group>
          <p class="perm-hint">未勾选的模块，该用户登录后看不到入口，直接输地址也会被拦回首页</p>
        </el-form-item>
        <el-form-item v-if="editForm.modules.includes('store')" label="店铺管理内的模块">
          <el-checkbox-group v-model="editForm.store_modules">
            <el-checkbox v-for="m in STORE_MODULE_OPTIONS" :key="m.key" :value="m.key">{{ m.label }}</el-checkbox>
          </el-checkbox-group>
          <p class="perm-hint">只勾「商品查询」时，该用户只能看看商品，不能创建商品</p>
        </el-form-item>
        <el-form-item label="品类权限">
          <el-checkbox-group v-if="categoryOptions.length" v-model="editForm.categories">
            <el-checkbox v-for="c in categoryOptions" :key="c.code" :value="c.code">{{ c.name }}</el-checkbox>
          </el-checkbox-group>
          <span v-else class="perm-empty">还没有品类，请先在「品类管理」里新建</span>
          <p class="perm-hint">
            不勾 = 没有任何品类权限，订单计算/库存/报名活动都是空的
          </p>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="editDialogVisible = false">取消</el-button><el-button type="primary" :loading="saving" :disabled="editForm.username.trim().length < 3 || (!!editForm.password && editForm.password.length < 8)" @click="saveUser">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="bulkDialogVisible" title="批量编辑用户" width="min(520px, calc(100vw - 32px))" :close-on-click-modal="false" destroy-on-close>
      <p class="bulk-summary">将编辑 {{ selectedIds.length }} 个普通用户。勾选要批量修改的字段，未勾选的字段保持不变。</p>
      <el-form label-position="top">
        <el-form-item>
          <el-checkbox v-model="bulkForm.setDisplayName">批量设置显示名称</el-checkbox>
          <el-input v-model="bulkForm.display_name" :disabled="!bulkForm.setDisplayName" maxlength="120" placeholder="留空可清除所选用户的显示名称" />
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="bulkForm.setNote">批量设置备注</el-checkbox>
          <el-input v-model="bulkForm.note" type="textarea" :rows="3" :disabled="!bulkForm.setNote" maxlength="500" show-word-limit placeholder="留空可清除所选用户的备注" />
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="bulkDialogVisible = false">取消</el-button><el-button type="primary" :loading="bulkSaving" :disabled="!canBulkEdit" @click="saveBulkEdit">应用到所选用户</el-button></template>
    </el-dialog>


  </section>
</template>

<style scoped>
.perm-empty {
  font-size: 12px;
  color: #909399;
}

.credential-password {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
}

.acct-main {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.acct-sub {
  color: var(--muted, #6b7280);
  font-size: 12px;
}

.binding-error {
  color: var(--danger, #dc2626);
  font-size: 12px;
}

.credential-note {
  margin: 10px 0 0;
  color: var(--muted, #6b7280);
  font-size: 12px;
}

.user-toolbar,
.row-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.user-toolbar {
  flex-wrap: wrap;
  justify-content: flex-end;
}

.users-hint {
  margin: 12px 0;
}

.store-filter {
  width: 168px;
}

.user-date {
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.admin-row-hint {
  color: var(--text-muted);
  font-size: 12px;
}

.bulk-summary {
  margin: 0 0 14px;
  color: var(--text-muted);
  font-size: 13px;
}

@media (max-width: 760px) {
  .user-toolbar {
    justify-content: flex-start;
  }
}
.perm-hint {
  margin: 4px 0 0;
  color: var(--muted, #9ca3af);
  font-size: 12px;
}
</style>