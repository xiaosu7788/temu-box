<script setup lang="ts">
import { computed, ref } from 'vue'

/** 色系 → 颜色的两级色板选择器（主表单与批量卡片共用） */
export interface ColorGroup {
  groupId: number
  groupName: string
  colors: Array<{ vid: number; value: string; extendInfo?: string | null }>
}

const props = defineProps<{
  modelValue: number | null
  groups: ColorGroup[]
  placeholder?: string
}>()

const emit = defineEmits<{ 'update:modelValue': [value: number | null] }>()

const visible = ref(false)
const activeGroupId = ref<number | null>(null)

const activeGroup = computed(
  () => props.groups.find((g) => g.groupId === activeGroupId.value) || props.groups[0] || null,
)

const currentColors = computed(() => activeGroup.value?.colors || [])

const totalCount = computed(() => props.groups.reduce((n, g) => n + (g.colors || []).length, 0))

/** extendInfo 形如 "(255,255,255,1)"，转成 CSS 颜色 */
function swatchOf(extendInfo?: string | null): string {
  if (!extendInfo) return '#e5e7eb'
  const m = /\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+))?\)/.exec(extendInfo)
  if (!m) return '#e5e7eb'
  const [, r, g, b, a] = m
  return a === undefined || a === '' ? `rgb(${r},${g},${b})` : `rgba(${r},${g},${b},${a})`
}

function swatchOfVid(vid?: number | null): string {
  if (!vid) return '#e5e7eb'
  for (const g of props.groups) {
    const hit = (g.colors || []).find((c) => c.vid === vid)
    if (hit) return swatchOf(hit.extendInfo)
  }
  return '#e5e7eb'
}

const currentName = computed(() => {
  if (!props.modelValue) return ''
  for (const g of props.groups) {
    const hit = (g.colors || []).find((c) => c.vid === props.modelValue)
    if (hit) return hit.value
  }
  return ''
})

function onOpen() {
  if (!props.groups.length) return
  // 打开时定位到已选颜色所在色系
  if (props.modelValue) {
    const owner = props.groups.find((g) => (g.colors || []).some((c) => c.vid === props.modelValue))
    activeGroupId.value = owner?.groupId ?? props.groups[0].groupId
  } else if (activeGroupId.value === null) {
    activeGroupId.value = props.groups[0].groupId
  }
}

function pick(vid: number) {
  emit('update:modelValue', vid)
  visible.value = false
}

function clear() {
  emit('update:modelValue', null)
  visible.value = false
}
</script>

<template>
  <el-popover
    v-model:visible="visible"
    :width="476"
    placement="bottom-start"
    trigger="click"
    popper-class="colorpicker__pop"
    @show="onOpen"
  >
    <template #reference>
      <button type="button" class="colorpicker__trigger" :disabled="!groups.length">
        <span class="colorpicker__dot" :style="{ background: swatchOfVid(modelValue) }" />
        <span class="colorpicker__text">{{ currentName || placeholder || '选择颜色' }}</span>
        <span v-if="!currentName" class="colorpicker__count">{{ totalCount }} 个可选</span>
      </button>
    </template>

    <div class="colorpicker__panel">
      <aside class="colorpicker__groups">
        <button
          v-for="g in groups"
          :key="g.groupId"
          type="button"
          class="colorpicker__group"
          :class="{ 'colorpicker__group--active': g.groupId === activeGroup?.groupId }"
          @mouseenter="activeGroupId = g.groupId"
          @focus="activeGroupId = g.groupId"
          @click="activeGroupId = g.groupId"
        >
          <span class="colorpicker__dot" :style="{ background: swatchOf((g.colors || [])[0]?.extendInfo) }" />
          <span class="colorpicker__gname">{{ g.groupName }}</span>
          <span class="colorpicker__gcount">{{ (g.colors || []).length }}</span>
        </button>
      </aside>

      <div class="colorpicker__grid">
        <button
          v-for="c in currentColors"
          :key="c.vid"
          type="button"
          class="colorpicker__item"
          :class="{ 'colorpicker__item--active': c.vid === modelValue }"
          @click="pick(c.vid)"
        >
          <span class="colorpicker__dot" :style="{ background: swatchOf(c.extendInfo) }" />
          <span class="colorpicker__iname">{{ c.value }}</span>
        </button>
      </div>
    </div>

    <div v-if="modelValue" class="colorpicker__foot">
      <el-button text size="small" @click="clear">清除选择</el-button>
    </div>
  </el-popover>
</template>

<style scoped>
.colorpicker__trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  padding: 5px 10px;
  font-size: 13px;
  background: #fff;
  border: 1px solid var(--border, #dcdfe6);
  border-radius: 4px;
  cursor: pointer;
}

.colorpicker__trigger:hover:not(:disabled) {
  border-color: var(--primary, #2563eb);
}

.colorpicker__trigger:disabled {
  color: var(--muted, #c0c4cc);
  cursor: not-allowed;
}

.colorpicker__dot {
  flex: none;
  width: 14px;
  height: 14px;
  border: 1px solid rgba(0, 0, 0, .1);
  border-radius: 50%;
}

.colorpicker__text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.colorpicker__count {
  flex: none;
  color: var(--muted, #9ca3af);
  font-size: 11px;
}

.colorpicker__panel {
  display: flex;
  gap: 10px;
  max-height: 340px;
}

.colorpicker__groups {
  flex: 0 0 118px;
  overflow-y: auto;
  border-right: 1px solid var(--border, #ebeef5);
}

.colorpicker__group {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 8px;
  font-size: 13px;
  text-align: left;
  background: transparent;
  border: 0;
  border-radius: 4px;
  cursor: pointer;
}

.colorpicker__group:hover,
.colorpicker__group--active {
  color: var(--primary, #2563eb);
  background: #eff6ff;
}

.colorpicker__gname {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.colorpicker__gcount {
  margin-left: auto;
  color: var(--muted, #9ca3af);
  font-size: 11px;
}

.colorpicker__grid {
  flex: 1 1 auto;
  display: grid;
  grid-template-columns: repeat(3, 104px);
  grid-auto-rows: 30px;
  gap: 2px 6px;
  align-content: start;
  justify-content: start;
  overflow-y: auto;
  padding-right: 4px;
}

.colorpicker__item {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 104px;
  height: 30px;
  padding: 0 8px;
  overflow: hidden;
  font-size: 13px;
  text-align: left;
  background: transparent;
  border: 0;
  border-radius: 4px;
  cursor: pointer;
}

.colorpicker__item:hover,
.colorpicker__item--active {
  color: var(--primary, #2563eb);
  background: #eff6ff;
}

.colorpicker__iname {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.colorpicker__foot {
  margin-top: 6px;
  text-align: right;
}
</style>
