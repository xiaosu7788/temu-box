<script setup lang="ts">
import { ref } from 'vue'
import MaterialPicker from './MaterialPicker.vue'
import ProductCreateManual from './ProductCreateManual.vue'
import ProductCreateBatch from './ProductCreateBatch.vue'

/** 创建商品：手动 / 批量两个模式共用一页，切换不丢状态。 */
const mode = ref<'manual' | 'batch'>('manual')

/** 右上角素材中心：只做上传/整理，选完不落到表单 */
const materialCenterVisible = ref(false)
</script>

<template>
  <div class="product-create">
    <div class="product-create__head">
      <el-tabs v-model="mode" class="product-create__tabs">
        <el-tab-pane label="手动创建" name="manual">
          <ProductCreateManual />
        </el-tab-pane>
        <el-tab-pane label="批量创建" name="batch">
          <ProductCreateBatch />
        </el-tab-pane>
      </el-tabs>
      <el-button class="product-create__mc" @click="materialCenterVisible = true">素材中心</el-button>
    </div>

    <MaterialPicker
      v-model="materialCenterVisible"
      carousel
      :selected="[]"
      @confirm="materialCenterVisible = false"
    />
  </div>
</template>

<style scoped>
.product-create {
  display: flex;
  flex-direction: column;
}

.product-create__head {
  position: relative;
}

.product-create__tabs :deep(.el-tabs__header) {
  margin-bottom: 12px;
}

/* 贴在 tab 行右侧，不占 tab 位置 */
.product-create__mc {
  position: absolute;
  top: 0;
  right: 0;
  z-index: 1;
}
</style>
