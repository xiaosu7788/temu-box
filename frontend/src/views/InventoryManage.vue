<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Refresh, Search, Upload } from '@element-plus/icons-vue'
import type { UploadFile } from 'element-plus'
import { getInventory, getInventoryItems, getMe, rebuildInventory, uploadInventory } from '../api'
import { notifyError, notifySuccess } from '../feedback'
import type { InventoryStatus, SkuResult } from '../types'
import HalfHeadcost from './HalfHeadcost.vue'

const route = useRoute()
const router = useRouter()

type InventoryTab = 'inventory' | 'half-headcost'

function normalizeTab(value: unknown): InventoryTab {
  return value === 'half-headcost' ? value : 'inventory'
}

const activeTab = ref<InventoryTab>(normalizeTab(route.query.tab))
const status = ref<InventoryStatus | null>(null)
const items = ref<SkuResult[]>([])
const query = ref('')
const total = ref(0)
const page = ref(1)
const pageSize = 30
const loading = ref(false)
const itemsLoading = ref(false)
const isAdmin = ref(false)

function formatSize(size: number | null) {
  if (size === null) return '-'
  return size > 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${(size / 1024).toFixed(0)} KB`
}

async function refresh() {
  status.value = await getInventory()
}

async function loadItems() {
  itemsLoading.value = true
  try {
    const data = await getInventoryItems(query.value, page.value, pageSize)
    items.value = data.items
    total.value = data.total
  } catch (error) {
    notifyError(error)
  } finally {
    itemsLoading.value = false
  }
}

function searchItems() {
  page.value = 1
  loadItems()
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
    await Promise.all([refresh(), loadItems()])
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
    await Promise.all([refresh(), loadItems()])
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
  }
}

function changeTab(value: string | number | boolean) {
  const tab = normalizeTab(value)
  activeTab.value = tab
  router.replace({ query: tab === 'inventory' ? {} : { tab } })
}

watch(() => route.query.tab, (value) => {
  activeTab.value = normalizeTab(value)
})

onMounted(async () => {
  try {
    const user = await getMe()
    isAdmin.value = user.role === 'admin'
    await Promise.all([refresh(), loadItems()])
  } catch (error) { notifyError(error) }
})
</script>

<template>
  <section class="inventory-module inventory-module--fixed">
    <el-tabs v-model="activeTab" class="inventory-tabs inventory-tabs--fixed" @tab-change="changeTab">
      <el-tab-pane name="inventory" label="库存数据">
        <Teleport to="#inventory-topbar-target">
          <div class="inventory-topbar-content">
            <div class="inventory-topbar-copy">
              <div class="inventory-topbar-heading"><div class="inventory-title-line"><h1>库存管理</h1><span class="inventory-title-subtitle">查看库存数据和 SKU 价格</span></div></div>
              <div class="inventory-topbar-summary">
              <span>SKU <strong>{{ status?.sku_count || 0 }}</strong></span>
              <span>文件 <strong>{{ status?.exists ? '正常' : '缺失' }}</strong></span>
              <span>大小 <strong>{{ formatSize(status?.size ?? null) }}</strong></span>
                <span>更新 <strong>{{ status?.modified_at || '-' }}</strong></span>
              </div>
            </div>
            <div class="inventory-topbar-actions">
              <el-button :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button>
            </div>
          </div>
        </Teleport>
        <section class="section-band inventory-data-panel">
          <div class="section-heading"><div><h2>库存明细</h2><p>共 {{ total }} 个 SKU，输入 SKU 可查询价格和类型</p></div></div>
          <div class="toolbar-row inventory-search-row">
            <el-input v-model="query" clearable placeholder="输入 SKU 查询" :prefix-icon="Search" @keyup.enter="searchItems" @clear="clearSearch" />
            <el-button type="primary" :icon="Search" :loading="itemsLoading" @click="searchItems">查询</el-button>
            <el-button :disabled="!query" @click="clearSearch">显示全部</el-button>
          </div>
          <div class="inventory-table-scroll">
            <el-table v-loading="itemsLoading" :data="items" stripe>
              <el-table-column prop="sku" label="SKU" min-width="180" />
              <el-table-column prop="price" label="价格" width="120"><template #default="scope">{{ scope.row.price?.toFixed(2) || '-' }}</template></el-table-column>
              <el-table-column prop="set_type" label="类型" width="120" />
              <el-table-column prop="source_sheet" label="来源工作表" min-width="150" />
              <el-table-column prop="source_row" label="行号" width="80" />
              <el-table-column prop="source_column" label="价格列" width="90" />
            </el-table>
          </div>
          <el-pagination v-if="total > pageSize" v-model:current-page="page" class="pagination inventory-pagination" layout="prev, pager, next" :page-size="pageSize" :total="total" @current-change="loadItems" />
        </section>
      </el-tab-pane>
      <el-tab-pane name="half-headcost" label="头程减半名单">
        <HalfHeadcost />
      </el-tab-pane>
    </el-tabs>
  </section>
</template>
