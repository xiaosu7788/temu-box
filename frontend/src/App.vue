<script setup lang="ts">
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { Component } from 'vue'
import {
  ArrowRight,
  Box,
  Calendar,
  Clock,
  Collection,
  DataAnalysis,
  Menu as MenuIcon,
  Setting,
  Shop,
  UserFilled,
} from '@element-plus/icons-vue'
import { getMe, getStatus, logout } from './api'
import type { User } from './types'
import { applyPermissions, allowedModules as permModules, allowedStoreModules as permStoreModules } from './permissions'
import AuthView from './views/AuthView.vue'
import RegionPicker from './components/RegionPicker.vue'
import CategoryPicker from './components/CategoryPicker.vue'
import AdminLayout from './components/AdminLayout.vue'
import FloatingTaskCenter from './components/FloatingTaskCenter.vue'
import { selectedCategoryCode, selectedRegionCode } from './regionState'
import { applyStoreFeature, storeFeatureEnabled } from './storeState'

const route = useRoute()
const router = useRouter()
const online = ref(false)
const mobileMenu = ref(false)
const authLoading = ref(true)
const user = ref<User | null>(null)
const pageTitle = computed(() => String(route.meta.title || '工作台'))
const fixedDataPage = computed(() => route.path === '/inventory')
const showRegionPicker = computed(() => route.path === '/orders' || route.path === '/activities')
const inventoryPage = computed(() => route.path === '/inventory')
// 后台管理是独立页面：/admin 路由下整体切换为 AdminLayout 外壳
const isAdminRoute = computed(() => route.path.startsWith('/admin'))

interface NavChild {
  path: string
  label: string
}

interface NavItem {
  path: string
  label: string
  icon?: Component
  children?: NavChild[]
}

// 权限的唯一真值在 permissions.ts（路由守卫也用同一份）
const allowedModules = permModules
const allowedStoreModules = permStoreModules

const baseMenuItems: NavItem[] = [
  { path: '/orders', label: '订单计算', icon: DataAnalysis },
  { path: '/inventory', label: '库存管理', icon: Box },
  { path: '/activities', label: '批量报名活动', icon: Calendar },
  { path: '/tasks', label: '任务记录', icon: Clock },
]

// 店铺管理入口按模块准入动态渲染（管理员在「用户管理」里逐个开通）；
// 开通后是「我的店铺」可展开分组，子菜单对应三个二级页面。
const menuItems = computed<NavItem[]>(() => {
  const [orders, inventory, activities, tasks] = baseMenuItems
  // 上一版这里有个 `if (!storeFeatureEnabled.value) return baseMenuItems` 的提前返回，
  // 会把权限过滤整个绕过（店铺功能一关就是全员全菜单）。店铺开关只影响店铺分组。
  const allowed = allowedModules.value
  const storeAllowed = allowedStoreModules.value

  const storeChildren = [
    { path: '/store/goods', label: '商品查询', key: 'goods' },
    { path: '/store/create', label: '创建商品', key: 'create' },
    { path: '/store/kefu', label: '客服自助', key: 'kefu' },
    { path: '/store/actions', label: '操作记录', key: 'actions' },
  ]
    .filter((x) => storeAllowed.includes(x.key))
    .map(({ path, label }) => ({ path, label }))

  const entries: NavItem[] = []
  if (allowed.includes('orders')) entries.push(orders)
  if (allowed.includes('inventory')) entries.push(inventory)
  if (allowed.includes('activities')) entries.push(activities)
  if (storeFeatureEnabled.value && allowed.includes('store') && storeChildren.length) {
    entries.push({ path: '/store', label: '店铺管理', icon: Shop, children: storeChildren })
  }
  if (allowed.includes('tasks')) entries.push(tasks)
  return entries
})

const expandedGroups = ref<string[]>([])

function isGroupOpen(path: string): boolean {
  return expandedGroups.value.includes(path)
}

function toggleGroup(path: string): void {
  expandedGroups.value = isGroupOpen(path)
    ? expandedGroups.value.filter((item) => item !== path)
    : [...expandedGroups.value, path]
}

function isActive(item: NavItem): boolean {
  return item.children ? route.path.startsWith(item.path) : route.path === item.path
}

// 进入店铺任一子页时自动展开分组
watch(
  () => route.path,
  (path) => {
    if (path.startsWith('/store') && !isGroupOpen('/store')) {
      expandedGroups.value = [...expandedGroups.value, '/store']
    }
  },
  { immediate: true },
)

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
  applyStoreFeature(user.value)
  applyPermissions(user.value)
  if (user.value) checkHealth()
}

