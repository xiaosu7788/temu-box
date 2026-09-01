<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ArrowLeft, Delete, Refresh, Search, Upload } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile } from 'element-plus'
import { useRouter } from 'vue-router'
import { deleteInventoryItem, errorMessage, getAdminInventory, getAdminInventoryItems, rebuildInventory, uploadInventory } from '../api'
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
    ElMessage.error(errorMessage(error))
  } finally {
    itemsLoading.value = false
  }
}

async function load() {
  try {
    await Promise.all([refreshStatus(), loadItems()])
  } catch (error) {
    ElMessage.error(errorMessage(error))
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
    ElMessage.success('库存表已更新')
    page.value = 1
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function rebuild() {
  loading.value = true
  try {
    await rebuildInventory()
    ElMessage.success('库存缓存已重建')
    page.value = 1
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function remove(item: SkuResult) {
  try {
    await ElMessageBox.confirm(`删除库存明细 ${item.sku}？删除后会从当前库存查询中隐藏。`, '确认删除', { type: 'warning' })
    deleting.value = item.sku
    await deleteInventoryItem(item.sku)
    ElMessage.success('库存明细已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(errorMessage(error))
  } finally {
    deleting.value = null
  }
}

onMounted(load)
</script>

<template>
  <div class="admin-page admin-inventory-page">
    <section class="section-band">
      <div class="section-heading">
        <div class="subpage-title"><el-button text :icon="ArrowLeft" @click="router.push('/admin')">后台管理</el-button><div><h2>库存管理</h2><p>管理员可以更新库存表、重建缓存和维护库存明细</p></div></div>
        <div class="admin-settings-actions">
          <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
          <el-upload :show-file-list="false" :auto-upload="false" accept=".xlsx,.xlsm" @change="handleInventoryFile"><el-button type="primary" :icon="Upload" :loading="loading">更新库存表</el-button></el-upload>
          <el-button :icon="Refresh" :loading="loading" @click="rebuild">重建缓存</el-button>
        </div>
      </div>
      <div class="metric-strip inventory-metrics">
        <div><span>文件状态</span><strong>{{ status?.exists ? '正常' : '缺失' }}</strong></div>
        <div><span>文件大小</span><strong>{{ formatSize(status?.size ?? null) }}</strong></div>
        <div><span>SKU 数量</span><strong>{{ status?.sku_count || 0 }}</strong></div>
        <div><span>缓存状态</span><strong>{{ status?.cache_valid ? '有效' : '待重建' }}</strong></div>
        <div><span>更新时间</span><strong class="small-value">{{ status?.modified_at || '-' }}</strong></div>
      </div>
    </section>

    <section class="section-band admin-inventory-table">
      <div class="section-heading"><div><h2>库存明细</h2><p>共 {{ total }} 个 SKU，可单独删除不需要的库存记录</p></div></div>
      <div class="toolbar-row inventory-search-row">
        <el-input v-model="query" clearable placeholder="输入 SKU 查询" :prefix-icon="Search" @keyup.enter="searchItems" @clear="clearSearch" />
        <el-button type="primary" :icon="Search" :loading="itemsLoading" @click="searchItems">查询</el-button>
        <el-button :disabled="!query" @click="clearSearch">显示全部</el-button>
      </div>
      <el-table v-loading="itemsLoading" :data="items" stripe>
        <el-table-column prop="sku" label="SKU" min-width="180" />
        <el-table-column label="价格" width="120"><template #default="scope">{{ scope.row.price?.toFixed(2) || '-' }}</template></el-table-column>
        <el-table-column prop="set_type" label="类型" width="120" />
        <el-table-column prop="source_sheet" label="来源工作表" min-width="150" />
        <el-table-column prop="source_row" label="行号" width="80" />
        <el-table-column prop="source_column" label="价格列" width="90" />
        <el-table-column label="操作" width="110" fixed="right"><template #default="scope"><el-button link type="danger" :icon="Delete" :loading="deleting === scope.row.sku" @click="remove(scope.row)">删除</el-button></template></el-table-column>
      </el-table>
      <el-pagination v-if="total > pageSize" v-model:current-page="page" class="pagination" layout="prev, pager, next" :page-size="pageSize" :total="total" @current-change="loadItems" />
    </section>
  </div>
</template>
