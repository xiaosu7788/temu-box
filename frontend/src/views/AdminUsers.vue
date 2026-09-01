<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ArrowLeft, Refresh, Select, CloseBold } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { errorMessage, getAdminUsers, updateUserStatus } from '../api'
import type { User } from '../types'

const router = useRouter()
const users = ref<User[]>([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    users.value = await getAdminUsers()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function changeStatus(user: User, status: 'approve' | 'reject') {
  try {
    Object.assign(user, await updateUserStatus(user.id, status))
    ElMessage.success(status === 'approve' ? '用户已通过审核' : '用户已拒绝')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

function statusText(status: string) {
  return { pending: '待审核', approved: '已通过', rejected: '已拒绝' }[status] || status
}

function statusType(status: string) {
  return status === 'approved' ? 'success' : status === 'rejected' ? 'danger' : 'warning'
}

onMounted(load)
</script>

<template>
  <section class="section-band admin-subpage">
    <div class="section-heading">
      <div class="subpage-title"><el-button text :icon="ArrowLeft" @click="router.push('/admin')">后台管理</el-button><div><h2>用户审核</h2><p>普通用户通过审核后才可以登录</p></div></div>
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
    </div>
    <el-table v-loading="loading" :data="users" stripe>
      <el-table-column prop="username" label="用户名" min-width="150" />
      <el-table-column prop="display_name" label="显示名称" min-width="140" />
      <el-table-column prop="role" label="角色" width="100" />
      <el-table-column label="状态" width="110"><template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ statusText(scope.row.status) }}</el-tag></template></el-table-column>
      <el-table-column prop="created_at" label="注册时间" min-width="210" />
      <el-table-column label="操作" width="190" fixed="right">
        <template #default="scope">
          <el-button v-if="scope.row.role !== 'admin' && scope.row.status !== 'approved'" link type="success" :icon="Select" @click="changeStatus(scope.row, 'approve')">通过</el-button>
          <el-button v-if="scope.row.role !== 'admin' && scope.row.status !== 'rejected'" link type="danger" :icon="CloseBold" @click="changeStatus(scope.row, 'reject')">拒绝</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && !users.length" description="暂无用户申请记录" />
  </section>
</template>
