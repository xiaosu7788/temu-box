<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Delete, Edit, Filter, More, Plus, Refresh, Search, Upload } from '@element-plus/icons-vue'
import type { UploadFile } from 'element-plus'
import { applyPendingInventory, createInventoryCategory, createInventoryItem, deleteInventoryItem, discardPendingInventory, getAdminInventory, getAdminInventoryItems, getInventoryCategories, getPendingInventory, previewInventory, rebuildInventory, updateInventoryCategory, updateInventoryItem } from '../api'
import type { InventoryItemFilters } from '../api'
import { confirmAction, notifyError, notifySuccess } from '../feedback'
import type { InventoryCategory, InventoryDiffItem, InventoryPreview, InventoryStatus, SkuResult } from '../types'
import AdminHalfHeadcost from './AdminHalfHeadcost.vue'
import InventoryTopbar from '../components/InventoryTopbar.vue'
type InventoryTab = 'inventory' | 'half-headcost'

const activeTab = ref<InventoryTab>('inventory')
const halfHeadcostRef = ref<{ reload: () => Promise<void> } | null>(null)
const topbarReady = ref(false)
const status = ref<InventoryStatus | null>(null)
const inventorySummary = computed(() => [
  { label: 'SKU', value: status.value?.sku_count || 0 },
  { label: '文件', value: status.value?.exists ? '正常' : '缺失' },
  { label: '大小', value: formatSize(status.value?.size ?? null) },
  { label: '缓存', value: status.value?.cache_valid ? '有效' : '待重建' },
  { label: '更新', value: status.value?.modified_at || '-' },
])
const items = ref<SkuResult[]>([])
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

const inventoryCategories = ref<InventoryCategory[]>([{ key: 'A', label: 'A类目' }])
const inventoryCategory = ref('A')

const categoryDialogVisible = ref(false)
const categorySaving = ref(false)
const categoryFormMode = ref<'create' | 'edit'>('create')
const editingCategory = ref<InventoryCategory | null>(null)
const categoryForm = ref({ key: '', label: '', code_pattern: '', price_max: 100 })

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


function openInventoryCategoryDialog() {
  categoryFormMode.value = 'create'
  editingCategory.value = null
  categoryForm.value = { key: '', label: '', code_pattern: '', price_max: 100 }
  categoryDialogVisible.value = true
}

function openEditCategoryDialog(cat: InventoryCategory) {
  categoryFormMode.value = 'edit'
  editingCategory.value = cat
  categoryForm.value = {
    key: cat.key,
    label: cat.label,
    code_pattern: cat.code_pattern ?? '',
    price_max: cat.price_max ?? 100,
  }
  categoryDialogVisible.value = true
}

async function saveInventoryCategory() {
  const label = categoryForm.value.label.trim()
  if (!label) {
    notifyError(new Error('请输入库存类目名称'))
    return
  }
  categorySaving.value = true
  try {
    if (categoryFormMode.value === 'edit' && editingCategory.value) {
      const key = editingCategory.value.key
      const updated = await updateInventoryCategory(key, {
        label,
        code_pattern: categoryForm.value.code_pattern.trim() || null,
        price_max: categoryForm.value.price_max,
        enabled: editingCategory.value.enabled ?? true,
        sort_order: editingCategory.value.sort_order ?? 100,
      })
      inventoryCategories.value = inventoryCategories.value.map((item) => (item.key === key ? updated : item))
      notifySuccess('库存类目已更新')
    } else {
      const key = categoryForm.value.key.trim().toUpperCase()
      if (!key) {
        notifyError(new Error('请输入库存类目代码'))
        return
      }
      const created = await createInventoryCategory({
        key,
        label,
        code_pattern: categoryForm.value.code_pattern.trim() || null,
        price_max: categoryForm.value.price_max,
      })
      inventoryCategories.value = [...inventoryCategories.value, created]
      inventoryCategory.value = created.key
      notifySuccess('库存类目已创建')
    }
    categoryDialogVisible.value = false
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    categorySaving.value = false
  }
}

