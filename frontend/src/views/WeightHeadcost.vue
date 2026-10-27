<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { getCategories, getWeightHeadcost } from '../api'
import { notifyError } from '../feedback'
import type { CategorySummary, WeightHeadcostItem } from '../types'
import { selectedCategoryCode } from '../regionState'

// 库存类目由「品类管理」中的绑定关系推导，页面上只保留一个品类选择器
const businessCategories = ref<CategorySummary[]>([])
const inventoryCategory = ref('A')

const query = ref('')
const items = ref<WeightHeadcostItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 30
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const data = await getWeightHeadcost(query.value, page.value, pageSize, selectedCategoryCode.value, inventoryCategory.value)
    items.value = data.items
    total.value = data.total
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  void load()
}

async function loadBusinessCategories() {
  try {
    businessCategories.value = await getCategories()
    const selected = businessCategories.value.find((item) => item.code === selectedCategoryCode.value)
    inventoryCategory.value = selected?.inventory_category || 'A'
  } catch {
    inventoryCategory.value = 'A'
  }
}

watch(selectedCategoryCode, async () => {
  const selected = businessCategories.value.find((item) => item.code === selectedCategoryCode.value)
  inventoryCategory.value = selected?.inventory_category || 'A'
  page.value = 1
  await load()
})

onMounted(async () => {
  await loadBusinessCategories()
  void load()
})
</script>

<template>
  <div class="half-headcost-page">
    <section class="section-band compact-band half-headcost-toolbar">
      <div class="toolbar-row">
        <el-input v-model="query" clearable placeholder="搜索编号，如 MB131-260" :prefix-icon="Search" @keyup.enter="search" @clear="search" />
        <el-button type="primary" :icon="Search" @click="search">查询</el-button>
        <span class="half-headcost-readonly-hint">名单由管理员在「后台管理 → 库存管理 → 克重计算头程」维护；头程 = 克重 × 0.017</span>
      </div>
    </section>

    <section class="section-band half-headcost-content">
      <div class="section-heading"><div><h2>名单数据</h2><p>共 {{ total }} 个编号</p></div></div>
      <el-table v-loading="loading" :data="items" stripe>
        <el-table-column prop="sku" label="编号" min-width="200" />
        <el-table-column label="克重(g)" width="130" align="right" header-align="right">
          <template #default="scope"><span class="tabular-num">{{ scope.row.weight_g.toFixed(2) }}</span></template>
        </el-table-column>
        <el-table-column label="头程" width="130" align="right" header-align="right">
          <template #default="scope"><span class="tabular-num">¥{{ scope.row.headcost.toFixed(2) }}</span></template>
        </el-table-column>
      </el-table>
      <el-pagination v-if="total > pageSize" v-model:current-page="page" class="pagination" layout="prev, pager, next" :page-size="pageSize" :total="total" @current-change="load" />
    </section>
  </div>
</template>

<style scoped>
.half-headcost-toolbar {
  margin-bottom: 0;
}

.half-headcost-toolbar .toolbar-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.half-headcost-toolbar .toolbar-row .el-input {
  width: min(340px, 100%);
}

.half-headcost-readonly-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.half-headcost-content {
  padding-top: 8px;
}
</style>
