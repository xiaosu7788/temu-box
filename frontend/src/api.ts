import axios from 'axios'
import type { ActivityIdProfitRule, ActivitySkuPreview, ActivitySkuPreviewItem, ActivitySkuRules, ActivityTaskItem, AppSettings, AuditLogPage, BulkActivityResult, CategoryPage, CategorySummary, CleanupResult, HalfHeadcostItem, InventoryCategory, InventoryDiffItem, InventoryPreview, InventoryStatus, MonitoringSnapshot, RegionProfile, RegionSummary, SkuResult, SystemSettings, SystemSettingsInfo, TaskItem, User } from './types'

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

export async function getInventory(inventoryCategory = 'A') {
  const { data } = await http.get<InventoryStatus>('/inventory', { params: { inventory_category: inventoryCategory } })
  return data
}

export async function getInventoryCategories() {
  const { data } = await http.get<{ items: InventoryCategory[] }>('/inventory/categories')
  return data.items
}

export async function createInventoryCategory(payload: { key: string; label: string; code_pattern?: string | null; price_max: number }) {
  const { data } = await http.post<InventoryCategory>('/admin/inventory/categories', payload)
  return data
}

export async function updateInventoryCategory(key: string, payload: { label: string; code_pattern?: string | null; price_max: number; enabled?: boolean; sort_order?: number }) {
  const { data } = await http.put<InventoryCategory>(`/admin/inventory/categories/${encodeURIComponent(key)}`, payload)
  return data
}

export async function deleteInventoryCategory(key: string) {
  await http.delete(`/admin/inventory/categories/${encodeURIComponent(key)}`)
}

export interface InventoryItemFilters {
  query?: string
  setType?: string
  sourceSheet?: string
  priceMin?: number | null
  priceMax?: number | null
  rowMin?: number | null
  rowMax?: number | null
}

function inventoryFilterParams(filters: InventoryItemFilters) {
  return {
    query: filters.query ?? '',
    set_type: filters.setType ?? '',
    source_sheet: filters.sourceSheet ?? '',
    price_min: filters.priceMin ?? undefined,
    price_max: filters.priceMax ?? undefined,
    row_min: filters.rowMin ?? undefined,
    row_max: filters.rowMax ?? undefined,
  }
}

export async function getInventoryItems(filters: InventoryItemFilters = {}, page = 1, pageSize = 30, inventoryCategory = 'A') {
  const { data } = await http.get<{ total: number; items: SkuResult[] }>('/inventory/items', {
    params: { ...inventoryFilterParams(filters), page, page_size: pageSize, inventory_category: inventoryCategory },
  })
  return data
}

export async function previewInventory(file: File, inventoryCategory = 'A') {
  const form = new FormData()
  form.append('file', file)
  form.append('inventory_category', inventoryCategory)
  const { data } = await uploadHttp.post<InventoryPreview & { message: string }>('/inventory/preview', form)
  return data
}

export async function getPendingInventory() {
  const { data } = await uploadHttp.get<{ pending: InventoryPreview | null; all: Record<string, InventoryPreview | null> }>('/inventory/pending')
  return data
}

export async function applyPendingInventory(keepSkus: string[], skipSkus: string[], inventoryCategory = 'A') {
  const { data } = await uploadHttp.post<InventoryStatus & { message: string; sku_count: number; kept: number; skipped: number }>('/inventory/pending/apply', { keep_skus: keepSkus, skip_skus: skipSkus, inventory_category: inventoryCategory })
  return data
}

export async function discardPendingInventory(inventoryCategory = 'A') {
  const { data } = await uploadHttp.post<{ message: string; discarded: boolean }>('/inventory/pending/discard', { inventory_category: inventoryCategory })
  return data
}

export async function uploadInventory(file: File, inventoryCategory = 'A') {
  const form = new FormData()
  form.append('file', file)
  form.append('inventory_category', inventoryCategory)
  const { data } = await uploadHttp.post<InventoryStatus & { message: string }>('/inventory', form)
  return data
}

export async function rebuildInventory(inventoryCategory = 'A') {
  const { data } = await uploadHttp.post<InventoryStatus & { message: string }>('/inventory/rebuild', null, { params: { inventory_category: inventoryCategory } })
  return data
}

export async function getAdminInventory(inventoryCategory = 'A') {
  const { data } = await http.get<InventoryStatus>('/admin/inventory', { params: { inventory_category: inventoryCategory } })
  return data
}

