<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { Delete, Edit, Plus, Search, Upload } from '@element-plus/icons-vue'
import type { UploadFile } from 'element-plus'
import { createWeightHeadcost, deleteWeightHeadcost, getCategories, getWeightHeadcost, importWeightHeadcost, updateWeightHeadcost } from '../api'
import { confirmAction, notifyError, notifySuccess } from '../feedback'
import type { CategorySummary, WeightHeadcostItem } from '../types'

// 作为「库存管理」页的标签内容使用。
// 库存类目由页面顶部的切换器统一控制（父级传入），此处不再单独提供选择器。
const props = defineProps<{ inventoryCategory: string }>()

const businessCategories = ref<CategorySummary[]>([])
// 由库存类目反查对应品类：顶部的切换器因此可以完整控制本名单
const categoryCode = ref('')

const query = ref('')
const items = ref<WeightHeadcostItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 30
const loading = ref(false)
const uploading = ref(false)

const dialogVisible = ref(false)
const saving = ref(false)
const formMode = ref<'create' | 'edit'>('create')
const editingSku = ref('')
const form = ref({ sku: '', weight_g: 0 })
const previewHeadcost = ref(0)

function resolveCategoryCode() {
  const match = businessCategories.value.find((item) => (item.inventory_category || 'A') === props.inventoryCategory)
  categoryCode.value = match?.code || ''
}

async function loadBusinessCategories() {
  try {
    businessCategories.value = await getCategories()
  } catch {
    businessCategories.value = []
  }
  resolveCategoryCode()
}

async function load() {
  loading.value = true
  try {
    const data = await getWeightHeadcost(query.value, page.value, pageSize, categoryCode.value || undefined, props.inventoryCategory)
    items.value = data.items
    total.value = data.total
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
  }
}

async function reload() {
  page.value = 1
  await load()
}

function search() {
  page.value = 1
  void load()
}

function clearSearch() {
  query.value = ''
  search()
}

function updatePreviewHeadcost() {
  previewHeadcost.value = Math.round(currentWeight() * 0.017 * 100) / 100
}

function currentWeight(): number {
  const value = Number(form.value.weight_g)
  return Number.isFinite(value) ? value : 0
}

function openCreateDialog() {
  formMode.value = 'create'
  editingSku.value = ''
  form.value = { sku: '', weight_g: 0 }
  updatePreviewHeadcost()
  dialogVisible.value = true
}

function openEditDialog(item: WeightHeadcostItem) {
  formMode.value = 'edit'
  editingSku.value = item.sku
  form.value = { sku: item.sku, weight_g: item.weight_g }
  updatePreviewHeadcost()
  dialogVisible.value = true
}

async function saveEntry() {
  const weightG = currentWeight()
  if (weightG <= 0) {
    notifyError(new Error('克重必须大于 0'))
    return
  }
  if (formMode.value === 'create') {
    const sku = form.value.sku.trim()
    if (!sku) {
      notifyError(new Error('请输入编号'))
      return
    }
    saving.value = true
    try {
      const data = await createWeightHeadcost({ sku, weight_g: weightG, category_code: categoryCode.value || undefined, inventory_category: props.inventoryCategory })
      notifySuccess(data.message || '克重计算头程名单已添加')
      dialogVisible.value = false
      page.value = 1
      await load()
    } catch (error) {
      notifyError(error)
    } finally {
      saving.value = false
    }
    return
  }
  if (!editingSku.value) return
  saving.value = true
  try {
    const data = await updateWeightHeadcost(editingSku.value, { weight_g: weightG, category_code: categoryCode.value || undefined, inventory_category: props.inventoryCategory })
    notifySuccess(data.message || '克重计算头程名单已更新')
    dialogVisible.value = false
    editingSku.value = ''
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    saving.value = false
  }
}

