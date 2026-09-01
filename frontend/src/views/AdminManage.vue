<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Delete, Plus, Refresh, Select, CloseBold } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { errorMessage, getAdminSettings, getAdminUsers, saveAdminSettings, updateUserStatus } from '../api'
import type { AppSettings, User } from '../types'

const users = ref<User[]>([])
const loadingUsers = ref(false)
const saving = ref(false)
const settings = reactive<AppSettings>({
  order: { headcost: {}, operation_fee: 7, extra_item_fee: 2 },
  activity: { headcost: 5, operation_fee: 7, set_prices: {}, single_tiers: [] },
})
const orderTypes = ['单品', '4件套', '5件套', '6件套', '8件套', '10件套', '12件套']
const setTypes = ['4', '5', '6', '8', '10', '12']

async function load() {
  loadingUsers.value = true
  try {
    users.value = await getAdminUsers()
    Object.assign(settings, await getAdminSettings())
  } catch (error) { ElMessage.error(errorMessage(error)) } finally { loadingUsers.value = false }
}

async function changeStatus(user: User, status: 'approve' | 'reject') {
  try {
    const updated = await updateUserStatus(user.id, status)
    Object.assign(user, updated)
    ElMessage.success(status === 'approve' ? '用户已通过审核' : '用户已拒绝')
  } catch (error) { ElMessage.error(errorMessage(error)) }
}

async function save() {
  saving.value = true
  try { Object.assign(settings, await saveAdminSettings(settings)); ElMessage.success('成本参数已保存') }
  catch (error) { ElMessage.error(errorMessage(error)) }
  finally { saving.value = false }
}

function addTier() { settings.activity.single_tiers.push({ min_price: 0, profit: 0 }) }
function removeTier(index: number) { if (settings.activity.single_tiers.length > 1) settings.activity.single_tiers.splice(index, 1) }
function statusText(status: string) { return { pending: '待审核', approved: '已通过', rejected: '已拒绝' }[status] || status }
function statusType(status: string) { return status === 'approved' ? 'success' : status === 'rejected' ? 'danger' : 'warning' }

onMounted(load)
</script>

<template>
  <div class="admin-page">
    <section class="section-band">
      <div class="section-heading"><div><h2>用户审核</h2><p>普通用户通过审核后才可以登录</p></div><el-button :icon="Refresh" :loading="loadingUsers" @click="load">刷新</el-button></div>
      <el-table v-loading="loadingUsers" :data="users" stripe>
        <el-table-column prop="username" label="用户名" min-width="150" />
        <el-table-column prop="display_name" label="显示名称" min-width="140" />
        <el-table-column prop="role" label="角色" width="100" />
        <el-table-column label="状态" width="110"><template #default="scope"><el-tag :type="statusType(scope.row.status)">{{ statusText(scope.row.status) }}</el-tag></template></el-table-column>
        <el-table-column prop="created_at" label="注册时间" min-width="190" />
        <el-table-column label="操作" width="190" fixed="right"><template #default="scope"><el-button v-if="scope.row.role !== 'admin' && scope.row.status !== 'approved'" link type="success" :icon="Select" @click="changeStatus(scope.row, 'approve')">通过</el-button><el-button v-if="scope.row.role !== 'admin' && scope.row.status !== 'rejected'" link type="danger" :icon="CloseBold" @click="changeStatus(scope.row, 'reject')">拒绝</el-button></template></el-table-column>
      </el-table>
    </section>

    <section class="section-band settings-panel">
      <div class="section-heading"><div><h2>成本参数</h2><p>参数保存后，后续新任务立即生效</p></div><el-button type="primary" :loading="saving" @click="save">保存全部参数</el-button></div>
      <div class="settings-grid">
        <div class="settings-block"><h3>订单计算</h3><div class="settings-form-grid"><label v-for="type in orderTypes" :key="type">{{ type }}头程<el-input-number v-model="settings.order.headcost[type]" :min="0" :precision="2" controls-position="right" /></label><label>操作费<el-input-number v-model="settings.order.operation_fee" :min="0" :precision="2" controls-position="right" /></label><label>续件费<el-input-number v-model="settings.order.extra_item_fee" :min="0" :precision="2" controls-position="right" /></label></div></div>
        <div class="settings-block"><h3>批量报名活动</h3><div class="settings-form-grid"><label>单品头程<el-input-number v-model="settings.activity.headcost" :min="0" :precision="2" controls-position="right" /></label><label>操作费<el-input-number v-model="settings.activity.operation_fee" :min="0" :precision="2" controls-position="right" /></label><label v-for="type in setTypes" :key="type">{{ type }}件套活动价<el-input-number v-model="settings.activity.set_prices[type]" :min="0" :precision="2" controls-position="right" /></label></div><h3 class="subheading">单品货值利润条件</h3><div v-for="(tier, index) in settings.activity.single_tiers" :key="index" class="tier-row"><span>货值 ≥</span><el-input-number v-model="tier.min_price" :min="0" :precision="2" controls-position="right" /><span>利润 +</span><el-input-number v-model="tier.profit" :min="0" :precision="2" controls-position="right" /><el-button circle text type="danger" :icon="Delete" @click="removeTier(index)" /><span v-if="index === settings.activity.single_tiers.length - 1" class="tier-hint">按最高匹配条件计算</span></div><el-button class="add-tier" text type="primary" :icon="Plus" @click="addTier">新增条件</el-button></div>
      </div>
    </section>
  </div>
</template>
