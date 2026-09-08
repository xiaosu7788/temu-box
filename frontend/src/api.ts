import axios from 'axios'
import type { ActivityIdProfitRule, ActivitySkuPreview, ActivitySkuRules, ActivityTaskItem, AppSettings, AuditLogPage, BulkActivityResult, CategoryPage, CategorySummary, CleanupResult, HalfHeadcostItem, InventoryStatus, MonitoringSnapshot, RegionProfile, RegionSummary, SkuResult, SystemSettings, SystemSettingsInfo, TaskItem, User } from './types'

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

export async function getRegions() {
  const { data } = await http.get<{ items: RegionSummary[] }>('/regions')
  return data.items
}

export async function getCategories(regionCode?: string) {
  const { data } = await http.get<{ items: CategorySummary[] }>('/categories', { params: regionCode ? { region_code: regionCode } : undefined })
  return data.items
}

export async function getRegionSettings(code: string, categoryCode?: string) {
  const { data } = await http.get<RegionProfile>(`/regions/${encodeURIComponent(code)}/settings`, { params: { category_code: categoryCode } })
  return data
}

export async function createTask(form: FormData, regionCode: string, categoryCode?: string) {
  form.append('region_code', regionCode)
  if (categoryCode) form.append('category_code', categoryCode)
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

export async function createInventoryItem(payload: { sku: string; price: number | null; set_type: string }) {
  const { data } = await http.post<{ item: SkuResult }>('/admin/inventory/items', payload)
  return data.item
}

export async function updateInventoryItem(oldSku: string, payload: { sku: string; price: number | null; set_type: string }) {
  const { data } = await http.put<{ item: SkuResult }>(`/admin/inventory/items/${encodeURIComponent(oldSku)}`, payload)
  return data.item
}
export async function deleteInventoryItem(sku: string) {
  const { data } = await http.delete<InventoryStatus & { message: string; sku: string }>(`/admin/inventory/items/${encodeURIComponent(sku)}`)
  return data
}

export async function getHalfHeadcost(query = '', page = 1, pageSize = 30, categoryCode?: string) {
  const { data } = await http.get<{ total: number; items: HalfHeadcostItem[]; category?: { code: string; name: string } }>('/half-headcost', {
    params: { query, page, page_size: pageSize, category_code: categoryCode },
  })
  return data
}

export async function importHalfHeadcost(file: File, categoryCode?: string) {
  const form = new FormData()
  form.append('file', file)
  if (categoryCode) form.append('category_code', categoryCode)
  const { data } = await uploadHttp.post<{ message: string; incoming: number; added: number; total: number }>(
    '/half-headcost/import',
    form,
  )
  return data
}

export async function deleteHalfHeadcost(sku: string, categoryCode?: string) {
  await http.delete(`/half-headcost/${encodeURIComponent(sku)}`, { params: { category_code: categoryCode } })
}

export async function previewActivitySkuRules(file: File, rules: ActivitySkuRules | undefined, regionCode: string, idProfitRules?: ActivityIdProfitRule[], categoryCode?: string) {
  const form = new FormData()
  form.append('file', file)
  if (rules) form.append('skc_rules', JSON.stringify(rules))
  if (idProfitRules) form.append('id_profit_rules', JSON.stringify(idProfitRules))
  form.append('region_code', regionCode)
  if (categoryCode) form.append('category_code', categoryCode)
  const { data } = await uploadHttp.post<ActivitySkuPreview>('/activities/preview', form)
  return data
}

export async function processBulkActivity(file: File, regionCode: string, upliftLimit?: number, rules?: ActivitySkuRules, idProfitRules?: ActivityIdProfitRule[], categoryCode?: string) {
  const form = new FormData()
  form.append('file', file)
  form.append('region_code', regionCode)
  if (categoryCode) form.append('category_code', categoryCode)
  if (upliftLimit !== undefined) form.append('uplift_limit', String(upliftLimit))
  if (rules) form.append('skc_rules', JSON.stringify(rules))
  if (idProfitRules) form.append('id_profit_rules', JSON.stringify(idProfitRules))
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

export async function deleteAdminTask(id: string) {
  await http.delete(`/admin/tasks/${encodeURIComponent(id)}`)
}

export async function deleteAdminActivityTask(id: string) {
  await http.delete(`/admin/activity-tasks/${encodeURIComponent(id)}`)
}

export function adminDownloadUrl(id: string) {
  return `/api/admin/tasks/${encodeURIComponent(id)}/download`
}

export function adminActivityDownloadUrl(jobId: string) {
  return `/api/admin/activity-tasks/${encodeURIComponent(jobId)}/download`
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

export async function getAdminSettings(regionCode?: string, categoryCode?: string) {
  const { data } = await http.get<AppSettings>('/admin/settings', { params: { region_code: regionCode, category_code: categoryCode } })
  return data
}

export async function getSettings(regionCode?: string, categoryCode?: string) {
  const { data } = await http.get<AppSettings>('/settings', { params: { region_code: regionCode, category_code: categoryCode } })
  return data
}

export async function saveAdminSettings(settings: AppSettings, regionCode?: string, categoryCode?: string) {
  const { data } = await http.put<AppSettings>('/admin/settings', settings, { params: { region_code: regionCode, category_code: categoryCode } })
  return data
}

export async function getAdminCategories() {
  const { data } = await http.get<CategoryPage>('/admin/categories')
  return data
}

export async function createAdminCategory(payload: { code: string; name: string; template_type: string; set_types?: number[]; allowed_regions?: string[] | null; sort_order?: number }) {
  const { data } = await http.post<CategorySummary>('/admin/categories', payload)
  return data
}

export async function updateAdminCategory(code: string, payload: { name: string; template_type: string; set_types?: number[]; allowed_regions?: string[] | null; enabled?: boolean; is_default?: boolean; sort_order?: number }) {
  const { data } = await http.put<CategorySummary>(`/admin/categories/${encodeURIComponent(code)}`, payload)
  return data
}

export async function deleteAdminCategory(code: string) {
  await http.delete(`/admin/categories/${encodeURIComponent(code)}`)
}

export async function getAdminActivitySkuRules(categoryCode?: string) {
  const { data } = await http.get<ActivitySkuRules>('/admin/activity-settings/skc-rules', { params: { category_code: categoryCode } })
  return data
}

export async function saveAdminActivitySkuRules(rules: ActivitySkuRules, categoryCode?: string) {
  const { data } = await http.put<ActivitySkuRules>('/admin/activity-settings/skc-rules', rules, { params: { category_code: categoryCode } })
  return data
}

export async function getAdminRegions() {
  const { data } = await http.get<{ items: RegionSummary[] }>('/admin/regions')
  return data.items
}

export async function getAdminRegion(code: string, categoryCode?: string) {
  const { data } = await http.get<RegionProfile>(`/admin/regions/${encodeURIComponent(code)}`, { params: { category_code: categoryCode } })
  return data
}

export async function createAdminRegion(payload: Pick<RegionSummary, 'code' | 'name' | 'currency' | 'sort_order'> & { copy_from?: string }) {
  const { data } = await http.post<RegionProfile>('/admin/regions', payload)
  return data
}

export async function saveAdminRegion(code: string, profile: RegionProfile, categoryCode?: string) {
  const { data } = await http.put<RegionProfile>(`/admin/regions/${encodeURIComponent(code)}`, profile, { params: { category_code: categoryCode } })
  return data
}

export async function deleteAdminRegion(code: string) {
  await http.delete(`/admin/regions/${encodeURIComponent(code)}`)
}

export async function getAdminSystemSettings() {
  const { data } = await http.get<SystemSettingsInfo>('/admin/system-settings')
  return data
}

export async function saveAdminSystemSettings(settings: SystemSettings) {
  const { data } = await http.put<{ settings: SystemSettings; pools: SystemSettingsInfo['pools'] }>('/admin/system-settings', settings)
  return data
}

export async function getAdminAuditLogs(params: { page: number; page_size: number; action?: string; keyword?: string }) {
  const { data } = await http.get<AuditLogPage>('/admin/audit-logs', { params })
  return data
}

export async function getAdminMonitoring() {
  const { data } = await http.get<MonitoringSnapshot>('/admin/monitoring')
  return data
}

export async function runAdminCleanup() {
  const { data } = await http.post<CleanupResult>('/admin/maintenance/cleanup')
  return data
}

export async function runAdminPurge() {
  const { data } = await http.post<CleanupResult>('/admin/maintenance/purge')
  return data
}