function authenticated(nextUser: User) {
  user.value = nextUser
  applyStoreFeature(nextUser)
  applyPermissions(nextUser)
  checkHealth()
}

async function signOut() {
  await logout()
  user.value = null
  applyPermissions(null)
  applyStoreFeature(null)
  router.replace('/orders')
}

function navigate(path: string) {
  router.push(path)
  mobileMenu.value = false
}

onMounted(bootstrap)
</script>

<template>
  <el-config-provider :locale="zhCn">
  <AuthView v-if="!authLoading && !user" @authenticated="authenticated" />
  <AdminLayout v-else-if="user && isAdminRoute" :user="user" :online="online" @sign-out="signOut">
    <router-view />
  </AdminLayout>
  <div v-else-if="user" class="app-shell">
    <aside class="sidebar" :class="{ open: mobileMenu }">
      <div class="brand">
        <div class="brand-mark"><el-icon><Collection /></el-icon></div>
        <div>
          <strong>Temu-Box</strong>
          <span>Operations Console</span>
        </div>
      </div>
      <nav class="nav-list">
        <template v-for="item in menuItems" :key="item.path">
          <button
            v-if="!item.children"
            class="nav-item"
            :class="{ active: isActive(item) }"
            type="button"
            @click="navigate(item.path)"
          >
            <el-icon><component :is="item.icon" /></el-icon>
            <span>{{ item.label }}</span>
          </button>

          <template v-else>
            <button
              class="nav-item nav-item--group"
              :class="{ active: isActive(item) }"
              type="button"
              :aria-expanded="isGroupOpen(item.path)"
              @click="toggleGroup(item.path)"
            >
              <el-icon><component :is="item.icon" /></el-icon>
              <span class="nav-item__label">{{ item.label }}</span>
              <el-icon class="nav-caret" :class="{ 'is-open': isGroupOpen(item.path) }"><ArrowRight /></el-icon>
            </button>
            <div v-show="isGroupOpen(item.path)" class="nav-children">
              <button
                v-for="child in item.children"
                :key="child.path"
                class="nav-item nav-item--child"
                :class="{ active: route.path === child.path }"
                type="button"
                @click="navigate(child.path)"
              >
                <span class="nav-item__label">{{ child.label }}</span>
              </button>
            </div>
          </template>
        </template>
      </nav>
      <div class="sidebar-footer">
        <button v-if="user.role === 'admin'" class="nav-item sidebar-admin" :class="{ active: route.path.startsWith('/admin') }" type="button" @click="navigate('/admin')">
          <el-icon><Setting /></el-icon><span>后台管理</span>
        </button>
        <div class="sidebar-status">
          <span class="status-dot" :class="{ online }" />
          <span>{{ online ? 'API 正常' : 'API 离线' }}</span>
        </div>
        <div class="sidebar-user"><el-icon><UserFilled /></el-icon><span>{{ user.display_name || user.username }}</span><el-button text type="info" @click="signOut">退出</el-button></div>
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
            <p>Temu-Box</p>
          </div>
          <RegionPicker v-if="showRegionPicker" v-model="selectedRegionCode" compact />
          <CategoryPicker v-if="showRegionPicker" v-model="selectedCategoryCode" compact filter-by-region />
        </template>
        <div id="store-topbar-target" class="store-topbar-target"></div>
        <el-tag v-if="user.role === 'admin' && !inventoryPage" class="role-tag" type="warning">管理员</el-tag>
      </header>
      <main class="page-content" :class="{ 'page-content--fixed': fixedDataPage, 'page-content--inventory': inventoryPage }">
        <!-- 页面本身按权限渲染，不再整页替换（无权限的地址由路由守卫处理） -->
        <router-view />
      </main>
    </div>
    <button v-if="mobileMenu" class="sidebar-backdrop" type="button" @click="mobileMenu = false" />
  </div>
  <FloatingTaskCenter v-if="user" />
  </el-config-provider>
</template>

<style scoped>

.store-topbar-target {
  display: flex;
  align-items: center;
  min-width: 0;
  margin-left: auto;
}

/* 「我的店铺」可展开分组：父项占满宽度并带右向箭头，子项缩进显示 */
.nav-item--group .nav-item__label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nav-caret {
  font-size: 13px !important;
  opacity: 0.7;
  transition: transform 0.18s ease;
}

.nav-caret.is-open {
  transform: rotate(90deg);
}

.nav-children {
  display: grid;
  gap: 3px;
  margin: 2px 0 4px 22px;
  padding-left: 8px;
  border-left: 1px solid #2b3543;
}

.nav-item--child {
  height: 34px;
  padding: 0 10px;
  font-size: 13px;
  color: #98a2b3;
}

.nav-item--child:hover {
  color: #fff;
}


</style>
