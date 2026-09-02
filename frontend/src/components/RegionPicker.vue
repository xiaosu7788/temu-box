<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { Location } from '@element-plus/icons-vue'
import { getRegions } from '../api'
import { notifyError } from '../feedback'
import type { RegionSummary } from '../types'

const props = withDefaults(defineProps<{ modelValue: string; compact?: boolean }>(), { compact: false })
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const regions = ref<RegionSummary[]>([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    regions.value = await getRegions()
    if (!regions.value.some((item) => item.code === props.modelValue)) {
      const fallback = regions.value.find((item) => item.is_default) || regions.value[0]
      if (fallback) emit('update:modelValue', fallback.code)
    }
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
  }
}

watch(() => props.modelValue, (value) => {
  if (!value && regions.value.length) emit('update:modelValue', (regions.value.find((item) => item.is_default) || regions.value[0]).code)
})
onMounted(load)
</script>

<template>
  <div class="region-picker" :class="{ 'region-picker--compact': props.compact }">
    <span><el-icon><Location /></el-icon>业务区域</span>
    <el-select :model-value="modelValue" :loading="loading" placeholder="选择区域" @update:model-value="emit('update:modelValue', $event)">
      <el-option v-for="region in regions" :key="region.code" :label="region.name" :value="region.code">
        <span>{{ region.name }}</span><small>{{ region.code }} · {{ region.currency }}</small>
      </el-option>
    </el-select>
  </div>
</template>