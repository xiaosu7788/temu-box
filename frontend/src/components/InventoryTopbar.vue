<script setup lang="ts">
/**
 * 库存页顶栏内容（用户端与后台共用）。
 *
 * 两个布局各自提供 Teleport 目标 `#inventory-topbar-target`，本组件只负责内部排版：
 * 左侧标题/摘要 + 右侧操作区（通过默认插槽传入按钮与选择器）。
 * 用 flex 布局替代原先写死的 `inset: 0 300px`，按钮增减不再互相挤压。
 */
interface TopbarSummaryItem {
  label: string
  value: string | number
}

withDefaults(
  defineProps<{
    title?: string
    subtitle?: string
    admin?: boolean
    summary?: TopbarSummaryItem[]
  }>(),
  {
    title: '库存管理',
    subtitle: '',
    admin: false,
    summary: () => [],
  },
)
</script>

<template>
  <div class="inventory-topbar">
    <div class="inventory-topbar-copy">
      <div class="inventory-title-line">
        <h1>{{ title }}</h1>
        <el-tag v-if="admin" size="small" type="warning">管理员</el-tag>
        <span v-if="subtitle" class="inventory-title-subtitle">{{ subtitle }}</span>
      </div>
      <div v-if="summary.length" class="inventory-topbar-summary">
        <span v-for="item in summary" :key="item.label">
          {{ item.label }} <strong>{{ item.value }}</strong>
        </span>
      </div>
    </div>
    <div class="inventory-topbar-actions">
      <slot />
    </div>
  </div>
</template>
