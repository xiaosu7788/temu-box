<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Delete, Search, Upload } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile, UploadFiles, UploadUserFile } from 'element-plus'
import { deleteHalfHeadcost, errorMessage, getHalfHeadcost, getMe, importHalfHeadcost } from '../api'
import type { HalfHeadcostItem } from '../types'

const query = ref('')
const items = ref<HalfHeadcostItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 30
const files = ref<UploadUserFile[]>([])
const loading = ref(false)
const isAdmin = ref(false)

async function load() {
  loading.value = true
  try {
    const data = await getHalfHeadcost(query.value, page.value, pageSize)
    items.value = data.items
    total.value = data.total
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  load()
}

function keepLatest(_file: UploadFile, uploadFiles: UploadFiles) {
  files.value = uploadFiles.slice(-1)
}

async function importList() {
  const file = files.value[0]?.raw
  if (!file) return
  loading.value = true
  try {
    const result = await importHalfHeadcost(file)
    ElMessage.success(`提取 ${result.incoming} 个，新增 ${result.added} 个`)
    files.value = []
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function remove(sku: string) {
  await ElMessageBox.confirm(`从头程减半名单删除 ${sku}？`, '确认删除', { type: 'warning' })
  try {
    await deleteHalfHeadcost(sku)
    ElMessage.success('已删除')
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

onMounted(async () => {
  try { isAdmin.value = (await getMe()).role === 'admin' } catch (error) { ElMessage.error(errorMessage(error)) }
  load()
})
</script>

<template>
  <div class="half-headcost-page">
    <section class="section-band compact-band half-headcost-toolbar">
    <div class="toolbar-row">
      <el-input v-model="query" clearable placeholder="搜索 SKU" :prefix-icon="Search" @keyup.enter="search" @clear="search" />
      <el-button type="primary" :icon="Search" @click="search">查询</el-button>
      <el-upload v-if="isAdmin" v-model:file-list="files" :auto-upload="false" :limit="1" accept=".xlsx,.xlsm" :show-file-list="false" @change="keepLatest">
        <el-button :icon="Upload">选择名单</el-button>
      </el-upload>
      <el-button v-if="isAdmin" type="success" :disabled="!files.length" :loading="loading" @click="importList">导入合并</el-button>
      <span v-if="files[0]" class="selected-file">{{ files[0].name }}</span>
    </div>
    </section>

    <section class="section-band half-headcost-content">
      <div class="section-heading"><div><h2>名单数据</h2><p>共 {{ total }} 个 SKU</p></div></div>
      <el-table v-loading="loading" :data="items" stripe>
        <el-table-column prop="sku" label="SKU" min-width="180" />
        <el-table-column prop="set_type" label="类型" width="130" />
        <el-table-column label="操作" width="90" align="right">
          <template #default="scope"><el-button v-if="isAdmin" type="danger" link :icon="Delete" @click="remove(scope.row.sku)">删除</el-button></template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-if="total > pageSize"
        v-model:current-page="page"
        class="pagination"
        layout="prev, pager, next"
        :page-size="pageSize"
        :total="total"
        @current-change="load"
      />
    </section>
  </div>
</template>
