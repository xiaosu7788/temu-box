<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { Goods } from '@element-plus/icons-vue'
import { getCategories } from '../api'
import { notifyError } from '../feedback'
import { selectedRegionCode } from '../regionState'
import type { CategorySummary } from '../types'

const props = withDefaults(defineProps<{ modelValue: string; compact?: boolean; filterByRegion?: boolean }>(), { compact: false, filterByRegion: false })
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const categories = ref<CategorySummary[]>([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    // filterByRegion：只显示对当前区域开放的品类（减半名单等与区域无关的页面不过滤）
    categories.value = await getCategories(props.filterByRegion ? selectedRegionCode.value || undefined : undefined)
    if (!categories.value.some((item) => item.code === props.modelValue)) {
      const fallback = categories.value.find((item) => item.is_default) || categories.value[0]
      if (fallback) emit('update:modelValue', fallback.code)
    }
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
  }
}

watch(() => props.modelValue, (value) => {
  if (!value && categories.value.length) emit('update:modelValue', (categories.value.find((item) => item.is_default) || categories.value[0]).code)
})
watch(() => props.filterByRegion ? selectedRegionCode.value : '', () => {
  if (props.filterByRegion) load()
})
onMounted(load)
</script>

<template>
  <div class="category-picker" :class="{ 'category-picker--compact': props.compact }">
    <span><el-icon><Goods /></el-icon>品类</span>
    <el-select :model-value="modelValue" :loading="loading" placeholder="选择品类" @update:model-value="emit('update:modelValue', $event)">
      <el-option v-for="category in categories" :key="category.code" :label="category.name" :value="category.code">
        <span>{{ category.name }}</span><small>{{ category.template_label }}</small>
      </el-option>
    </el-select>
  </div>
</template>

<style scoped>
.category-picker {
  display: flex;
  align-items: center;
  gap: 10px;
}

.category-picker > span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  white-space: nowrap;
}

.category-picker :deep(.el-select) {
  width: 160px;
}

.category-picker--compact > span {
  font-size: 12px;
}

.category-picker--compact :deep(.el-select) {
  width: 128px;
}

.category-picker small {
  float: right;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
