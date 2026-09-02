<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ArrowLeft, Delete, Edit, Plus, Refresh, Search, Upload } from '@element-plus/icons-vue'
import type { UploadFile } from 'element-plus'
import { useRouter } from 'vue-router'
import { createInventoryItem, deleteInventoryItem, getAdminInventory, getAdminInventoryItems, rebuildInventory, updateInventoryItem, uploadInventory } from '../api'
import { confirmAction, notifyError, notifySuccess } from '../feedback'
import type { InventoryStatus, SkuResult } from '../types'

const router = useRouter()
const status = ref<InventoryStatus | null>(null)
const items = ref<SkuResult[]>([])
const query = ref('')
const total = ref(0)
const page = ref(1)
const pageSize = 30
const loading = ref(false)
const itemsLoading = ref(false)
const deleting = ref<string | null>(null)
const editing = ref<string | null>(null)
const itemDialogVisible = ref(false)
const itemSaving = ref(false)
const itemForm = ref({ sku: '', price: null as number | null, set_type: '单品' })
const itemFormMode = ref<'create' | 'edit'>('create')

function openCreateDialog() {
  itemFormMode.value = 'create'
  itemForm.value = { sku: '', price: null, set_type: '单品' }
  itemDialogVisible.value = true
}

function openEditDialog(item: SkuResult) {
  itemFormMode.value = 'edit'
  editing.value = item.sku
  itemForm.value = { sku: item.sku, price: item.price ?? null, set_type: item.set_type || '单品' }
  itemDialogVisible.value = true
}

async function saveItem() {
  const payload = {
    sku: itemForm.value.sku.trim(),
    price: itemForm.value.price,
    set_type: itemForm.value.set_type.trim() || '单品',
  }
  if (!payload.sku) {
    notifyError('请输入 SKU')
    return
  }
  itemSaving.value = true
  try {
    if (itemFormMode.value === 'create') {
      await createInventoryItem(payload)
      notifySuccess('库存明细已添加')
    } else if (editing.value) {
      await updateInventoryItem(editing.value, payload)
      notifySuccess('库存明细已更新')
    }
    itemDialogVisible.value = false
    editing.value = null
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    itemSaving.value = false
  }
}

