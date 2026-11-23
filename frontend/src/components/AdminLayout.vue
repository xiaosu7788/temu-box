<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowLeft,
  ArrowRight,
  Box,
  ChatLineSquare,
  Clock,
  Document,
  Goods,
  Location,
  Menu as MenuIcon,
  Monitor,
  PriceTag,
  Setting,
  SetUp,
  Shop,
  Tickets,
  Tools,
  Upload,
  UserFilled,
} from '@element-plus/icons-vue'
import type { Component } from 'vue'
import type { User } from '../types'
import { getVersion } from '../api'
import VersionDialog from './VersionDialog.vue'

const props = defineProps<{ user: User; online?: boolean }>()
const emit = defineEmits<{ 'sign-out': [] }>()

const route = useRoute()
const router = useRouter()
const mobileMenu = ref(false)

const showVersion = ref(false)
const version = ref('')

const pageTitle = computed(() => String(route.meta.title || '后台管理'))
const inventoryPage = computed(() => route.path === '/admin/inventory')

onMounted(async () => {
  try {
    version.value = (await getVersion()).version
  } catch {
    version.value = ''
  }
})

type NavLeaf = { path: string; label: string; icon: Component }
type NavGroup = { key: string; label: string; icon: Component; children: NavLeaf[] }
type NavEntry = NavLeaf | NavGroup

const isGroup = (item: NavEntry): item is NavGroup => 'children' in item

const navItems: NavEntry[] = [
  { path: '/admin/monitoring', label: '系统监控', icon: Monitor },
  { path: '/admin/users', label: '用户管理', icon: UserFilled },
  { path: '/admin/regions', label: '区域设置', icon: Location },
  { path: '/admin/categories', label: '品类管理', icon: Goods },
  { path: '/admin/settings', label: '成本参数', icon: SetUp },
  { path: '/admin/activity-settings', label: '批量报活动设置', icon: PriceTag },
  { path: '/admin/inventory', label: '库存管理', icon: Box },
  { path: '/admin/tasks', label: '任务记录', icon: Clock },
  {
    key: 'store',
    label: '店铺管理',
    icon: Shop,
    children: [
      { path: '/admin/store-users', label: '用户管理', icon: UserFilled },
      { path: '/admin/store-center', label: '日志与审计', icon: Tickets },
    ],
  },
  { path: '/admin/system', label: '系统设置', icon: Tools },
]

/** 可展开分组：默认带上当前路由所在的分组 */
const expandedGroups = ref<string[]>(['store'])

function toggleGroup(key: string) {
  expandedGroups.value = expandedGroups.value.includes(key)
    ? expandedGroups.value.filter((item) => item !== key)
    : [...expandedGroups.value, key]
}

function groupActive(group: NavGroup): boolean {
  return group.children.some((child) => route.path === child.path)
}

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
        <template v-for="item in navItems" :key="isGroup(item) ? item.key : item.path">
          <template v-if="isGroup(item)">
            <button
              class="nav-item nav-item--group"
              :class="{ active: groupActive(item) }"
              type="button"
              @click="toggleGroup(item.key)"
            >
              <el-icon><component :is="item.icon" /></el-icon>
              <span>{{ item.label }}</span>
              <el-icon class="nav-arrow" :class="{ open: expandedGroups.includes(item.key) }">
                <ArrowRight />
              </el-icon>
            </button>
            <div v-show="expandedGroups.includes(item.key)" class="nav-children">
              <button
                v-for="child in item.children"
                :key="child.path"
                class="nav-child"
                :class="{ active: route.path === child.path }"
                type="button"
                @click="navigate(child.path)"
              >
                <el-icon><component :is="child.icon" /></el-icon>
                <span>{{ child.label }}</span>
              </button>
            </div>
          </template>
          <button
            v-else
            class="nav-item"
            :class="{ active: route.path === item.path }"
            type="button"
            @click="navigate(item.path)"
          >
            <el-icon><component :is="item.icon" /></el-icon>
            <span>{{ item.label }}</span>
          </button>
        </template>
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
          <span v-if="version" class="version-chip">v{{ version }}</span>
          <el-button size="small" :icon="Upload" @click="showVersion = true">版本更新</el-button>
          <el-tag class="role-tag" type="warning">管理员</el-tag>
        </template>
      </header>
      <main class="page-content" :class="{ 'page-content--fixed': inventoryPage, 'page-content--admin-inventory': inventoryPage }">
        <slot />
      </main>
    </div>
    <button v-if="mobileMenu" class="sidebar-backdrop" type="button" @click="mobileMenu = false" />
    <VersionDialog v-model="showVersion" />
  </div>
</template>
