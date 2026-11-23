<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AdminAuditLogs from './AdminAuditLogs.vue'
import AdminKefuActions from './AdminKefuActions.vue'
import AdminStoreLogs from './AdminStoreLogs.vue'

/** 店铺日志与审计：三个来源合到一个模块，内部用 tab 切换 */
const route = useRoute()
const router = useRouter()

const TABS = ['logs', 'kefu', 'audit'] as const
type TabKey = (typeof TABS)[number]

const active = computed<TabKey>({
  get() {
    const q = String(route.query.tab || '')
    return (TABS as readonly string[]).includes(q) ? (q as TabKey) : 'logs'
  },
  set(v) {
    router.replace({ query: { ...route.query, tab: v } })
  },
})

// 直接访问 /admin/store-logs 等旧地址时，query 已带 tab，无需额外处理
watch(() => route.query.tab, () => { /* computed 已响应 */ }, { immediate: true })
</script>

<template>
  <div class="store-center">
    <el-tabs v-model="active" class="store-center__tabs">
      <el-tab-pane label="登录日志" name="logs">
        <AdminStoreLogs />
      </el-tab-pane>
      <el-tab-pane label="客服操作审计" name="kefu">
        <AdminKefuActions />
      </el-tab-pane>
      <el-tab-pane label="系统审计" name="audit">
        <AdminAuditLogs />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.store-center {
  display: flex;
  flex-direction: column;
}

.store-center__tabs :deep(.el-tabs__header) {
  margin-bottom: 12px;
}
</style>
