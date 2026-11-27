<script setup lang="ts">
/**
 * 操作记录：把「商品创建」和「客服操作」两类日志集中在一页，用标签切换。
 *
 * 之前只有客服写操作，建品记录散在商品列表里看不到全貌；
 * 现在两类都在这里，成功/失败可分类筛选。
 */
import { ref } from 'vue'
import StoreProductLogsPanel from './StoreProductLogsPanel.vue'
import StoreKefuActionsPanel from './StoreKefuActionsPanel.vue'

const tab = ref<'product' | 'kefu'>('product')
</script>

<template>
  <el-card shadow="never" class="records">
    <el-tabs v-model="tab">
      <el-tab-pane label="商品创建" name="product">
        <StoreProductLogsPanel />
      </el-tab-pane>
      <el-tab-pane label="客服操作" name="kefu">
        <StoreKefuActionsPanel />
      </el-tab-pane>
    </el-tabs>
  </el-card>
</template>

<style scoped>
.records {
  border: none;
}

.records :deep(.el-card__body) {
  padding: 4px 0 0;
}

.records :deep(.el-tabs__header) {
  margin-bottom: 14px;
}
</style>
