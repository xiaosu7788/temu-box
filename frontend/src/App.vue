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
  Setting,
  UserFilled,
} from '@element-plus/icons-vue'
import { getMe, getStatus, logout } from './api'
import type { User } from './types'
import AuthView from './views/AuthView.vue'

const route = useRoute()
const router = useRouter()
const online = ref(false)
const mobileMenu = ref(false)
const authLoading = ref(true)
const user = ref<User | null>(null)
const pageTitle = computed(() => String(route.meta.title || '工作台'))

const menuItems = [
  { path: '/orders', label: '订单计算', icon: DataAnalysis },
  { path: '/inventory', label: '库存管理', icon: Box },
  { path: '/activities', label: '批量报名活动', icon: Calendar },
  { path: '/tasks', label: '任务记录', icon: Clock },
]

async function checkHealth() {
  if (!user.value) return
  try {
    await getStatus()
    online.value = true
  } catch {
    online.value = false
  }
}

async function bootstrap() {
  try { user.value = await getMe() } catch { user.value = null } finally { authLoading.value = false }
  if (user.value) checkHealth()
}

function authenticated(nextUser: User) {
  user.value = nextUser
  checkHealth()
}

async function signOut() {
  await logout()
  user.value = null
  router.replace('/orders')
}

function navigate(path: string) {
  router.push(path)
  mobileMenu.value = false
}

onMounted(bootstrap)
</script>

<template>
  <AuthView v-if="!authLoading && !user" @authenticated="authenticated" />
  <div v-else-if="user" class="app-shell">
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
        <button v-if="user.role === 'admin'" class="nav-item" :class="{ active: route.path === '/admin' }" type="button" @click="navigate('/admin')"><el-icon><Setting /></el-icon><span>后台管理</span></button>
      </nav>
      <div class="sidebar-status">
        <span class="status-dot" :class="{ online }" />
        <span>{{ online ? 'API 正常' : 'API 离线' }}</span>
      </div>
      <div class="sidebar-user"><el-icon><UserFilled /></el-icon><span>{{ user.display_name || user.username }}</span><el-button text type="info" @click="signOut">退出</el-button></div>
    </aside>

    <div class="workspace">
      <header class="topbar">
        <el-button class="menu-button" :icon="MenuIcon" circle @click="mobileMenu = !mobileMenu" />
        <div>
          <h1>{{ pageTitle }}</h1>
          <p>销售订单货值/成本计算工具</p>
        </div>
        <el-tag v-if="user.role === 'admin'" class="role-tag" type="warning">管理员</el-tag>
      </header>
      <main class="page-content" :class="{ 'page-content--fixed': route.path === '/inventory' }">
        <router-view />
      </main>
    </div>
    <button v-if="mobileMenu" class="sidebar-backdrop" type="button" @click="mobileMenu = false" />
  </div>
</template>
