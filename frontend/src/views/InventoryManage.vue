<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Filter, Refresh, Search } from '@element-plus/icons-vue'
import { getCategories, getInventory, getInventoryCategories, getInventoryItems, getMe } from '../api'
import type { InventoryItemFilters } from '../api'
import { notifyError } from '../feedback'
import type { CategorySummary, InventoryCategory, InventoryStatus, SkuResult } from '../types'
import CategoryPicker from '../components/CategoryPicker.vue'
import { selectedCategoryCode } from '../regionState'
import HalfHeadcost from './HalfHeadcost.vue'
import InventoryTopbar from '../components/InventoryTopbar.vue'

const route = useRoute()
const router = useRouter()

type InventoryTab = 'inventory' | 'half-headcost'

function normalizeTab(value: unknown): InventoryTab {
  return value === 'half-headcost' ? value : 'inventory'
}

const activeTab = ref<InventoryTab>(normalizeTab(route.query.tab))
const status = ref<InventoryStatus | null>(null)
const items = ref<SkuResult[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 30
const loading = ref(false)
const topbarReady = ref(false)
const itemsLoading = ref(false)
const isAdmin = ref(false)

const businessCategories = ref<CategorySummary[]>([])
const inventoryCategories = ref<InventoryCategory[]>([])
const inventoryCategory = ref('A')

// query 为唯一搜索框：同时匹配 SKU 与类型
const filters = ref<InventoryItemFilters>({ query: '' })
const advancedVisible = ref(false)
const advancedForm = ref({
  sourceSheet: '',
  priceMin: null as number | null,
  priceMax: null as number | null,
  rowMin: null as number | null,
  rowMax: null as number | null,
})
const knownSheets = ref<string[]>([])

const activeFilterCount = computed(() => {
  const f = filters.value
  return [f.sourceSheet, f.priceMin, f.priceMax, f.rowMin, f.rowMax]
    .filter((v) => v !== '' && v !== null && v !== undefined).length
})

async function loadBusinessCategories() {
  try {
    businessCategories.value = await getCategories()
    const selected = businessCategories.value.find((item) => item.code === selectedCategoryCode.value)
    inventoryCategory.value = selected?.inventory_category || 'A'
  } catch {
    inventoryCategory.value = 'A'
  }
}

async function loadInventoryCategories() {
  try {
    inventoryCategories.value = await getInventoryCategories()
    if (!inventoryCategories.value.some((c) => c.key === inventoryCategory.value)) {
      inventoryCategory.value = inventoryCategories.value[0]?.key || 'A'
    }
  } catch {
    inventoryCategories.value = [{ key: 'A', label: 'A类目' }]
  }
}

async function syncInventoryCategory() {
  if (!businessCategories.value.length) await loadBusinessCategories()
  const selected = businessCategories.value.find((item) => item.code === selectedCategoryCode.value)
  inventoryCategory.value = selected?.inventory_category || 'A'
  page.value = 1
  await Promise.all([refresh(), loadItems()])
}

watch(selectedCategoryCode, () => {
  void syncInventoryCategory()
})

function formatSize(size: number | null) {
  if (size === null) return '-'
  return size > 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${(size / 1024).toFixed(0)} KB`
}

async function refresh() {
  try {
    status.value = await getInventory(inventoryCategory.value)
  } catch (error) {
    notifyError(error)
  }
}

async function loadItems() {
  itemsLoading.value = true
  try {
    const data = await getInventoryItems(filters.value, page.value, pageSize, inventoryCategory.value)
    items.value = data.items
    total.value = data.total
    const sheets = data.items.map((i) => i.source_sheet).filter((s): s is string => Boolean(s))
    knownSheets.value = [...new Set([...knownSheets.value, ...sheets])].sort()
  } catch (error) {
    notifyError(error)
  } finally {
    itemsLoading.value = false
  }
}

function searchItems() {
  page.value = 1
  void loadItems()
}

function clearSearch() {
  filters.value = { query: '' }
  advancedForm.value = { sourceSheet: '', priceMin: null, priceMax: null, rowMin: null, rowMax: null }
  searchItems()
}

function applyAdvanced() {
  filters.value = {
    query: filters.value.query,
    sourceSheet: advancedForm.value.sourceSheet.trim(),
    priceMin: advancedForm.value.priceMin,
    priceMax: advancedForm.value.priceMax,
    rowMin: advancedForm.value.rowMin,
    rowMax: advancedForm.value.rowMax,
  }
  searchItems()
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
  // 挂载完成后顶栏目标元素才确定存在于文档中，此时再启用 Teleport
  topbarReady.value = true
  await Promise.all([loadBusinessCategories(), loadInventoryCategories()])
  try {
    const user = await getMe()
    isAdmin.value = user.role === 'admin'
  } catch (error) {
    notifyError(error)
  }
  await Promise.all([refresh(), loadItems()])
})
</script>

<template>
  <section class="inventory-module inventory-module--fixed">
    <el-tabs v-model="activeTab" class="inventory-tabs inventory-tabs--fixed" @tab-change="changeTab">
      <el-tab-pane name="inventory" label="库存数据">
        <!-- topbarReady 在 onMounted 后置真：冷启动时组件树先构建、再整体插入文档，
             Teleport 只能查询已在文档中的元素，故需等挂载完成后再 Teleport。 -->
        <Teleport v-if="topbarReady" to="#inventory-topbar-target">
          <InventoryTopbar
            subtitle="查看库存数据和 SKU 价格"
            :summary="[
              { label: 'SKU', value: status?.sku_count || 0 },
              { label: '文件', value: status?.exists ? '正常' : '缺失' },
              { label: '大小', value: formatSize(status?.size ?? null) },
              { label: '更新', value: status?.modified_at || '-' },
            ]"
          >
            <CategoryPicker v-model="selectedCategoryCode" />
            <el-button :icon="Refresh" :loading="loading" @click="refresh">刷新</el-button>
          </InventoryTopbar>
        </Teleport>

        <section class="section-band inventory-data-panel">
          <div class="section-heading">
            <div>
              <h2>库存明细</h2>
              <p>共 {{ total }} 个 SKU，可按 SKU、类型、价格等条件查询</p>
            </div>
            <div class="inventory-actions">
              <el-button :icon="Filter" :type="activeFilterCount ? 'primary' : 'default'" @click="advancedVisible = !advancedVisible">
                高级搜索<span v-if="activeFilterCount"> ({{ activeFilterCount }})</span>
              </el-button>
            </div>
          </div>

          <div v-show="advancedVisible" class="inventory-advanced-panel">
            <div class="advanced-fields">
              <div class="advanced-field">
                <span>来源工作表</span>
                <el-select v-model="advancedForm.sourceSheet" clearable filterable allow-create default-first-option placeholder="选择或输入">
                  <el-option v-for="sheet in knownSheets" :key="sheet" :label="sheet" :value="sheet" />
                </el-select>
              </div>
              <div class="advanced-field">
                <span>价格区间</span>
                <div class="advanced-range">
                  <el-input-number v-model="advancedForm.priceMin" :min="0" :precision="2" :controls="false" placeholder="最低" />
                  <em>—</em>
                  <el-input-number v-model="advancedForm.priceMax" :min="0" :precision="2" :controls="false" placeholder="最高" />
                </div>
              </div>
              <div class="advanced-field">
                <span>行号区间</span>
                <div class="advanced-range">
                  <el-input-number v-model="advancedForm.rowMin" :min="1" :controls="false" placeholder="起始" />
                  <em>—</em>
                  <el-input-number v-model="advancedForm.rowMax" :min="1" :controls="false" placeholder="结束" />
                </div>
              </div>
            </div>
            <div class="advanced-actions">
              <el-button type="primary" :icon="Search" @click="applyAdvanced">应用筛选</el-button>
              <el-button @click="clearSearch">清空条件</el-button>
            </div>
          </div>

          <div class="toolbar-row inventory-search-row">
            <el-input
              v-model="filters.query"
              clearable
              placeholder="搜索 SKU / 类型，如 MB131-100 或 6件套"
              :prefix-icon="Search"
              @keyup.enter="searchItems"
              @clear="searchItems"
            />
            <el-button type="primary" :icon="Search" :loading="itemsLoading" @click="searchItems">查询</el-button>
            <el-button :disabled="!filters.query && !activeFilterCount" @click="clearSearch">显示全部</el-button>
          </div>

          <div class="inventory-table-scroll">
            <el-table v-loading="itemsLoading" :data="items" stripe>
              <el-table-column prop="sku" label="SKU" min-width="180" />
              <el-table-column label="价格" width="120"><template #default="scope">{{ scope.row.price?.toFixed(2) || '-' }}</template></el-table-column>
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
        <div v-if="!isAdmin" class="permission-hint">
          <el-alert type="info" :closable="false" title="头程减半名单为只读展示，如需导入、添加或修改请联系管理员" />
        </div>
        <HalfHeadcost />
      </el-tab-pane>
    </el-tabs>
  </section>
</template>

<style scoped>
.inventory-module {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.inventory-tabs :deep(.el-tabs__header) {
  margin-bottom: 0;
  border-bottom: 1px solid var(--el-border-color-light);
}

.inventory-tabs :deep(.el-tabs__nav-wrap) {
  overflow: visible;
}

.inventory-data-panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.inventory-data-panel .section-heading {
  align-items: center;
  padding-bottom: 12px;
  margin-bottom: 0;
}

.inventory-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.inventory-actions .el-upload {
  display: inline-flex;
}

/* 高级筛选：标签在上、控件在下，按钮独立成组靠右，避免挤压错位 */
.inventory-advanced-panel {
  flex: 0 0 auto;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px 24px;
  flex-wrap: wrap;
  margin-bottom: 14px;
  padding: 16px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.advanced-fields {
  display: flex;
  align-items: flex-end;
  gap: 20px;
  flex-wrap: wrap;
}

.advanced-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.advanced-field > span {
  font-size: 12px;
  line-height: 1;
  color: var(--el-text-color-regular);
}

.advanced-field :deep(.el-select) {
  width: 190px;
}

.advanced-range {
  display: flex;
  align-items: center;
  gap: 8px;
}

.advanced-range :deep(.el-input-number) {
  width: 116px;
}

.advanced-range em {
  font-style: normal;
  color: var(--el-text-color-placeholder);
}

.advanced-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}

.inventory-search-row {
  flex: 0 0 auto;
  margin-bottom: 12px;
}

.inventory-search-row .el-input {
  width: min(420px, 100%);
}

.inventory-table-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.inventory-table-scroll :deep(.el-table) {
  font-size: 13px;
}

.inventory-pagination {
  flex: 0 0 auto;
  display: flex;
  justify-content: flex-end;
  padding-top: 12px;
}
</style>
