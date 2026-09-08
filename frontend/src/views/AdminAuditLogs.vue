<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Refresh, Search } from '@element-plus/icons-vue'
import { getAdminAuditLogs } from '../api'
import { notifyError } from '../feedback'
import type { AuditLogItem } from '../types'

const loading = ref(false)
const items = ref<AuditLogItem[]>([])
const actions = ref<string[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(30)
const actionFilter = ref('')
const keyword = ref('')

const ACTION_LABELS: Record<string, string> = {
  'auth.register': '用户注册',
  'auth.login': '登录成功',
  'auth.login_failed': '登录失败',
  'user.approve': '审核通过',
  'user.reject': '驳回用户',
  'user.update': '修改用户',
  'user.delete': '删除用户',
  'settings.update': '更新成本参数',
  'activity_rules.update': '更新SKC规则',
  'region.create': '新增区域',
  'region.update': '更新区域',
  'region.delete': '删除区域',
  'inventory.upload': '上传库存表',
  'inventory.rebuild': '重建库存缓存',
  'inventory.item_create': '新增库存明细',
  'inventory.item_update': '更新库存明细',
  'inventory.item_delete': '删除库存明细',
  'half_headcost.import': '导入减半名单',
  'half_headcost.delete': '删除减半SKU',
  'task.create': '提交订单计算',
  'task.delete': '删除订单任务',
  'task.admin_delete': '管理员删除任务',
  'activity.create': '提交报名活动',
  'activity.delete': '删除活动任务',
  'activity.admin_delete': '管理员删除活动',
  'system_settings.update': '更新系统设置',
  'maintenance.cleanup': '手动清理',
  'maintenance.purge': '清空历史数据',
}

function actionLabel(action: string) {
  return ACTION_LABELS[action] || action
}

function actionType(action: string) {
  if (action.endsWith('login_failed')) return 'danger'
  if (action.includes('delete')) return 'danger'
  if (action.includes('update') || action.includes('reject')) return 'warning'
  if (action.includes('create') || action.includes('approve') || action.includes('upload')) return 'success'
  return 'info'
}

async function load() {
  loading.value = true
  try {
    const data = await getAdminAuditLogs({
      page: page.value,
      page_size: pageSize.value,
      action: actionFilter.value || undefined,
      keyword: keyword.value.trim() || undefined,
    })
    items.value = data.items
    actions.value = data.actions
    total.value = data.total
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  load()
}

onMounted(load)
</script>

<template>
  <div class="admin-page admin-audit-page">
    <section class="section-band">
      <div class="section-heading">
        <div class="subpage-title"><div><h2>审计日志</h2><p>登录、配置变更与关键操作记录</p></div></div>
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </div>

      <div class="filter-bar">
        <el-select v-model="actionFilter" placeholder="全部操作" clearable style="width: 200px" @change="search">
          <el-option v-for="item in actions" :key="item" :label="actionLabel(item)" :value="item" />
        </el-select>
        <el-input v-model="keyword" placeholder="搜索用户名 / 对象 / 详情" clearable style="width: 260px" @keyup.enter="search" @clear="search">
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button type="primary" :icon="Search" @click="search">查询</el-button>
      </div>

      <el-table v-loading="loading" :data="items" stripe>
        <el-table-column prop="created_at" label="时间" width="200">
          <template #default="scope"><span class="mono">{{ scope.row.created_at?.replace('T', ' ').slice(0, 19) }}</span></template>
        </el-table-column>
        <el-table-column prop="actor_username" label="操作者" width="140" />
        <el-table-column label="操作" width="150">
          <template #default="scope"><el-tag :type="actionType(scope.row.action)">{{ actionLabel(scope.row.action) }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="target_id" label="对象" min-width="180" show-overflow-tooltip>
          <template #default="scope"><span class="mono">{{ scope.row.target_id || '-' }}</span></template>
        </el-table-column>
        <el-table-column prop="detail" label="详情" min-width="240" show-overflow-tooltip />
        <el-table-column prop="ip" label="IP" width="140">
          <template #default="scope"><span class="mono">{{ scope.row.ip || '-' }}</span></template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !items.length" description="暂无审计日志" />
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[30, 50, 100]"
        layout="total, sizes, prev, pager, next"
        class="pagination"
        @current-change="load"
        @size-change="search"
      />
    </section>
  </div>
</template>

<style scoped>
.pagination {
  display: flex;
  justify-content: flex-end;
}
</style>