async function handleImportFile(file: UploadFile) {
  if (!file.raw) return
  uploading.value = true
  try {
    const result = await importWeightHeadcost(file.raw, categoryCode.value || undefined, props.inventoryCategory)
    notifySuccess(`提取 ${result.incoming} 个，新增 ${result.added} 个，更新 ${result.updated} 个，当前共 ${result.total} 个`)
    page.value = 1
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    uploading.value = false
  }
}

async function remove(item: WeightHeadcostItem) {
  if (!await confirmAction(`从克重计算头程名单删除 ${item.sku}？`, '确认删除')) return
  try {
    await deleteWeightHeadcost(item.sku, categoryCode.value || undefined, props.inventoryCategory)
    notifySuccess('已删除')
    await load()
  } catch (error) {
    notifyError(error)
  }
}

watch(() => props.inventoryCategory, () => {
  resolveCategoryCode()
  page.value = 1
  void load()
})

onMounted(async () => {
  await loadBusinessCategories()
  void load()
})

defineExpose({ reload })
</script>

<template>
  <section class="section-band admin-weight-headcost-panel">
    <div class="section-heading">
      <div class="subpage-title">
        <div>
          <h2>克重计算头程</h2>
          <p>共 {{ total }} 个编号（{{ inventoryCategory }} 类目），头程 = 克重 × 0.017，支持导入、单条添加与编辑</p>
        </div>
      </div>
      <div class="inventory-actions">
        <el-upload :show-file-list="false" :auto-upload="false" accept=".xlsx,.xlsm" @change="handleImportFile">
          <el-button type="primary" :icon="Upload" :loading="uploading">导入参考表</el-button>
        </el-upload>
        <el-button type="success" :icon="Plus" @click="openCreateDialog">单条添加</el-button>
      </div>
    </div>

    <div class="toolbar-row inventory-search-row">
      <el-input
        v-model="query"
        clearable
        placeholder="搜索编号，如 MB131-260"
        :prefix-icon="Search"
        @keyup.enter="search"
        @clear="search"
      />
      <el-button type="primary" :icon="Search" :loading="loading" @click="search">查询</el-button>
      <el-button :disabled="!query" @click="clearSearch">显示全部</el-button>
    </div>

    <div class="inventory-table-scroll">
      <el-table v-loading="loading" :data="items" stripe>
        <el-table-column prop="sku" label="编号" min-width="200" />
        <el-table-column label="克重(g)" width="130" align="right" header-align="right">
          <template #default="scope"><span class="tabular-num">{{ scope.row.weight_g.toFixed(2) }}</span></template>
        </el-table-column>
        <el-table-column label="头程" width="130" align="right" header-align="right">
          <template #default="scope"><span class="tabular-num">¥{{ scope.row.headcost.toFixed(2) }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="scope">
            <el-button link type="primary" :icon="Edit" @click="openEditDialog(scope.row)">编辑</el-button>
            <el-button link type="danger" :icon="Delete" @click="remove(scope.row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <el-pagination v-if="total > pageSize" v-model:current-page="page" class="pagination inventory-pagination" layout="prev, pager, next" :page-size="pageSize" :total="total" @current-change="load" />

    <el-dialog v-model="dialogVisible" :title="formMode === 'create' ? '添加克重计算头程' : '编辑克重计算头程'" width="min(520px, 92vw)">
      <el-form label-position="top" @submit.prevent="saveEntry">
        <el-form-item label="编号" required>
          <el-input v-model="form.sku" maxlength="255" :disabled="formMode === 'edit'" placeholder="请输入编号，如 MB131-260" />
        </el-form-item>
        <el-form-item label="克重(g)" required>
          <el-input-number v-model="form.weight_g" :min="0.01" :max="1000000" :precision="2" :step="1" controls-position="right" @change="updatePreviewHeadcost" />
          <div class="field-hint">头程 = 克重 × 0.017 = ¥{{ previewHeadcost.toFixed(2) }}</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveEntry">保存</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.admin-weight-headcost-panel {
  min-height: 0;
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  margin-bottom: 0;
}

.admin-weight-headcost-panel .section-heading {
  flex: 0 0 auto;
  align-items: center;
}
</style>
