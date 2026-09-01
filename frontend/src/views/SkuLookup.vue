<script setup lang="ts">
import { ref } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { errorMessage, querySkus } from '../api'
import type { SkuResult } from '../types'

const input = ref('')
const loading = ref(false)
const results = ref<SkuResult[]>([])

async function query() {
  const skus = input.value.split(/[\s,，;；]+/).map((item) => item.trim()).filter(Boolean)
  if (!skus.length) {
    ElMessage.warning('请输入 SKU')
    return
  }
  loading.value = true
  try {
    results.value = (await querySkus(skus)).items
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <section class="section-band">
    <div class="section-heading">
      <div><h2>批量查询</h2><p>空格、逗号或换行分隔</p></div>
    </div>
    <el-input v-model="input" type="textarea" :rows="7" resize="vertical" placeholder="MB131-491" />
    <div class="action-row left">
      <el-button type="primary" :icon="Search" :loading="loading" @click="query">查询价格</el-button>
      <el-button @click="input = ''; results = []">清空</el-button>
    </div>
  </section>

  <section v-if="results.length" class="section-band">
    <div class="section-heading"><div><h2>查询结果</h2><p>共 {{ results.length }} 个 SKU</p></div></div>
    <el-table :data="results" stripe>
      <el-table-column prop="sku" label="SKU" min-width="160" />
      <el-table-column label="状态" width="90">
        <template #default="scope"><el-tag :type="scope.row.found ? 'success' : 'danger'">{{ scope.row.found ? '已找到' : '未找到' }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="price" label="价格" width="110"><template #default="scope">{{ scope.row.price?.toFixed(2) || '-' }}</template></el-table-column>
      <el-table-column prop="set_type" label="类型" width="110" />
      <el-table-column prop="source_sheet" label="来源工作表" min-width="140" />
      <el-table-column prop="source_row" label="行号" width="80" />
      <el-table-column prop="source_column" label="价格列" width="90" />
    </el-table>
  </section>
</template>
