import { computed, ref } from 'vue'
import axios from 'axios'
import type { User } from './types'
import type { StoreSession } from './types'
import { getStoreSession } from './api'

/**
 * 店铺管理模块的全局状态。
 *
 * 两层权限要分清：
 *  - `storeFeatureEnabled`：模块准入（管理员按用户开通），决定侧栏是否有入口；
 *  - `storeSession`：该用户当前登录的店铺（一用户一店铺），决定页面能做什么。
 */
export const storeFeatureEnabled = ref(false)
export const storeSession = ref<StoreSession | null>(null)

/** 用 /api/auth/me 的返回值同步模块准入（管理员恒为 true） */
export function applyStoreFeature(user: User | null): void {
  storeFeatureEnabled.value = !!user && (user.role === 'admin' || !!user.store_access)
  if (!storeFeatureEnabled.value) storeSession.value = null
}

export async function refreshStoreSession(): Promise<StoreSession | null> {
  if (!storeFeatureEnabled.value) {
    storeSession.value = null
    return null
  }
  try {
    storeSession.value = await getStoreSession()
  } catch (error) {
    // 403 = 管理员中途收回了模块权限：立刻关掉入口，页面据 storeFeatureEnabled 跳回工作台
    if (axios.isAxiosError(error) && error.response?.status === 403) {
      storeFeatureEnabled.value = false
    }
    storeSession.value = null
  }
  return storeSession.value
}

/**
 * 把本地会话标记为失效：页面据此立刻回落到登录卡。
 * 由两处触发：api 层的 401 拦截、切页时的主动探测。
 */
export function markStoreSessionInvalid(reason: string): void {
  const current = storeSession.value
  if (!current) return
  if (current.status === 'invalid') return
  storeSession.value = {
    ...current,
    status: 'invalid',
    last_error: reason || '店铺会话已失效，请重新登录',
    expires_in_seconds: 0,
  }
}

// api.ts 的响应拦截器发现 /store/* 返回 401 时派发，避免两个模块循环 import
if (typeof window !== 'undefined') {
  window.addEventListener('store:session-invalid', (event) => {
    const detail = (event as CustomEvent<string>).detail
    markStoreSessionInvalid(detail)
  })
}

export const storeLoggedIn = computed(() => storeSession.value?.status === 'active')
export const storeBridgeReady = computed(() => storeSession.value?.bridge_configured !== false)
