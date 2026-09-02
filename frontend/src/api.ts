import axios from 'axios'
import type { ActivitySkuPreview, ActivitySkuRules, ActivityTaskItem, AppSettings, BulkActivityResult, HalfHeadcostItem, InventoryStatus, SkuResult, TaskItem, User } from './types'

const http = axios.create({
  baseURL: '/api',
  timeout: 15 * 1000,
})

const uploadHttp = axios.create({
  baseURL: '/api',
  timeout: 30 * 60 * 1000,
})

export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT') return '请求超时，请检查服务器和数据库连接'
    return error.response?.data?.detail || error.message
  }
  return error instanceof Error ? error.message : '请求失败'
}

export async function getMe() {
  const { data } = await http.get<User>('/auth/me')
  return data
}

export async function login(username: string, password: string) {
  const { data } = await http.post<User>('/auth/login', { username, password })
  return data
}

export async function register(username: string, password: string, displayName: string) {
  const { data } = await http.post<{ message: string }>('/auth/register', { username, password, display_name: displayName })
  return data
}

export async function logout() {
  await http.post('/auth/logout')
}

export async function getStatus() {
  const { data } = await http.get<{
    version: string
    inventory: InventoryStatus
    half_headcost_count: number
    tasks: TaskItem[]
  }>('/status')
  return data
}

export async function createTask(form: FormData) {
  const { data } = await uploadHttp.post<TaskItem>('/tasks', form)
  return data
}

export async function getTask(id: string) {
  const { data } = await http.get<TaskItem>(`/tasks/${id}`)
  return data
}

export async function getTasks(limit = 50) {
  const { data } = await http.get<{ items: TaskItem[] }>('/tasks', { params: { limit } })
  return data.items
}

export async function getMyActivityTasks(limit = 50) {
  const { data } = await http.get<{ items: ActivityTaskItem[] }>('/activities', { params: { limit } })
  return data.items
}

export async function deleteTask(id: string) {
  await http.delete(`/tasks/${encodeURIComponent(id)}`)
}

export function downloadUrl(id: string) {
  return `/api/tasks/${id}/download`
}

export async function querySkus(skus: string[]) {
  const { data } = await http.post<{ total: number; found: number; items: SkuResult[] }>(
    '/skus/query',
    { skus },
  )
  return data
}

export async function getInventory() {
  const { data } = await http.get<InventoryStatus>('/inventory')
  return data
}

export async function getInventoryItems(query = '', page = 1, pageSize = 30) {
  const { data } = await http.get<{ total: number; items: SkuResult[] }>('/inventory/items', {
    params: { query, page, page_size: pageSize },
  })
  return data
}

export async function uploadInventory(file: File) {
  const form = new FormData()
  form.append('file', file)
  const { data } = await uploadHttp.post<InventoryStatus & { message: string }>('/inventory', form)
  return data
}

export async function rebuildInventory() {
  const { data } = await uploadHttp.post<InventoryStatus & { message: string }>('/inventory/rebuild')
  return data
}

export async function getAdminInventory() {
  const { data } = await http.get<InventoryStatus>('/admin/inventory')
  return data
}

export async function getAdminInventoryItems(query = '', page = 1, pageSize = 30) {
  const { data } = await http.get<{ total: number; items: SkuResult[] }>('/admin/inventory/items', {
    params: { query, page, page_size: pageSize },
  })
  return data
}

export async function deleteInventoryItem(sku: string) {
  const { data } = await http.delete<InventoryStatus & { message: string; sku: string }>(`/admin/inventory/items/${encodeURIComponent(sku)}`)
  return data
}

export async function getHalfHeadcost(query = '', page = 1, pageSize = 30) {
  const { data } = await http.get<{ total: number; items: HalfHeadcostItem[] }>('/half-headcost', {
    params: { query, page, page_size: pageSize },
  })
  return data
}

export async function importHalfHeadcost(file: File) {
  const form = new FormData()
  form.append('file', file)
  const { data } = await uploadHttp.post<{ message: string; incoming: number; added: number; total: number }>(
    '/half-headcost/import',
    form,
  )
  return data
}

export async function deleteHalfHeadcost(sku: string) {
  await http.delete(`/half-headcost/${encodeURIComponent(sku)}`)
}

export async function previewActivitySkuRules(file: File, rules: ActivitySkuRules) {
  const form = new FormData()
  form.append('file', file)
  form.append('skc_rules', JSON.stringify(rules))
  const { data } = await uploadHttp.post<ActivitySkuPreview>('/activities/preview', form)
  return data
}

export async function processBulkActivity(file: File, upliftLimit?: number, rules?: ActivitySkuRules) {
  const form = new FormData()
  form.append('file', file)
  if (upliftLimit !== undefined) form.append('uplift_limit', String(upliftLimit))
  if (rules) form.append('skc_rules', JSON.stringify(rules))
  const { data } = await uploadHttp.post<ActivityTaskItem & Pick<BulkActivityResult, 'download_url'>>('/activities/bulk', form)
  return data
}

export async function getActivityTasks(limit = 50) {
  const { data } = await http.get<{ items: ActivityTaskItem[] }>('/activities', { params: { limit } })
  return data.items
}

export async function getActivityTask(id: string) {
  const { data } = await http.get<ActivityTaskItem>(`/activities/${encodeURIComponent(id)}`)
  return data
}

export async function deleteActivityTask(id: string) {
  await http.delete(`/activities/${encodeURIComponent(id)}`)
}

export async function getAdminTasks(limit = 100) {
  const { data } = await http.get<{ items: TaskItem[] }>('/admin/tasks', { params: { limit } })
  return data.items
}

export async function getAdminActivityTasks(limit = 100) {
  const { data } = await http.get<{ items: ActivityTaskItem[] }>('/admin/activity-tasks', { params: { limit } })
  return data.items
}

export function activityDownloadUrl(jobId: string) {
  return `/api/activities/${encodeURIComponent(jobId)}/download`
}

export async function getAdminUsers() {
  const { data } = await http.get<{ items: User[] }>('/admin/users')
  return data.items
}

export async function updateUserStatus(id: number, status: 'approve' | 'reject') {
  const { data } = await http.post<User>(`/admin/users/${id}/${status}`)
  return data
}

export async function updateAdminUser(id: number, username: string, password?: string) {
  const { data } = await http.patch<User>(`/admin/users/${id}`, {
    username,
    password: password || undefined,
  })
  return data
}

export async function deleteAdminUser(id: number) {
  await http.delete(`/admin/users/${id}`)
}

export async function getAdminSettings() {
  const { data } = await http.get<AppSettings>('/admin/settings')
  return data
}

export async function getSettings() {
  const { data } = await http.get<AppSettings>('/settings')
  return data
}

export async function saveAdminSettings(settings: AppSettings) {
  const { data } = await http.put<AppSettings>('/admin/settings', settings)
  return data
}
