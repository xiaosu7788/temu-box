/** 当前用户的模块权限快照（供路由守卫使用，App.vue 登录后写入）。 */

import { ref } from 'vue'

export const ALL_MODULES = ['orders', 'inventory', 'activities', 'tasks', 'store'] as const
export const ALL_STORE_MODULES = ['goods', 'create', 'kefu', 'actions'] as const

export const allowedModules = ref<string[]>([])
export const allowedStoreModules = ref<string[]>([])
export const isAdminUser = ref(false)

export function applyPermissions(user: {
  role?: string
  modules?: string[]
  store_modules?: string[]
} | null) {
  if (!user) {
    allowedModules.value = []
    allowedStoreModules.value = []
    isAdminUser.value = false
    return
  }
  const admin = user.role === 'admin'
  isAdminUser.value = admin
  if (admin) {
    allowedModules.value = [...ALL_MODULES]
    allowedStoreModules.value = [...ALL_STORE_MODULES]
    return
  }
  allowedModules.value = Array.isArray(user.modules) ? user.modules : []
  const sm = Array.isArray(user.store_modules) ? user.store_modules : []
  // 有 store 权限但没配二级时默认全开（与后端一致）
  allowedStoreModules.value = sm.length
    ? sm
    : allowedModules.value.includes('store')
      ? [...ALL_STORE_MODULES]
      : []
}

/** 路径 → 需要的一级模块 */
const PATH_MODULE: Array<[RegExp, string]> = [
  [/^\/orders/, 'orders'],
  [/^\/inventory/, 'inventory'],
  [/^\/activities/, 'activities'],
  [/^\/tasks/, 'tasks'],
  [/^\/store/, 'store'],
]

/** 店铺路径 → 需要的二级模块 */
const STORE_PATH_MODULE: Array<[RegExp, string]> = [
  [/^\/store\/goods/, 'goods'],
  [/^\/store\/create/, 'create'],
  [/^\/store\/kefu/, 'kefu'],
  [/^\/store\/actions/, 'actions'],
]

export function firstAllowedPath(): string {
  const map: Record<string, string> = {
    orders: '/orders',
    inventory: '/inventory',
    activities: '/activities',
    tasks: '/tasks',
    store: '/store/goods',
  }
  for (const key of ALL_MODULES) {
    if (allowedModules.value.includes(key)) {
      if (key === 'store') {
        const sub = ALL_STORE_MODULES.find((s) => allowedStoreModules.value.includes(s))
        if (sub) return map.store.replace('goods', sub)
        continue
      }
      return map[key]
    }
  }
  return '/'
}

/** 该路径当前用户能否访问 */
export function canVisit(path: string): boolean {
  if (isAdminUser.value) return true
  const hit = PATH_MODULE.find(([re]) => re.test(path))
  if (!hit) return true
  if (!allowedModules.value.includes(hit[1])) return false
  if (hit[1] !== 'store') return true
  const sub = STORE_PATH_MODULE.find(([re]) => re.test(path))
  if (!sub) return true
  return allowedStoreModules.value.includes(sub[1])
}
