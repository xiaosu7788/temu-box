<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { Delete, Edit, Plus, Search, Upload } from '@element-plus/icons-vue'
import type { UploadFile } from 'element-plus'
import { createHalfHeadcost, deleteHalfHeadcost, getCategories, getHalfHeadcost, importHalfHeadcost, updateHalfHeadcost } from '../api'
import { confirmAction, notifyError, notifySuccess } from '../feedback'
import type { CategorySummary, HalfHeadcostItem } from '../types'

// 作为「库存管理」页的标签内容使用。
// 库存类目由页面顶部的切换器统一控制（父级传入），此处不再单独提供选择器。
const props = defineProps<{ inventoryCategory: string }>()

const businessCategories = ref<CategorySummary[]>([])
// 由库存类目反查对应品类：顶部的切换器因此可以完整控制本名单
const categoryCode = ref('')

const query = ref('')
const items = ref<HalfHeadcostItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 30
const loading = ref(false)
const uploading = ref(false)

const dialogVisible = ref(false)
const saving = ref(false)
const formMode = ref<'create' | 'edit'>('create')
const editingSku = ref('')
const form = ref({ sku: '', set_type: '单品' })

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
    const data = await getHalfHeadcost(query.value, page.value, pageSize, categoryCode.value || undefined, props.inventoryCategory)
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

function openCreateDialog() {
  formMode.value = 'create'
  editingSku.value = ''
  form.value = { sku: '', set_type: '单品' }
  dialogVisible.value = true
}

function openEditDialog(item: HalfHeadcostItem) {
  formMode.value = 'edit'
  editingSku.value = item.sku
  form.value = { sku: item.sku, set_type: item.set_type || '单品' }
  dialogVisible.value = true
}

async function saveEntry() {
  const setType = form.value.set_type.trim() || '单品'
  if (formMode.value === 'create') {
    const sku = form.value.sku.trim()
    if (!sku) {
      notifyError(new Error('请输入 SKU'))
      return
    }
    saving.value = true
    try {
      const data = await createHalfHeadcost({ sku, set_type: setType, category_code: categoryCode.value || undefined, inventory_category: props.inventoryCategory })
      notifySuccess(data.message || '头程减半名单已添加')
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
    const data = await updateHalfHeadcost(editingSku.value, { set_type: setType, category_code: categoryCode.value || undefined, inventory_category: props.inventoryCategory })
    notifySuccess(data.message || '头程减半名单已更新')
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
    const result = await importHalfHeadcost(file.raw, categoryCode.value || undefined, props.inventoryCategory)
    notifySuccess(`提取 ${result.incoming} 个，新增 ${result.added} 个，当前共 ${result.total} 个`)
    page.value = 1
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    uploading.value = false
  }
}

async function remove(item: HalfHeadcostItem) {
  if (!await confirmAction(`从头程减半名单删除 ${item.sku}？`, '确认删除')) return
  try {
    await deleteHalfHeadcost(item.sku, categoryCode.value || undefined, props.inventoryCategory)
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
  <section class="section-band admin-half-headcost-panel">
    <div class="section-heading">
      <div class="subpage-title">
        <div>
          <h2>头程减半名单</h2>
          <p>共 {{ total }} 个 SKU（{{ inventoryCategory }} 类目），支持导入合并、单条添加与编辑</p>
        </div>
      </div>
      <div class="inventory-actions">
        <el-upload :show-file-list="false" :auto-upload="false" accept=".xlsx,.xlsm" @change="handleImportFile">
          <el-button type="primary" :icon="Upload" :loading="uploading">导入合并</el-button>
        </el-upload>
        <el-button type="success" :icon="Plus" @click="openCreateDialog">单条添加</el-button>
      </div>
    </div>

    <div class="toolbar-row inventory-search-row">
      <el-input
        v-model="query"
        clearable
        placeholder="搜索 SKU，如 MB131-100"
        :prefix-icon="Search"
        @keyup.enter="search"
        @clear="search"
      />
      <el-button type="primary" :icon="Search" :loading="loading" @click="search">查询</el-button>
      <el-button :disabled="!query" @click="clearSearch">显示全部</el-button>
    </div>

    <div class="inventory-table-scroll">
      <el-table v-loading="loading" :data="items" stripe>
        <el-table-column prop="sku" label="SKU" min-width="180" />
        <el-table-column prop="set_type" label="类型" width="130" />
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="scope">
            <el-button link type="primary" :icon="Edit" @click="openEditDialog(scope.row)">编辑</el-button>
            <el-button link type="danger" :icon="Delete" @click="remove(scope.row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <el-pagination v-if="total > pageSize" v-model:current-page="page" class="pagination inventory-pagination" layout="prev, pager, next" :page-size="pageSize" :total="total" @current-change="load" />

    <el-dialog v-model="dialogVisible" :title="formMode === 'create' ? '添加头程减半 SKU' : '编辑头程减半 SKU'" width="min(520px, 92vw)">
      <el-form label-position="top" @submit.prevent="saveEntry">
        <el-form-item label="SKU" required>
          <el-input v-model="form.sku" maxlength="255" :disabled="formMode === 'edit'" placeholder="请输入 SKU" />
        </el-form-item>
        <el-form-item label="类型" required>
          <el-input v-model="form.set_type" maxlength="64" placeholder="例如：单品、6件套" />
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
.admin-half-headcost-panel {
  min-height: 0;
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  margin-bottom: 0;
}

.admin-half-headcost-panel .section-heading {
  flex: 0 0 auto;
  align-items: center;
}
</style>