export async function getAdminInventoryItems(filters: InventoryItemFilters = {}, page = 1, pageSize = 30, inventoryCategory = 'A') {
  const { data } = await http.get<{ total: number; items: SkuResult[] }>('/admin/inventory/items', {
    params: { ...inventoryFilterParams(filters), page, page_size: pageSize, inventory_category: inventoryCategory },
  })
  return data
}

export async function createInventoryItem(payload: { sku: string; price: number | null; set_type: string; inventory_category?: string }) {
  const { data } = await http.post<{ item: SkuResult }>('/admin/inventory/items', payload)
  return data.item
}

export async function updateInventoryItem(oldSku: string, payload: { sku: string; price: number | null; set_type: string; inventory_category?: string }) {
  const { data } = await http.put<{ item: SkuResult }>(`/admin/inventory/items/${encodeURIComponent(oldSku)}`, payload)
  return data.item
}

export async function deleteInventoryItem(sku: string, inventoryCategory = 'A') {
  const { data } = await http.delete<InventoryStatus & { message: string; sku: string }>(`/admin/inventory/items/${encodeURIComponent(sku)}`, { params: { inventory_category: inventoryCategory } })
  return data
}

export async function getHalfHeadcost(query = '', page = 1, pageSize = 30, categoryCode?: string, inventoryCategory = 'A') {
  const { data } = await http.get<{ total: number; items: HalfHeadcostItem[]; category?: { code: string; name: string }; inventory_category?: string }>('/half-headcost', {
    params: { query, page, page_size: pageSize, category_code: categoryCode, inventory_category: inventoryCategory },
  })
  return data
}

export async function importHalfHeadcost(file: File, categoryCode?: string, inventoryCategory = 'A') {
  const form = new FormData()
  form.append('file', file)
  if (categoryCode) form.append('category_code', categoryCode)
  form.append('inventory_category', inventoryCategory)
  const { data } = await uploadHttp.post<{ message: string; incoming: number; added: number; total: number }>(
    '/half-headcost/import',
    form,
  )
  return data
}

export async function deleteHalfHeadcost(sku: string, categoryCode?: string, inventoryCategory = 'A') {
  await http.delete(`/half-headcost/${encodeURIComponent(sku)}`, { params: { category_code: categoryCode, inventory_category: inventoryCategory } })
}

export async function createHalfHeadcost(payload: { sku: string; set_type: string; category_code?: string; inventory_category?: string }) {
  const { data } = await http.post<{ message: string; item: HalfHeadcostItem }>('/admin/half-headcost', payload)
  return data
}

export async function updateHalfHeadcost(sku: string, payload: { set_type: string; category_code?: string; inventory_category?: string }) {
  const { data } = await http.put<{ message: string; item: HalfHeadcostItem }>(`/admin/half-headcost/${encodeURIComponent(sku)}`, payload)
  return data
}

export interface ActivityPreviewQuery {
  page?: number
  pageSize?: number
  resultFilter?: ActivitySkuPreviewItem['result'] | null
}

export async function previewActivitySkuRules(
  file: File,
  rules: ActivitySkuRules | undefined,
  regionCode: string,
  idProfitRules?: ActivityIdProfitRule[],
  categoryCode?: string,
  query: ActivityPreviewQuery = {},
) {
  const form = new FormData()
  form.append('file', file)
  if (rules) form.append('skc_rules', JSON.stringify(rules))
  if (idProfitRules) form.append('id_profit_rules', JSON.stringify(idProfitRules))
  form.append('region_code', regionCode)
  if (categoryCode) form.append('category_code', categoryCode)
  form.append('page', String(query.page ?? 1))
  if (query.pageSize) form.append('page_size', String(query.pageSize))
  if (query.resultFilter) form.append('result_filter', query.resultFilter)
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

export async function createAdminCategory(payload: { code: string; name: string; template_type: string; set_types?: number[]; allowed_regions?: string[] | null; inventory_category?: string | null; sort_order?: number }) {
  const { data } = await http.post<CategorySummary>('/admin/categories', payload)
  return data
}

export async function updateAdminCategory(code: string, payload: { name: string; template_type: string; set_types?: number[]; allowed_regions?: string[] | null; enabled?: boolean; is_default?: boolean; inventory_category?: string | null; sort_order?: number }) {
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
