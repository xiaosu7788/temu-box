<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Box,
  Calendar,
  Clock,
  Collection,
  DataAnalysis,
  Menu as MenuIcon,
} from '@element-plus/icons-vue'
import { getStatus } from './api'

const route = useRoute()
const router = useRouter()
const online = ref(false)
const mobileMenu = ref(false)
const pageTitle = computed(() => String(route.meta.title || '工作台'))

const menuItems = [
  { path: '/orders', label: '订单计算', icon: DataAnalysis },
  { path: '/inventory', label: '库存管理', icon: Box },
  { path: '/activities', label: '批量报名活动', icon: Calendar },
  { path: '/tasks', label: '任务记录', icon: Clock },
]

async function checkHealth() {
  try {
    await getStatus()
    online.value = true
  } catch {
    online.value = false
  }
}

function navigate(path: string) {
  router.push(path)
  mobileMenu.value = false
}

onMounted(checkHealth)
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar" :class="{ open: mobileMenu }">
      <div class="brand">
        <div class="brand-mark"><el-icon><Collection /></el-icon></div>
        <div>
          <strong>销售成本工具</strong>
          <span>Operations Console</span>
        </div>
      </div>
      <nav class="nav-list">
        <button
          v-for="item in menuItems"
          :key="item.path"
          class="nav-item"
          :class="{ active: route.path === item.path }"
          type="button"
          @click="navigate(item.path)"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </button>
      </nav>
      <div class="sidebar-status">
        <span class="status-dot" :class="{ online }" />
        <span>{{ online ? 'API 正常' : 'API 离线' }}</span>
      </div>
    </aside>

    <div class="workspace">
      <header class="topbar">
        <el-button class="menu-button" :icon="MenuIcon" circle @click="mobileMenu = !mobileMenu" />
        <div>
          <h1>{{ pageTitle }}</h1>
          <p>销售订单货值/成本计算工具</p>
        </div>
      </header>
      <main class="page-content" :class="{ 'page-content--fixed': route.path === '/inventory' }">
        <router-view />
      </main>
    </div>
    <button v-if="mobileMenu" class="sidebar-backdrop" type="button" @click="mobileMenu = false" />
  </div>
</template>