function formatSize(size: number | null) {
  if (size === null) return '-'
  return size > 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${(size / 1024).toFixed(0)} KB`
}

async function refreshStatus() {
  status.value = await getAdminInventory()
}

async function loadItems() {
  itemsLoading.value = true
  try {
    const data = await getAdminInventoryItems(query.value, page.value, pageSize)
    items.value = data.items
    total.value = data.total
  } catch (error) {
    notifyError(error)
  } finally {
    itemsLoading.value = false
  }
}

async function load() {
  try {
    await Promise.all([refreshStatus(), loadItems()])
  } catch (error) {
    notifyError(error)
  }
}

function searchItems() {
  page.value = 1
  void loadItems()
}

function clearSearch() {
  query.value = ''
  searchItems()
}

async function handleInventoryFile(file: UploadFile) {
  if (!file.raw) return
  loading.value = true
  try {
    await uploadInventory(file.raw)
    notifySuccess('库存表已更新')
    page.value = 1
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
  }
}

async function rebuild() {
  loading.value = true
  try {
    await rebuildInventory()
    notifySuccess('库存缓存已重建')
    page.value = 1
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
  }
}

async function remove(item: SkuResult) {
  try {
    if (!await confirmAction(`删除库存明细 ${item.sku}？删除后会从当前库存查询中隐藏。`, '确认删除')) return
    deleting.value = item.sku
    await deleteInventoryItem(item.sku)
    notifySuccess('库存明细已删除')
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    deleting.value = null
  }
}

onMounted(load)
</script>

<template>
  <div class="admin-page admin-inventory-page admin-inventory-page--fixed">
    <Teleport to="#inventory-topbar-target">
      <div class="admin-inventory-topbar">
        <el-button class="admin-inventory-back" text :icon="ArrowLeft" @click="router.push('/admin')">后台管理</el-button>
        <div class="admin-inventory-heading">
          <h1>库存管理</h1>
          <span>维护库存表和库存明细</span>
        </div>
        <div class="admin-inventory-summary">
          <span>SKU <strong>{{ status?.sku_count || 0 }}</strong></span>
          <span>文件 <strong>{{ status?.exists ? '正常' : '缺失' }}</strong></span>
          <span>大小 <strong>{{ formatSize(status?.size ?? null) }}</strong></span>
          <span>缓存 <strong>{{ status?.cache_valid ? '有效' : '待重建' }}</strong></span>
          <span>更新 <strong>{{ status?.modified_at || '-' }}</strong></span>
        </div>
        <div class="admin-inventory-topbar-actions">
          <div class="inventory-action-group inventory-action-group--source">
            <el-button :icon="Refresh" :loading="loading" title="刷新库存状态" @click="load">刷新</el-button>
            <el-upload :show-file-list="false" :auto-upload="false" accept=".xlsx,.xlsm" @change="handleInventoryFile"><el-button type="primary" :icon="Upload" :loading="loading">更新库存表</el-button></el-upload>
            <el-button :icon="Refresh" :loading="loading" title="重新读取库存数据" @click="rebuild">重建缓存</el-button>
          </div>
          <div class="inventory-action-divider" aria-hidden="true"></div>
          <el-button class="inventory-add-button" type="primary" :icon="Plus" @click="openCreateDialog">添加库存</el-button>
        </div>
      </div>
    </Teleport>

    <section class="section-band admin-inventory-data-panel">
      <div class="section-heading"><div><h2>库存明细</h2><p>共 {{ total }} 个 SKU，可单独添加、编辑或删除库存记录；重新上传 Excel 后以新表为准</p></div></div>
      <div class="toolbar-row inventory-search-row">
        <el-input v-model="query" clearable placeholder="输入 SKU 查询" :prefix-icon="Search" @keyup.enter="searchItems" @clear="clearSearch" />
        <el-button type="primary" :icon="Search" :loading="itemsLoading" @click="searchItems">查询</el-button>
        <el-button :disabled="!query" @click="clearSearch">显示全部</el-button>
      </div>
      <div class="inventory-table-scroll">
        <el-table v-loading="itemsLoading" :data="items" stripe>
          <el-table-column prop="sku" label="SKU" min-width="180" />
          <el-table-column label="价格" width="120"><template #default="scope">{{ scope.row.price?.toFixed(2) || '-' }}</template></el-table-column>
          <el-table-column prop="set_type" label="类型" width="120" />
          <el-table-column prop="source_sheet" label="来源工作表" min-width="150" />
          <el-table-column prop="source_row" label="行号" width="80" />
          <el-table-column prop="source_column" label="价格列" width="90" />
          <el-table-column label="操作" width="150" fixed="right"><template #default="scope"><el-button link type="primary" :icon="Edit" @click="openEditDialog(scope.row)">编辑</el-button><el-button link type="danger" :icon="Delete" :loading="deleting === scope.row.sku" @click="remove(scope.row)">删除</el-button></template></el-table-column>
        </el-table>
      </div>
      <el-pagination v-if="total > pageSize" v-model:current-page="page" class="pagination inventory-pagination" layout="prev, pager, next" :page-size="pageSize" :total="total" @current-change="loadItems" />
    </section>

    <el-dialog v-model="itemDialogVisible" :title="itemFormMode === 'create' ? '添加库存明细' : '编辑库存明细'" width="min(520px, 92vw)">
      <el-form label-position="top" @submit.prevent="saveItem">
        <el-form-item label="SKU" required><el-input v-model="itemForm.sku" maxlength="255" placeholder="请输入 SKU" /></el-form-item>
        <el-form-item label="价格"><el-input-number v-model="itemForm.price" :min="0" :precision="2" :step="0.01" controls-position="right" placeholder="可留空" /></el-form-item>
        <el-form-item label="类型" required><el-input v-model="itemForm.set_type" maxlength="64" placeholder="例如：单品、6件套" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="itemDialogVisible = false">取消</el-button><el-button type="primary" :loading="itemSaving" @click="saveItem">保存</el-button></template>
    </el-dialog>
  </div>
</template>