<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ArrowDown, Check, CloseBold, Delete, EditPen, MoreFilled, Refresh, Select } from '@element-plus/icons-vue'
import {
  bulkDeleteAdminUsers,
  bulkUpdateAdminUsers,
  deleteAdminUser,
  getAdminUsers,
  updateAdminUser,
  updateUserStatus,
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
const editForm = reactive({ username: '', display_name: '', note: '', password: '' })
const bulkForm = reactive({ setDisplayName: false, display_name: '', setNote: false, note: '' })
const selectedIds = computed(() => selectedUsers.value.filter((user) => user.role !== 'admin').map((user) => user.id))
const canBulkEdit = computed(() => bulkForm.setDisplayName || bulkForm.setNote)

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
  editDialogVisible.value = true
}

async function saveUser() {
  if (!editing.value) return
  saving.value = true
  try {
    Object.assign(editing.value, await updateAdminUser(editing.value.id, {
      username: editForm.username.trim(),
      display_name: editForm.display_name.trim(),
      note: editForm.note.trim(),
      password: editForm.password || undefined,
    }))
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
  if (command === 'delete') void bulkDelete()
}

onMounted(load)
</script>

<template>
  <section class="section-band admin-subpage admin-users-page">
    <div class="section-heading">
      <div class="subpage-title"><div><h2>用户管理</h2><p>审核并管理普通用户账号、显示名称与内部备注</p></div></div>
      <div class="user-toolbar">
        <el-tag v-if="selectedIds.length" type="primary" effect="plain">已选 {{ selectedIds.length }} 人</el-tag>
        <el-dropdown v-if="selectedIds.length" trigger="click" @command="handleBulkCommand">
          <el-button type="primary" :icon="MoreFilled" :loading="bulkSaving">
            批量操作<el-icon class="el-icon--right"><ArrowDown /></el-icon>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="edit" :icon="EditPen">批量编辑名称/备注</el-dropdown-item>
              <el-dropdown-item command="enable" :icon="Check">批量启用</el-dropdown-item>
              <el-dropdown-item command="disable" :icon="CloseBold">批量禁用</el-dropdown-item>
              <el-dropdown-item command="delete" :icon="Delete" divided>批量删除</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <el-alert class="users-hint" type="info" :closable="false" show-icon>
      勾选表头可全选普通用户；管理员账号不可批量编辑或删除。备注仅管理员可见，不会显示给用户。
    </el-alert>

    <el-table
      v-loading="loading"
      :data="users"
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
      <el-table-column label="注册时间" min-width="165">
        <template #default="scope"><span class="user-date">{{ formatLocalDateTime(scope.row.created_at) }}</span></template>
      </el-table-column>
      <el-table-column label="操作" width="190" fixed="right">
        <template #default="scope">
          <div v-if="scope.row.role !== 'admin'" class="row-actions">
            <el-button v-if="scope.row.status === 'pending'" size="small" type="success" :icon="Select" @click="changeStatus(scope.row, 'approve')">通过</el-button>
            <el-button v-if="scope.row.status !== 'rejected'" size="small" plain type="warning" :icon="CloseBold" @click="changeStatus(scope.row, 'disable')">禁用</el-button>
            <el-button v-else size="small" plain type="success" :icon="Check" @click="changeStatus(scope.row, 'enable')">启用</el-button>
            <el-dropdown trigger="click" @command="(command: string) => handleRowCommand(command, scope.row)">
              <el-button size="small" circle :icon="MoreFilled" aria-label="更多操作" />
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="edit" :icon="EditPen">编辑资料</el-dropdown-item>
                  <el-dropdown-item command="delete" :icon="Delete" divided>删除用户</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
          <span v-else class="admin-row-hint">管理员</span>
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
</style>
