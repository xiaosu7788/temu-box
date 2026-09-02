<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ArrowLeft, Delete, Edit, Plus, Refresh } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { createAdminRegion, deleteAdminRegion, getAdminRegion, getAdminRegions, saveAdminRegion } from '../api'
import { confirmAction, notifyError, notifySuccess } from '../feedback'
import type { RegionProfile, RegionSummary } from '../types'

const router = useRouter()
const regions = ref<RegionSummary[]>([])
const loading = ref(false)
const savingCode = ref('')
const createVisible = ref(false)
const editVisible = ref(false)
const editingProfile = ref<RegionProfile | null>(null)
const editForm = reactive({ code: '', name: '', currency: 'CNY', sort_order: 100 })
const createForm = reactive({ code: '', name: '', currency: 'CNY', copy_from: 'US', sort_order: 100 })

async function load() {
  loading.value = true
  try { regions.value = await getAdminRegions() } catch (error) { notifyError(error) } finally { loading.value = false }
}

async function updateMeta(region: RegionSummary, values: Partial<RegionSummary>) {
  savingCode.value = region.code
  try {
    const profile = await getAdminRegion(region.code)
    await saveAdminRegion(region.code, { ...profile, ...values })
    notifySuccess('区域设置已保存')
    await load()
  } catch (error) { notifyError(error) } finally { savingCode.value = '' }
}

async function openEdit(region: RegionSummary) {
  savingCode.value = region.code
  try {
    editingProfile.value = await getAdminRegion(region.code)
    Object.assign(editForm, {
      code: editingProfile.value.code,
      name: editingProfile.value.name,
      currency: editingProfile.value.currency,
      sort_order: editingProfile.value.sort_order,
    })
    editVisible.value = true
  } catch (error) {
    notifyError(error)
  } finally {
    savingCode.value = ''
  }
}

async function saveEdit() {
  if (!editingProfile.value) return
  savingCode.value = editingProfile.value.code
  try {
    await saveAdminRegion(editingProfile.value.code, {
      ...editingProfile.value,
      name: editForm.name.trim(),
      currency: editForm.currency.trim().toUpperCase(),
      sort_order: editForm.sort_order,
    })
    editVisible.value = false
    notifySuccess('区域信息已保存')
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    savingCode.value = ''
  }
}

async function create() {
  savingCode.value = 'new'
  try {
    await createAdminRegion({ ...createForm, code: createForm.code.toUpperCase() })
    createVisible.value = false
    Object.assign(createForm, { code: '', name: '', currency: 'CNY', copy_from: regions.value.find((item) => item.is_default)?.code || 'US', sort_order: 100 })
    notifySuccess('区域已创建并复制参数')
    await load()
  } catch (error) { notifyError(error) } finally { savingCode.value = '' }
}

async function remove(region: RegionSummary) {
  if (!await confirmAction(`确认删除区域“${region.name}”？`, '删除区域')) return
  try { await deleteAdminRegion(region.code); notifySuccess('区域已删除'); await load() } catch (error) { notifyError(error) }
}

function editParameters(region: RegionSummary) {
  router.push({ path: '/admin/settings', query: { region: region.code } })
}

onMounted(load)
</script>

<template>
  <div class="admin-page admin-regions-page">
    <section class="section-band">
      <div class="section-heading">
        <div class="subpage-title"><el-button text :icon="ArrowLeft" @click="router.push('/admin')">后台管理</el-button><div><h2>区域设置</h2><p>维护不同区域的成本、活动价格和计算规则</p></div></div>
        <div class="admin-settings-actions"><el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button><el-button type="primary" :icon="Plus" @click="createVisible = true">新增区域</el-button></div>
      </div>
      <el-table :data="regions" v-loading="loading" stripe>
        <el-table-column prop="name" label="区域" min-width="140" />
        <el-table-column prop="code" label="代码" width="100" />
        <el-table-column prop="currency" label="币种" width="90" />
        <el-table-column prop="sort_order" label="排序" width="80" />
        <el-table-column label="状态" width="110"><template #default="scope"><el-tag :type="scope.row.enabled ? 'success' : 'info'">{{ scope.row.enabled ? '启用' : '停用' }}</el-tag></template></el-table-column>
        <el-table-column label="默认区域" width="120"><template #default="scope"><el-tag v-if="scope.row.is_default" type="primary">默认</el-tag><el-button v-else link type="primary" :loading="savingCode === scope.row.code" @click="updateMeta(scope.row, { is_default: true, enabled: true })">设为默认</el-button></template></el-table-column>
        <el-table-column label="操作" min-width="350" align="right"><template #default="scope"><el-button link type="primary" :icon="Edit" :loading="savingCode === scope.row.code" @click="openEdit(scope.row)">编辑区域</el-button><el-button link type="primary" @click="editParameters(scope.row)">配置参数</el-button><el-button link :type="scope.row.enabled ? 'warning' : 'success'" :disabled="scope.row.is_default" :loading="savingCode === scope.row.code" @click="updateMeta(scope.row, { enabled: !scope.row.enabled })">{{ scope.row.enabled ? '停用' : '启用' }}</el-button><el-button link type="danger" :icon="Delete" :disabled="scope.row.is_default" @click="remove(scope.row)">删除</el-button></template></el-table-column>
      </el-table>
    </section>

    <el-dialog v-model="editVisible" title="编辑业务区域" width="min(520px, calc(100vw - 32px))" :close-on-click-modal="false">
      <el-form label-position="top">
        <el-form-item label="区域名称"><el-input v-model="editForm.name" maxlength="80" /></el-form-item>
        <div class="region-create-grid">
          <el-form-item label="区域代码"><el-input v-model="editForm.code" disabled /><div class="region-code-hint">区域代码用于关联历史任务，创建后不可修改</div></el-form-item>
          <el-form-item label="币种"><el-input v-model="editForm.currency" maxlength="3" /></el-form-item>
        </div>
        <el-form-item label="显示排序"><el-input-number v-model="editForm.sort_order" :min="-10000" :max="10000" controls-position="right" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="editVisible = false">取消</el-button><el-button type="primary" :loading="savingCode === editForm.code" :disabled="!editForm.name.trim() || editForm.currency.trim().length !== 3" @click="saveEdit">保存修改</el-button></template>
    </el-dialog>

    <el-dialog v-model="createVisible" title="新增业务区域" width="min(520px, calc(100vw - 32px))" :close-on-click-modal="false">
      <el-form label-position="top">
        <el-form-item label="区域名称"><el-input v-model="createForm.name" maxlength="80" placeholder="例如：欧洲区" /></el-form-item>
        <div class="region-create-grid"><el-form-item label="区域代码"><el-input v-model="createForm.code" maxlength="16" placeholder="例如：EU" /></el-form-item><el-form-item label="币种"><el-input v-model="createForm.currency" maxlength="3" /></el-form-item></div>
        <el-form-item label="复制现有配置"><el-select v-model="createForm.copy_from"><el-option v-for="region in regions" :key="region.code" :label="region.name" :value="region.code" /></el-select></el-form-item>
      </el-form>
      <template #footer><el-button @click="createVisible = false">取消</el-button><el-button type="primary" :loading="savingCode === 'new'" :disabled="!createForm.name.trim() || createForm.code.trim().length < 2" @click="create">创建区域</el-button></template>
    </el-dialog>
  </div>
</template>