async function loadCategories() {
  try {
    const items = await getInventoryCategories()
    if (items.length) inventoryCategories.value = items
  } catch {
    // 回退为仅 A 类目
  }
}

function categoryLabel(key: string) {
  return inventoryCategories.value.find((item) => item.key === key)?.label ?? key
}

function switchCategory(key: string) {
  inventoryCategory.value = key
  page.value = 1
  void load()
}

/** 顶栏刷新按钮：按当前标签刷新对应内容 */
function refreshActiveTab() {
  if (activeTab.value === 'half-headcost') {
    void halfHeadcostRef.value?.reload()
    return
  }
  void load()
}

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
    inventory_category: inventoryCategory.value,
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
  status.value = await getAdminInventory(inventoryCategory.value)
}

async function loadItems() {
  itemsLoading.value = true
  try {
    const data = await getAdminInventoryItems(filters.value, page.value, pageSize, inventoryCategory.value)
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
  filters.value = { query: '' }
  advancedForm.value = { sourceSheet: '', priceMin: null, priceMax: null, rowMin: null, rowMax: null }
  searchItems()
}

async function handleInventoryFile(file: UploadFile) {
  if (!file.raw) return
  previewLoading.value = true
  try {
    const data = await previewInventory(file.raw, inventoryCategory.value)
    pendingPreview.value = data
    openPreviewDialog()
    notifySuccess('库存表已解析，请确认变更')
  } catch (error) {
    notifyError(error)
  } finally {
    previewLoading.value = false
  }
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

const previewDialogVisible = ref(false)
const previewLoading = ref(false)
const applying = ref(false)
const pendingPreview = ref<InventoryPreview | null>(null)
const previewTab = ref<'changed' | 'added' | 'removed'>('changed')
const keepSkus = ref<Set<string>>(new Set())
const skipSkus = ref<Set<string>>(new Set())
const previewQuery = ref('')

function previewMatches(item: InventoryDiffItem) {
  const keyword = previewQuery.value.trim().toUpperCase()
  return !keyword || item.sku.includes(keyword)
}

const filteredChanged = computed(() => (pendingPreview.value?.diff.changed ?? []).filter(previewMatches))
const filteredAdded = computed(() => (pendingPreview.value?.diff.added ?? []).filter(previewMatches))
const filteredRemoved = computed(() => (pendingPreview.value?.diff.removed ?? []).filter(previewMatches))

function formatPreview(item?: { price?: number; set_type?: string; source_sheet?: string }) {
  if (!item) return '-'
  const parts: string[] = []
  if (item.price != null) parts.push(`¥${item.price.toFixed(2)}`)
  if (item.set_type) parts.push(item.set_type)
  if (item.source_sheet) parts.push(item.source_sheet)
  return parts.join(' · ') || '-'
}

const skippedAdds = computed(() => {
  const removed = new Set(pendingPreview.value?.diff.removed.map((item) => item.sku) ?? [])
  return [...skipSkus.value].filter((sku) => !removed.has(sku)).length
})

const keptRemovals = computed(() => {
  const removed = new Set(pendingPreview.value?.diff.removed.map((item) => item.sku) ?? [])
  return [...skipSkus.value].filter((sku) => removed.has(sku)).length
})

function toggleKeep(sku: string) {
  const next = new Set(keepSkus.value)
  if (next.has(sku)) next.delete(sku)
  else next.add(sku)
  keepSkus.value = next
}

function toggleSkip(sku: string) {
  const next = new Set(skipSkus.value)
  if (next.has(sku)) next.delete(sku)
  else next.add(sku)
  skipSkus.value = next
}

function openPreviewDialog() {
  previewTab.value = 'changed'
  previewQuery.value = ''
  keepSkus.value = new Set()
  skipSkus.value = new Set()
  previewDialogVisible.value = true
}

async function checkPendingUpload() {
  try {
    const data = await getPendingInventory()
    const all = data.all ?? {}
    // 优先显示当前类目，其次任意有待确认的类目
    const target = all[inventoryCategory.value] ?? Object.values(all).find((state) => state !== null) ?? null
    if (target) {
      pendingPreview.value = target
      inventoryCategory.value = target.inventory_category ?? inventoryCategory.value
      openPreviewDialog()
    }
  } catch {
    // 忽略：没有待确认上传
  }
}

async function applyPreview() {
  if (!pendingPreview.value) return
  applying.value = true
  try {
    const category = pendingPreview.value.inventory_category ?? inventoryCategory.value
    const data = await applyPendingInventory([...keepSkus.value], [...skipSkus.value], category)
    notifySuccess(`${data.message}：共 ${data.sku_count} 个 SKU，保留旧值 ${data.kept} 项，跳过 ${data.skipped} 项`)
    previewDialogVisible.value = false
    pendingPreview.value = null
    inventoryCategory.value = category
    page.value = 1
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    applying.value = false
  }
}

async function discardPreview() {
  applying.value = true
  try {
    const category = pendingPreview.value?.inventory_category ?? inventoryCategory.value
    await discardPendingInventory(category)
    notifySuccess('已取消本次上传')
    previewDialogVisible.value = false
    pendingPreview.value = null
  } catch (error) {
    notifyError(error)
  } finally {
    applying.value = false
  }
}

async function rebuild() {
  // 重建会以 Excel 为准重写该类目明细，并清空排除名单，故需二次确认
  if (!await confirmAction('重建缓存会重新解析库存统计表，并以 Excel 为准覆盖当前类目的库存明细（手动添加/编辑的记录将丢失，已删除的 SKU 会重新出现）。是否继续？', '确认重建缓存')) return
  loading.value = true
  try {
    await rebuildInventory(inventoryCategory.value)
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
    await deleteInventoryItem(item.sku, inventoryCategory.value)
    notifySuccess('库存明细已删除')
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    deleting.value = null
  }
}

onMounted(() => {
  // 挂载完成后顶栏目标元素才确定存在于文档中，此时再启用 Teleport
  topbarReady.value = true
  void loadCategories()
  void load()
  void checkPendingUpload()
})
</script>

<template>
  <div class="admin-page admin-inventory-page admin-inventory-page--fixed">
    <!-- topbarReady 在 onMounted 后置真：冷启动时整棵组件树先构建、再插入文档，
         Teleport 只能查询已在文档中的元素，故必须等挂载完成后再 Teleport。 -->
    <Teleport v-if="topbarReady" to="#inventory-topbar-target">
      <InventoryTopbar
        admin
        :summary="activeTab === 'inventory' ? inventorySummary : []"
      >
        <el-select :model-value="inventoryCategory" class="inventory-category-select" title="库存类目" @change="switchCategory">
          <el-option v-for="cat in inventoryCategories" :key="cat.key" :value="cat.key" :label="cat.label" />
        </el-select>
        <el-button :icon="Refresh" :loading="loading" @click="refreshActiveTab">刷新</el-button>
      </InventoryTopbar>
    </Teleport>

    <el-tabs v-model="activeTab" class="inventory-tabs inventory-tabs--fixed admin-inventory-tabs">
      <el-tab-pane name="inventory" label="库存数据">
      <section class="section-band admin-inventory-data-panel">
      <div class="section-heading">
        <div class="subpage-title">
          <div>
            <h2>库存明细</h2>
            <p>共 {{ total }} 个 SKU，可单独添加、编辑或删除库存记录；重新上传 Excel 后以新表为准</p>
          </div>
        </div>
        <div class="inventory-actions">
          <el-button :icon="Filter" :type="activeFilterCount ? 'primary' : 'default'" @click="advancedVisible = !advancedVisible">
            高级搜索<span v-if="activeFilterCount"> ({{ activeFilterCount }})</span>
          </el-button>
          <el-upload :show-file-list="false" :auto-upload="false" accept=".xlsx,.xlsm" @change="handleInventoryFile">
            <el-button type="primary" :icon="Upload" :loading="previewLoading">更新库存表</el-button>
          </el-upload>
          <el-button type="success" :icon="Plus" @click="openCreateDialog">添加库存</el-button>
          <el-dropdown trigger="click" hide-on-click>
            <el-button :icon="More">更多操作</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item :icon="Plus" @click="openInventoryCategoryDialog">新建类目</el-dropdown-item>
                <el-dropdown-item :icon="Edit" :disabled="!inventoryCategories.length" @click="openEditCategoryDialog(inventoryCategories.find((c) => c.key === inventoryCategory)!)">编辑类目</el-dropdown-item>
                <el-dropdown-item :icon="Refresh" divided @click="rebuild">重建缓存</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
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
          <el-table-column label="操作" width="150" fixed="right"><template #default="scope"><el-button link type="primary" :icon="Edit" @click="openEditDialog(scope.row)">编辑</el-button><el-button link type="danger" :icon="Delete" :loading="deleting === scope.row.sku" @click="remove(scope.row)">删除</el-button></template></el-table-column>
        </el-table>
      </div>
      <el-pagination v-if="total > pageSize" v-model:current-page="page" class="pagination inventory-pagination" layout="prev, pager, next" :page-size="pageSize" :total="total" @current-change="loadItems" />
    </section>
      </el-tab-pane>
      <el-tab-pane name="half-headcost" label="头程减半名单">
        <AdminHalfHeadcost ref="halfHeadcostRef" :inventory-category="inventoryCategory" />
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="itemDialogVisible" :title="itemFormMode === 'create' ? '添加库存明细' : '编辑库存明细'" width="min(520px, 92vw)">
      <el-form label-position="top" @submit.prevent="saveItem">
        <el-form-item label="SKU" required><el-input v-model="itemForm.sku" maxlength="255" placeholder="请输入 SKU" /></el-form-item>
        <el-form-item label="价格"><el-input-number v-model="itemForm.price" :min="0" :precision="2" :step="0.01" controls-position="right" placeholder="可留空" /></el-form-item>
        <el-form-item label="类型" required><el-input v-model="itemForm.set_type" maxlength="64" placeholder="例如：单品、6件套" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="itemDialogVisible = false">取消</el-button><el-button type="primary" :loading="itemSaving" @click="saveItem">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="previewDialogVisible" :title="`确认库存表变更（${categoryLabel(pendingPreview?.inventory_category ?? inventoryCategory)}）`" width="min(880px, 94vw)" :close-on-click-modal="false">
      <div v-if="pendingPreview" class="inventory-preview">
        <el-alert type="info" :closable="false">
          【{{ categoryLabel(pendingPreview.inventory_category ?? inventoryCategory) }}】上传于 {{ pendingPreview.uploaded_at }}，新表共 {{ pendingPreview.sku_count }} 个 SKU。默认应用全部变更，勾选条目可逐项调整。
        </el-alert>
        <div class="inventory-preview-search">
          <el-input v-model="previewQuery" clearable placeholder="输入 SKU 过滤" :prefix-icon="Search" />
        </div>
        <el-tabs v-model="previewTab">
          <el-tab-pane :label="`变更 (${pendingPreview.diff.changed.length})`" name="changed">
            <el-table :data="filteredChanged" max-height="380">
              <el-table-column prop="sku" label="SKU" min-width="150" />
              <el-table-column label="当前值" min-width="120"><template #default="scope">{{ formatPreview(scope.row.old) }}</template></el-table-column>
              <el-table-column label="新值" min-width="120"><template #default="scope">{{ formatPreview(scope.row.new) }}</template></el-table-column>
              <el-table-column label="保留旧值" width="110" fixed="right">
                <template #default="scope">
                  <el-checkbox :model-value="keepSkus.has(scope.row.sku)" @change="toggleKeep(scope.row.sku)">保留</el-checkbox>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
          <el-tab-pane :label="`新增 (${pendingPreview.diff.added.length})`" name="added">
            <el-table :data="filteredAdded" max-height="380">
              <el-table-column prop="sku" label="SKU" min-width="150" />
              <el-table-column label="新值" min-width="120"><template #default="scope">{{ formatPreview(scope.row.new) }}</template></el-table-column>
              <el-table-column label="跳过" width="110" fixed="right">
                <template #default="scope">
                  <el-checkbox :model-value="skipSkus.has(scope.row.sku)" @change="toggleSkip(scope.row.sku)">跳过</el-checkbox>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
          <el-tab-pane :label="`删除 (${pendingPreview.diff.removed.length})`" name="removed">
            <el-table :data="filteredRemoved" max-height="380">
              <el-table-column prop="sku" label="SKU" min-width="150" />
              <el-table-column label="当前值" min-width="120"><template #default="scope">{{ formatPreview(scope.row.old) }}</template></el-table-column>
              <el-table-column label="保留" width="110" fixed="right">
                <template #default="scope">
                  <el-checkbox :model-value="skipSkus.has(scope.row.sku)" @change="toggleSkip(scope.row.sku)">保留</el-checkbox>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
        <div class="inventory-preview-summary">
          变更 {{ pendingPreview.diff.changed.length }} 项（保留旧值 {{ keepSkus.size }} 项）、新增 {{ pendingPreview.diff.added.length }} 项（跳过 {{ skippedAdds }} 项）、删除 {{ pendingPreview.diff.removed.length }} 项（保留 {{ keptRemovals }} 项）、无变化 {{ pendingPreview.diff.unchanged.length }} 项
        </div>
      </div>
      <template #footer>
        <el-button :loading="applying" @click="discardPreview">取消上传</el-button>
        <el-button type="primary" :loading="applying" @click="applyPreview">确认应用</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="categoryDialogVisible" :title="categoryFormMode === 'create' ? '新建库存类目' : '编辑库存类目'" width="min(520px, 92vw)">
      <el-form label-position="top">
        <el-form-item label="类目代码" required>
          <el-input v-model="categoryForm.key" maxlength="16" :disabled="categoryFormMode === 'edit'" placeholder="例如 B、C" />
          <div v-if="categoryFormMode === 'edit'" class="field-hint">类目代码创建后不可修改</div>
        </el-form-item>
        <el-form-item label="类目名称" required><el-input v-model="categoryForm.label" maxlength="80" placeholder="例如 B类目" /></el-form-item>
        <el-form-item label="SKU 编码正则"><el-input v-model="categoryForm.code_pattern" maxlength="255" placeholder="留空使用通用编码模式" /></el-form-item>
        <el-form-item label="货值上限"><el-input-number v-model="categoryForm.price_max" :min="0.01" :max="1000000" :precision="2" controls-position="right" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="categoryDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="categorySaving" @click="saveInventoryCategory">{{ categoryFormMode === 'create' ? '创建' : '保存' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.admin-inventory-page--fixed {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.admin-inventory-data-panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.admin-inventory-data-panel .section-heading {
  align-items: center;
  padding-bottom: 12px;
  margin-bottom: 0;
}

/* 操作按钮区：flex + wrap，后续新增按钮直接追加即可自动换行 */
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

/* 「更多操作」下拉：与相邻按钮保持同一行高，菜单项左对齐 */
.inventory-actions .el-dropdown {
  display: inline-flex;
}

.inventory-actions :deep(.el-dropdown-menu__item) {
  min-width: 148px;
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

.field-hint {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.4;
  color: var(--el-text-color-secondary);
}