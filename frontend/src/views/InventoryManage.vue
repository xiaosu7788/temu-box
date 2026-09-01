<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Refresh, Search, Upload } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import type { UploadFile } from 'element-plus'
import { errorMessage, getInventory, getInventoryItems, rebuildInventory, uploadInventory } from '../api'
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
    ElMessage.error(errorMessage(error))
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
    ElMessage.success('库存表已更新')
    await Promise.all([refresh(), loadItems()])
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
    await Promise.all([refresh(), loadItems()])
  } catch (error) {
    ElMessage.error(errorMessage(error))
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

onMounted(() => {
  Promise.all([refresh(), loadItems()]).catch((error) => ElMessage.error(errorMessage(error)))
})
</script>

<template>
  <section class="inventory-module inventory-module--fixed">
    <el-tabs v-model="activeTab" class="inventory-tabs inventory-tabs--fixed" @tab-change="changeTab">
      <el-tab-pane name="inventory" label="库存数据">
        <section class="section-band inventory-overview">
          <div class="section-heading">
            <div><h2>当前库存</h2><p>服务器当前库存数据</p></div>
            <div class="inventory-actions">
              <el-button :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button>
              <el-upload :show-file-list="false" :auto-upload="false" accept=".xlsx,.xlsm" @change="handleInventoryFile">
                <el-button type="primary" :icon="Upload" :loading="loading">更新库存</el-button>
              </el-upload>
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

        <section class="section-band inventory-data-panel">
          <div class="section-heading">
            <div><h2>库存明细</h2><p>共 {{ total }} 个 SKU，输入 SKU 可查询价格和类型</p></div>
          </div>
          <div class="toolbar-row inventory-search-row">
            <el-input v-model="query" clearable placeholder="输入 SKU 查询" :prefix-icon="Search" @keyup.enter="searchItems" @clear="clearSearch" />
            <el-button type="primary" :icon="Search" :loading="itemsLoading" @click="searchItems">查询</el-button>
            <el-button :disabled="!query" @click="clearSearch">显示全部</el-button>
          </div>
          <div class="inventory-table-scroll">
            <el-table v-loading="itemsLoading" :data="items" stripe>
              <el-table-column prop="sku" label="SKU" min-width="180" />
              <el-table-column prop="price" label="价格" width="120">
                <template #default="scope">{{ scope.row.price?.toFixed(2) || '-' }}</template>
              </el-table-column>
              <el-table-column prop="set_type" label="类型" width="120" />
              <el-table-column prop="source_sheet" label="来源工作表" min-width="150" />
              <el-table-column prop="source_row" label="行号" width="80" />
              <el-table-column prop="source_column" label="价格列" width="90" />
            </el-table>
          </div>
          <el-pagination
            v-if="total > pageSize"
            v-model:current-page="page"
            class="pagination inventory-pagination"
            layout="prev, pager, next"
            :page-size="pageSize"
            :total="total"
            @current-change="loadItems"
          />
        </section>
      </el-tab-pane>
      <el-tab-pane name="half-headcost" label="头程减半名单">
        <HalfHeadcost />
      </el-tab-pane>
    </el-tabs>
  </section>
</template>
