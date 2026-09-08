<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowLeft,
  Box,
  Clock,
  Document,
  Goods,
  Location,
  Menu as MenuIcon,
  Monitor,
  PriceTag,
  Setting,
  SetUp,
  Tools,
  UserFilled,
} from '@element-plus/icons-vue'
import type { User } from '../types'

const props = defineProps<{ user: User; online?: boolean }>()
const emit = defineEmits<{ 'sign-out': [] }>()

const route = useRoute()
const router = useRouter()
const mobileMenu = ref(false)

const pageTitle = computed(() => String(route.meta.title || '后台管理'))
const inventoryPage = computed(() => route.path === '/admin/inventory')

const navItems = [
  { path: '/admin/monitoring', label: '系统监控', icon: Monitor },
  { path: '/admin/users', label: '用户管理', icon: UserFilled },
  { path: '/admin/regions', label: '区域设置', icon: Location },
  { path: '/admin/categories', label: '品类管理', icon: Goods },
  { path: '/admin/settings', label: '成本参数', icon: SetUp },
  { path: '/admin/activity-settings', label: '批量报活动设置', icon: PriceTag },
  { path: '/admin/inventory', label: '库存管理', icon: Box },
  { path: '/admin/tasks', label: '任务记录', icon: Clock },
  { path: '/admin/system', label: '系统设置', icon: Tools },
  { path: '/admin/audit-logs', label: '审计日志', icon: Document },
]

function navigate(path: string) {
  router.push(path)
  mobileMenu.value = false
}

function backToWorkspace() {
  router.push('/orders')
  mobileMenu.value = false
}
</script>

<template>
  <div class="app-shell admin-shell">
    <aside class="sidebar" :class="{ open: mobileMenu }">
      <div class="brand">
        <div class="brand-mark"><el-icon><Setting /></el-icon></div>
        <div>
          <strong>后台管理</strong>
          <span>Admin Console</span>
        </div>
      </div>
      <nav class="nav-list admin-nav-list">
        <button
          v-for="item in navItems"
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
      <div class="sidebar-footer">
        <button class="nav-item admin-back" type="button" @click="backToWorkspace">
          <el-icon><ArrowLeft /></el-icon><span>返回工作台</span>
        </button>
        <div class="sidebar-status">
          <span class="status-dot" :class="{ online: props.online }" />
          <span>{{ props.online ? 'API 正常' : 'API 离线' }}</span>
        </div>
        <div class="sidebar-user">
          <el-icon><UserFilled /></el-icon>
          <span>{{ props.user.display_name || props.user.username }}</span>
          <el-button text type="info" @click="emit('sign-out')">退出</el-button>
        </div>
      </div>
    </aside>

    <div class="workspace">
      <header class="topbar" :class="{ 'topbar--inventory': inventoryPage }">
        <el-button class="menu-button" :icon="MenuIcon" circle @click="mobileMenu = !mobileMenu" />
        <template v-if="inventoryPage">
          <div id="inventory-topbar-target" class="inventory-topbar-target"></div>
        </template>
        <template v-else>
          <div class="topbar-title">
            <h1>{{ pageTitle }}</h1>
            <p>Temu-Box 管理控制台</p>
          </div>
          <el-tag class="role-tag" type="warning">管理员</el-tag>
        </template>
      </header>
      <main class="page-content" :class="{ 'page-content--fixed': inventoryPage, 'page-content--admin-inventory': inventoryPage }">
        <slot />
      </main>
    </div>
    <button v-if="mobileMenu" class="sidebar-backdrop" type="button" @click="mobileMenu = false" />
  </div>
</template>
