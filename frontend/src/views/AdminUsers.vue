<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ArrowLeft, CloseBold, Delete, EditPen, Refresh, Select } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { deleteAdminUser, getAdminUsers, updateAdminUser, updateUserStatus } from '../api'
import { confirmAction, notifyError, notifySuccess } from '../feedback'
import type { User } from '../types'

const router = useRouter()
const users = ref<User[]>([])
const loading = ref(false)
const editing = ref<User | null>(null)
const editDialogVisible = ref(false)
const saving = ref(false)
const deleting = ref<number | null>(null)
const editForm = reactive({ username: '', password: '' })

async function load() {
  loading.value = true
  try {
    users.value = await getAdminUsers()
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
  }
}

async function changeStatus(user: User, status: 'approve' | 'reject') {
  try {
    Object.assign(user, await updateUserStatus(user.id, status))
    notifySuccess(status === 'approve' ? '用户已通过审核' : '用户已拒绝')
  } catch (error) {
    notifyError(error)
  }
}

function statusText(status: string) {
  return { pending: '待审核', approved: '已通过', rejected: '已拒绝' }[status] || status
}

function statusType(status: string) {
  return status === 'approved' ? 'success' : status === 'rejected' ? 'danger' : 'warning'
}

function openEdit(user: User) {
  editing.value = user
  editForm.username = user.username
  editForm.password = ''
  editDialogVisible.value = true
}

async function saveUser() {
  if (!editing.value) return
  saving.value = true
  try {
    Object.assign(editing.value, await updateAdminUser(editing.value.id, editForm.username.trim(), editForm.password))
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
    notifySuccess('用户已删除')
  } catch (error) {
    notifyError(error)
  } finally {
    deleting.value = null
  }
}

onMounted(load)
</script>

<template>
  <section class="section-band admin-subpage">
    <div class="section-heading">
      <div class="subpage-title"><el-button text :icon="ArrowLeft" @click="router.push('/admin')">后台管理</el-button><div><h2>用户管理</h2><p>审核并管理普通用户的登录账号</p></div></div>
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
    </div>
    <el-table v-loading="loading" :data="users" stripe>
      <el-table-column prop="username" label="用户名" min-width="150" />
      <el-table-column prop="display_name" label="显示名称" min-width="140" />
      <el-table-column prop="role" label="角色" width="100" />
      <el-table-column label="状态" width="110"><template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ statusText(scope.row.status) }}</el-tag></template></el-table-column>
      <el-table-column prop="created_at" label="注册时间" min-width="210" />
      <el-table-column label="操作" width="300" fixed="right">
        <template #default="scope">
          <el-button v-if="scope.row.role !== 'admin' && scope.row.status !== 'approved'" link type="success" :icon="Select" @click="changeStatus(scope.row, 'approve')">通过</el-button>
          <el-button v-if="scope.row.role !== 'admin' && scope.row.status !== 'rejected'" link type="danger" :icon="CloseBold" @click="changeStatus(scope.row, 'reject')">拒绝</el-button>
          <el-button v-if="scope.row.role !== 'admin'" link type="primary" :icon="EditPen" @click="openEdit(scope.row)">编辑</el-button>
          <el-button v-if="scope.row.role !== 'admin'" link type="danger" :icon="Delete" :loading="deleting === scope.row.id" @click="removeUser(scope.row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && !users.length" description="暂无用户" />
  </section>

  <el-dialog v-model="editDialogVisible" title="编辑用户" width="min(460px, calc(100vw - 32px))" :close-on-click-modal="false" @closed="editing = null">
    <el-form label-position="top" @submit.prevent="saveUser">
      <el-form-item label="用户名"><el-input v-model="editForm.username" maxlength="80" autocomplete="off" /></el-form-item>
      <el-form-item label="重置密码"><el-input v-model="editForm.password" type="password" show-password maxlength="128" autocomplete="new-password" placeholder="不修改密码请留空" /></el-form-item>
    </el-form>
    <template #footer><el-button @click="editDialogVisible = false">取消</el-button><el-button type="primary" :loading="saving" :disabled="editForm.username.trim().length < 3 || (!!editForm.password && editForm.password.length < 8)" @click="saveUser">保存</el-button></template>
  </el-dialog>
</template>
