import axios from 'axios'
import type { ActivityIdProfitRule, ActivitySkuPreview, ActivitySkuPreviewItem, ActivitySkuRules, ActivityTaskItem, AdminStoreSession, AppSettings, AuditLogPage, BulkActivityResult, CategoryPage, CategorySummary, CleanupResult, HalfHeadcostItem, InventoryCategory, InventoryDiffItem, InventoryPreview, InventoryStatus, MonitoringSnapshot, ProductCategoryChildren, ProductCategoryPathInfo, ProductConstants, ProductRegions, ProductSizeSpecConfig, ProductSizeTemplate, ProductCategorySuggest, ProductBatchState, ProductCreateForm, ProductCreateResult, ProductDescribeResult, ProductMaterialImage, ProductPreviewResult, ProductStoreEnv, ProductTemplateInfo, RegionProfile, RegionSummary, SkuResult, AdminUserStoreAccounts, AdminUserStoreBinding, StoreAccount, StoreGoodsDetail, StoreGoodsFilters, StoreGoodsLifecycle, StoreGoodsPage, StoreKefuAction, StoreKefuActionPayload, StoreKefuBatchPayload, StoreKefuBatchView, StoreKefuCard, StoreKefuMessage, StoreKefuQueryBatchResult, StoreKefuQueryResult, StoreKefuTool, StoreLoginLog, StoreSession, StoreSite, SystemSettings, SystemSettingsInfo, TaskItem, User, UpdateJobResponse, UpdateStartResponse, UpdateStatusResponse, VersionInfo, WeightHeadcostItem } from './types'

const http = axios.create({
  baseURL: '/api',
  timeout: 15 * 1000,
})

// 店铺接口返回 401 = 会话已失效：通知 storeState 立刻回落到登录卡
http.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error?.response?.status
    const url = String(error?.config?.url || '')
    // 登录/退出接口本身不参与（登录失败的 401 是凭据问题）
    if (status === 401 && url.startsWith('/store/') && !url.startsWith('/store/session')) {
      const detail = error?.response?.data?.detail
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('store:session-invalid', { detail }))
      }
    }
    return Promise.reject(error)
  },
)

const uploadHttp = axios.create({
  baseURL: '/api',
  timeout: 30 * 60 * 1000,
})

export function errorMessage(error: unknown): string {
  // 业务代码有时直接抛/传字符串，优先原样展示，避免被兜底成「请求失败」
  if (typeof error === 'string') return error
  if (axios.isAxiosError(error)) {
    if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT') return '请求超时，请检查服务器和数据库连接'
    const detail = error.response?.data?.detail
    if (typeof detail === 'string' && detail.trim()) return detail
    // FastAPI 的 422 校验错误是数组，挑出可读的 msg，避免提示变成 [object Object]
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) => (item && typeof item === 'object' ? String(item.msg || '') : String(item || '')))
        .filter(Boolean)
      if (messages.length) return messages.join('；')
    }
    return error.message
  }
  return error instanceof Error ? error.message : '请求失败'
}

export async function getMe() {
  const { data } = await http.get<User>('/auth/me')
  return data
}

// ==================== 店铺管理（每用户登录自己的店铺） ====================

export async function getStoreSession() {
  const { data } = await http.get<StoreSession>('/store/session')
  return data
}

export async function loginStore(payload: {
  account?: string
  account_id?: number
  password?: string
  mall_id?: string
  remember?: boolean; verify_code?: string
  use_remembered?: boolean
}) {
  // 登录要跑 anti-content + 换码，实测 0.2~3.4s 偶发更慢，单独放宽超时
  const { data } = await http.post<StoreSession>('/store/session/login', payload, { timeout: 130 * 1000 })
  return data
}

export async function sendStoreLoginCode(payload: { account: string; contact_type?: number }) {
  const { data } = await http.post<{ sent: boolean; contactType: number }>(
    '/store/session/send-code',
    payload,
    { timeout: 60 * 1000 },
  )
  return data
}

export async function logoutStore() {
  const { data } = await http.post<StoreSession>('/store/session/logout')
  return data
}

export async function refreshStoreSession() {
  const { data } = await http.post<StoreSession>('/store/session/refresh')
  return data
}

export async function forgetStorePassword() {
  const { data } = await http.delete<{ cleared: boolean }>('/store/session/remember')
  return data
}

// ==================== 商品创建 ====================
export async function suggestProductCategory(name: string) {
  const { data } = await http.post<ProductCategorySuggest>(
    '/store/products/category/suggest',
    { name },
    { timeout: 50 * 1000 },
  )
  return data
}

export async function searchProductCategories(keyword: string, isEn = false) {
  const { data } = await http.post<ProductCategorySuggest>(
    '/store/products/category/search',
    { keyword, is_en: isEn },
    { timeout: 50 * 1000 },
  )
  return data
}

export async function getCategoryConfig(leafCatId: number, siteId = 100) {
  const { data } = await http.get<{
    config: Record<string, unknown>
    english_name_required: boolean
    title_max_length: number
    spec_max_length: number
    sensitive_types: number[]
    extra_requirements: string[]
  }>('/store/products/category/config', {
    params: { leaf_cat_id: leafCatId, site_id: siteId },
    timeout: 40 * 1000,
  })
  return data
}

export async function getProductConstants() {
  const { data } = await http.get<ProductConstants>('/store/products/constants', {
    timeout: 30 * 1000,
  })
  return data
}

export async function getProductRegions(refresh = false) {
  const { data } = await http.get<ProductRegions>('/store/products/regions', {
    params: { refresh },
    timeout: 40 * 1000,
  })
  return data
}

export async function validateSizeTemplate(
  catId: number,
  ids: number[],
  generalSizeType = 0,
  sizeValues: string[] = [],
) {
  const { data } = await http.get<{
    ok: boolean
    bad: number[]
    typeBad?: number[]
    sizeBad?: { businessId: number; missing: string[]; sizes: string[] }[]
    available: ProductSizeTemplate[]
    suggestion: number | null
    reason: string
  }>('/store/products/size-template/validate', {
    params: {
      cat_id: catId,
      ids: ids.join(','),
      general_size_type: generalSizeType || undefined,
      size_values: sizeValues.length ? sizeValues.join(',') : undefined,
    },
    timeout: 60 * 1000,
  })
  return data
}

export async function getSizeTemplates(catId: number, siteId?: number) {
  const { data } = await http.get<{
    total: number
    group_id?: number | null
    list: ProductSizeTemplate[]
  }>('/store/products/size-templates', {
    // site_id 决定按哪个尺码组过滤（站点 100 → US 组），不传会拿到不能用的模板
    params: { cat_id: catId, site_id: siteId || undefined },
    timeout: 40 * 1000,
  })
  return data
}

export async function getSizeSpecConfig(catId: number) {
  const { data } = await http.get<ProductSizeSpecConfig>('/store/products/size-spec-config', {
    params: { cat_id: catId },
    timeout: 40 * 1000,
  })
  return data
}

export async function getCategoryHints() {
  const { data } = await http.get<ProductCategorySuggest>('/store/products/category/hints', {
    timeout: 40 * 1000,
  })
  return data
}

export async function getProductWarehouses(siteId?: number) {
  const { data } = await http.get<{
    total: number
    items: Array<{ warehouseId: string; warehouseName: string; siteIds: number[]; siteNames: string[] }>
  }>('/store/products/warehouses', {
    params: siteId ? { site_id: siteId } : undefined,
    timeout: 40 * 1000,
  })
  return data.items
}

export async function getCategoryChildren(parentCatId = 0) {
  const { data } = await http.get<ProductCategoryChildren>('/store/products/category/children', {
    params: { parent_cat_id: parentCatId },
    timeout: 40 * 1000,
  })
  return data
}

export async function getCategoryPath(catIds: number[]) {
  const { data } = await http.post<{ total: number; list: ProductCategoryPathInfo[] }>(
    '/store/products/category/path',
    { cat_ids: catIds },
    { timeout: 40 * 1000 },
  )
  return data
}

export async function getProductTemplate(catId: number, refresh = false) {
  const { data } = await http.get<ProductTemplateInfo>('/store/products/template', {
    params: { cat_id: catId, refresh },
    timeout: 40 * 1000,
  })
  return data
}

export async function describeProduct(catId: number, selected: Record<string, string | number>) {
  const { data } = await http.post<ProductDescribeResult>(
    '/store/products/describe',
    { cat_id: catId, selected },
    { timeout: 40 * 1000 },
  )
  return data
}

export async function getProductSites(refresh = false) {
  const { data } = await http.get<{ items: Array<Record<string, unknown>> }>('/store/products/sites', {
    params: { refresh },
    timeout: 40 * 1000,
  })
  return data.items
}

export async function getProductEnv(siteId?: number, refresh = false) {
  const { data } = await http.get<ProductStoreEnv>('/store/products/env', {
    params: { site_id: siteId, refresh },
    timeout: 40 * 1000,
  })
  return data
}

export async function getProductImages(params: {
  carousel?: boolean
  page_no?: number
  page_size?: number
  name?: string
  proportion?: string
  type?: number
  folder_id?: number
}) {
  const { data } = await http.get<{ total?: number; list?: ProductMaterialImage[] }>('/store/products/images', {
    params: {
      carousel: params.carousel ? true : undefined,
      page_no: params.page_no,
      page_size: params.page_size,
      name: params.name || undefined,
      proportion: params.proportion || undefined,
      type: params.type,
      folder_id: params.folder_id,
    },
    timeout: 50 * 1000,
  })
  return data
}

export async function previewProduct(form: ProductCreateForm) {
  const { data } = await http.post<ProductPreviewResult>('/store/products/preview', { form }, { timeout: 90 * 1000 })
  return data
}

export async function createProduct(form: ProductCreateForm, dryRun = true) {
  const { data } = await http.post<ProductCreateResult>(
    '/store/products/create',
    { form, dry_run: dryRun },
    { timeout: 200 * 1000 },
  )
  return data
}

export interface MaterialFolderNode {
  id: number
  name: string
  children: MaterialFolderNode[]
}

export interface ProductFormTemplateItem {
  id: number
  name: string
  note: string
  updated_at: string | null
  field_count: number
}

export async function listFormTemplates() {
  const { data } = await http.get<{ total: number; items: ProductFormTemplateItem[] }>(
    '/store/products/form-templates',
    { timeout: 30 * 1000 },
  )
  return data
}

export async function getFormTemplate(id: number) {
  const { data } = await http.get<{ id: number; name: string; note: string; values: Record<string, unknown> }>(
    `/store/products/form-templates/${id}`,
    { timeout: 30 * 1000 },
  )
  return data
}

export async function saveFormTemplate(name: string, note: string, form: unknown) {
  const { data } = await http.post<{ id: number; name: string; updated: boolean }>(
    '/store/products/form-templates',
    { name, note, form },
    { timeout: 30 * 1000 },
  )
  return data
}

export async function updateFormTemplate(
  id: number,
  payload: { name?: string; note?: string; form?: unknown },
) {
  const { data } = await http.patch<{ id: number; name: string; note: string; form_updated: boolean }>(
    `/store/products/form-templates/${id}`,
    payload,
    { timeout: 30 * 1000 },
  )
  return data
}

export async function deleteFormTemplate(id: number) {
  const { data } = await http.delete<{ ok: boolean }>(`/store/products/form-templates/${id}`, {
    timeout: 30 * 1000,
  })
  return data
}

export async function getMaterialFolders() {
  const { data } = await http.get<{ root: MaterialFolderNode | null }>('/store/products/folders', {
    timeout: 40 * 1000,
  })
  return data
}

export async function removeMaterial(imgIds: number[], videoIds: number[] = []) {
  const { data } = await http.post<{ ok: boolean; msg: string; count: number }>(
    '/store/products/material/remove',
    { img_ids: imgIds, video_ids: videoIds },
    { timeout: 60 * 1000 },
  )
  return data
}

export async function compressMaterial(urls: string[]) {
  const { data } = await http.post<{ compressMap: Record<string, string> }>(
    '/store/products/material/compress',
    { urls },
    { timeout: 120 * 1000 },
  )
  return data
}

export async function cropMaterial(url: string, cutConfig: { width: number; height: number; dx?: number; dy?: number }) {
  const { data } = await http.post<{ url: string; width: number; height: number; size: number }>(
    '/store/products/material/crop',
    { url, cut_config: cutConfig },
    { timeout: 60 * 1000 },
  )
  return data
}

export async function uploadProductVideo(contentBase64: string, fileName: string) {
  const { data } = await http.post<{
    url: string
    videoUrl: string
    coverUrl: string
    materialId: number | null
    vid: string
    width: number
    height: number
    split: boolean
    parts: number
  }>('/store/products/upload-video', { content_base64: contentBase64, file_name: fileName }, {
    timeout: 300 * 1000,
  })
  return data
}

export async function uploadProductImage(
  contentBase64: string,
  fileName: string,
  crop?: { width: number; height: number },
) {
  const { data } = await http.post<{ imgUrl: string; materialId: number }>(
    '/store/products/upload',
    {
      content_base64: contentBase64,
      file_name: fileName,
      crop_width: crop?.width || 0,
      crop_height: crop?.height || 0,
    },
    { timeout: 150 * 1000 },
  )
  return data
}

export async function getProductBatchStatus() {
  const { data } = await http.get<ProductBatchState>('/store/products/batch')
  return data
}

export async function startProductBatch(items: Array<Record<string, unknown>>, dryRun = true) {
  const { data } = await http.post<ProductBatchState>(
    '/store/products/batch',
    { items, dry_run: dryRun },
    { timeout: 60 * 1000 },
  )
  return data
}

export async function cancelProductBatch() {
  const { data } = await http.post<ProductBatchState>('/store/products/batch/cancel')
  return data
}

export async function getStoreAccounts() {
  const { data } = await http.get<{ items: StoreAccount[] }>('/store/accounts')
  return data.items
}

export async function addStoreAccount(payload: { account: string; password: string; label?: string }) {
  const { data } = await http.post<StoreAccount>('/store/accounts', payload)
  return data
}

export async function deleteStoreAccount(accountId: number) {
  const { data } = await http.delete<{ deleted: boolean }>(`/store/accounts/${accountId}`)
  return data
}

export async function getAdminUserStoreAccounts(userId: number, withPassword = false) {
  const { data } = await http.get<AdminUserStoreAccounts>(`/admin/users/${userId}/store-accounts`, {
    params: withPassword ? { with_password: true } : undefined,
  })
  return data
}

export async function getAdminUserStoreBinding(userId: number) {
  const { data } = await http.get<AdminUserStoreBinding>(`/admin/users/${userId}/store-binding`)
  return data
}

export async function getAdminUserStoreCredential(userId: number) {
  const { data } = await http.get<{ account: string; password: string; updated_at: string | null }>(
    `/admin/users/${userId}/store-credential`,
    { timeout: 20 * 1000 },
  )
  return data
}

export interface AdminStoreUserItem {
  user_id: number
  username: string
  display_name: string
  is_admin: boolean
  store_access: boolean
  template_count: number
  stats: { total_ok: number; today_ok: number; week_ok: number; failed: number }
  store: {
    account_masked: string
    status: string
    mall_name: string
    login_at: string | null
    expires_at: string | null
    last_seen_at: string | null
    remember: boolean
    last_error: string
  }
}

export async function getAdminStoreUsers() {
  const { data } = await http.get<{ items: AdminStoreUserItem[]; total: number }>('/admin/store-users')
  return data
}

export async function getAdminStoreUserTemplates(userId: number) {
  const { data } = await http.get<{ total: number; items: ProductFormTemplateItem[] }>(
    `/admin/store-users/${userId}/templates`,
  )
  return data
}

export async function deleteAdminStoreUserTemplate(userId: number, templateId: number) {
  const { data } = await http.delete<{ ok: boolean }>(
    `/admin/store-users/${userId}/templates/${templateId}`,
  )
  return data
}

export async function getAdminStoreSessions() {
  const { data } = await http.get<{ items: AdminStoreSession[] }>('/admin/store-sessions')
  return data.items
}

export async function revokeAdminStoreSession(userId: number) {
  const { data } = await http.delete<{ revoked: boolean }>(`/admin/store-sessions/${userId}`)
  return data
}

export async function getAdminStoreLogs(
  params: {
    limit?: number
    action?: string
    user_id?: number
    keyword?: string
    start?: string
    end?: string
  } = {},
) {
  const { data } = await http.get<{ items: StoreLoginLog[] }>('/admin/store-logs', { params })
  return data.items
}

export async function deleteAdminStoreLogs(ids: number[]) {
  const { data } = await http.delete<{ deleted: number }>('/admin/store-logs', { data: { ids } })
  return data
}

export async function getAdminKefuActions(
  params: { limit?: number; stage?: string; keyword?: string; start?: string; end?: string } = {},
) {
  const { data } = await http.get<{ items: StoreKefuAction[] }>('/admin/kefu-actions', { params })
  return data.items
}

export async function deleteAdminKefuActions(ids: string[]) {
  const { data } = await http.delete<{ deleted: number }>('/admin/kefu-actions', { data: { ids } })
  return data
}

export async function getStoreSites(force = false) {
  const { data } = await http.get<{ items: StoreSite[] }>('/store/sites', {
    params: force ? { force: 'true' } : undefined,
    timeout: 60 * 1000,
  })
  return data.items
}

export interface StoreGoodsQuery {
  page?: number
  page_size?: number
  site?: string
  keyword?: string
  /** name（商品名模糊）/ spu / skc / sku / extcode（SKC 货号） */
  search_type?: string
  /** 兼容旧字段：0 未发布 / 1 已加入站点 */
  site_status?: number
  /** 生命周期状态：9 待选品 / 10 待处理 / 12 已加入站点 / 13 待价格校验 */
  select_status?: number
  /** 时间类型：1 创建 / 4 价格确认 / 6 加入站点 / 7 下架 */
  time_type?: number
  time_begin?: number
  time_end?: number
  /** 逗号分隔的枚举列表 */
  price_review_status?: string
  sample_qc_status?: string
  todo_types?: string
  punish_status?: string
  limit_status?: string
  remove_status?: number
  force?: boolean
}

export async function getStoreGoods(params: StoreGoodsQuery = {}) {
  // 一次一页（服务端筛选）：平台连续 8~10 次请求/分钟会 40001
  const { data } = await http.get<StoreGoodsPage>('/store/goods', { params, timeout: 60 * 1000 })
  return data
}

export async function getStoreGoodsFilters() {
  const { data } = await http.get<StoreGoodsFilters>('/store/goods/filters', { timeout: 40 * 1000 })
  return data
}

export async function getProductLifecycle(productId: string) {
  const { data } = await http.get<StoreGoodsLifecycle>(`/store/goods/${productId}/lifecycle`, {
    timeout: 60 * 1000,
  })
  return data
}

export async function getStoreGoodsDetail(productId: string) {
  const { data } = await http.get<StoreGoodsDetail>(`/store/goods/${productId}`, { timeout: 60 * 1000 })
  return data
}

// ==================== 客服自助工具（只读） ====================

export async function getStoreKefuTools(force = false) {
  const { data } = await http.get<{ items: StoreKefuTool[] }>('/store/kefu/tools', {
    params: force ? { force: 'true' } : undefined,
    timeout: 45 * 1000,
  })
  return data.items
}

export async function getStoreKefuCard(toolId?: string, maxPages = 10) {
  // 卡片会被聊天记录挤出最近 50 条，服务端会按 toolId 精确找并自动向前翻页
  const { data } = await http.get<StoreKefuCard>('/store/kefu/card', {
    params: { tool_id: toolId || undefined, max_pages: maxPages },
    timeout: 90 * 1000,
  })
  return data
}

export async function getStoreKefuCards(maxPages = 10) {
  const { data } = await http.get<{ items: Array<{ tool_id: string; msg_id: string; option_title: string | null }> }>(
    '/store/kefu/cards',
    { params: { max_pages: maxPages }, timeout: 90 * 1000 },
  )
  return data.items
}

export async function getStoreKefuMessages(limit = 50) {
  const { data } = await http.get<{ total: number; items: StoreKefuMessage[]; limit: number }>(
    '/store/kefu/messages',
    { params: { limit }, timeout: 45 * 1000 },
  )
  return data
}

export async function queryStoreKefuProduct(payload: { type: string; id: string; tool_id?: string }) {
  const { data } = await http.post<StoreKefuQueryResult>('/store/kefu/query', payload, { timeout: 45 * 1000 })
  return data
}

// ==================== 客服写操作（预览 → 确认 → 回执） ====================

export async function createStoreKefuAction(payload: StoreKefuActionPayload) {
  // 创建预览要在服务端完成「查商品 + 拦截检查」，给足超时
  const { data } = await http.post<StoreKefuAction>('/store/kefu/actions', payload, { timeout: 90 * 1000 })
  return data
}

export async function confirmStoreKefuAction(actionId: string) {
  const { data } = await http.post<StoreKefuAction>(`/store/kefu/actions/${actionId}/confirm`, undefined, {
    timeout: 90 * 1000,
  })
  return data
}

export async function getStoreKefuActions(limit = 30, stage?: string) {
  const { data } = await http.get<{ items: StoreKefuAction[] }>('/store/kefu/actions', {
    params: { limit, stage: stage || undefined },
  })
  return data.items
}

export async function getStoreKefuAction(actionId: string) {
  const { data } = await http.get<StoreKefuAction>(`/store/kefu/actions/${actionId}`)
  return data
}

export async function fetchStoreKefuReply(actionId: string) {
  const { data } = await http.post<StoreKefuAction>(`/store/kefu/actions/${actionId}/reply`, undefined, {
    timeout: 45 * 1000,
  })
  return data
}

// ==================== 客服批量执行 ====================

export async function queryStoreKefuProductBatch(payload: { type: string; ids: string; tool_id?: string }) {
  // 串行查询，50 个编号可能要十几秒
  const { data } = await http.post<StoreKefuQueryBatchResult>('/store/kefu/query-batch', payload, {
    timeout: 150 * 1000,
  })
  return data
}

export async function createStoreKefuBatch(payload: StoreKefuBatchPayload) {
  const { data } = await http.post<StoreKefuBatchView>('/store/kefu/batches', payload, { timeout: 60 * 1000 })
  return data
}

export async function getStoreKefuBatch(batchId: string) {
  const { data } = await http.get<StoreKefuBatchView>(`/store/kefu/batches/${batchId}`)
  return data
}

export async function cancelStoreKefuBatch(batchId: string) {
  const { data } = await http.post<StoreKefuBatchView>(`/store/kefu/batches/${batchId}/cancel`)
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

export async function createInventoryItem(payload: { sku: string; price: number | null; set_type: string; inventory_category?: string; manual_combo?: boolean }) {
  const { data } = await http.post<{ item: SkuResult }>('/admin/inventory/items', payload)
  return data.item
}

export async function updateInventoryItem(oldSku: string, payload: { sku: string; price: number | null; set_type: string; inventory_category?: string; manual_combo?: boolean }) {
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

export async function getWeightHeadcost(query = '', page = 1, pageSize = 30, categoryCode?: string, inventoryCategory = 'A') {
  const { data } = await http.get<{ total: number; items: WeightHeadcostItem[]; category?: { code: string; name: string }; inventory_category?: string }>('/weight-headcost', {
    params: { query, page, page_size: pageSize, category_code: categoryCode, inventory_category: inventoryCategory },
  })
  return data
}

export async function importWeightHeadcost(file: File, categoryCode?: string, inventoryCategory = 'A') {
  const form = new FormData()
  form.append('file', file)
  if (categoryCode) form.append('category_code', categoryCode)
  form.append('inventory_category', inventoryCategory)
  const { data } = await uploadHttp.post<{ message: string; incoming: number; added: number; updated: number; total: number }>(
    '/weight-headcost/import',
    form,
  )
  return data
}

export async function deleteWeightHeadcost(sku: string, categoryCode?: string, inventoryCategory = 'A') {
  await http.delete(`/weight-headcost/${encodeURIComponent(sku)}`, { params: { category_code: categoryCode, inventory_category: inventoryCategory } })
}

export async function createWeightHeadcost(payload: { sku: string; weight_g: number; category_code?: string; inventory_category?: string }) {
  const { data } = await http.post<{ message: string; item: WeightHeadcostItem }>('/admin/weight-headcost', payload)
  return data
}

export async function updateWeightHeadcost(sku: string, payload: { weight_g: number; category_code?: string; inventory_category?: string }) {
  const { data } = await http.put<{ message: string; item: WeightHeadcostItem }>(`/admin/weight-headcost/${encodeURIComponent(sku)}`, payload)
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

export interface ActivityDropSelection {
  /** 要删除所有行的「无法识别」SKC 原文 */
  skcs?: string[]
  /** true 表示删除全部无法识别 SKC 的行（预览列表被截断或用户选择全删时使用） */
  all?: boolean
  /** 用户主动取消删除时传 false，避免后端按默认删除执行 */
  keep?: boolean
}

export async function processBulkActivity(file: File, regionCode: string, upliftLimit?: number, rules?: ActivitySkuRules, idProfitRules?: ActivityIdProfitRule[], categoryCode?: string, drop?: ActivityDropSelection) {
  const form = new FormData()
  form.append('file', file)
  form.append('region_code', regionCode)
  if (categoryCode) form.append('category_code', categoryCode)
  if (upliftLimit !== undefined) form.append('uplift_limit', String(upliftLimit))
  if (rules) form.append('skc_rules', JSON.stringify(rules))
  if (idProfitRules) form.append('id_profit_rules', JSON.stringify(idProfitRules))
  // 默认删除全部无法识别行；用户主动选择保留时才显式传 false
  if (drop?.keep) form.append('drop_all_unrecognized', 'false')
  else if (drop?.all) form.append('drop_all_unrecognized', 'true')
  else if (drop?.skcs?.length) form.append('drop_unrecognized_skcs', JSON.stringify(drop.skcs))
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

export type AdminUserStatusAction = 'approve' | 'enable' | 'disable'

export async function updateUserStatus(id: number, status: AdminUserStatusAction) {
  const { data } = await http.post<User>(`/admin/users/${id}/${status}`)
  return data
}

export async function updateAdminUser(id: number, payload: { username?: string; display_name?: string; note?: string; password?: string; store_access?: boolean; modules?: string[]; store_modules?: string[] }) {
  const { data } = await http.patch<User>(`/admin/users/${id}`, payload)
  return data
}

export async function bulkUpdateAdminUsers(payload: { user_ids: number[]; display_name?: string; note?: string; status?: 'approved' | 'rejected'; store_access?: boolean }) {
  const { data } = await http.patch<{ items: User[]; updated: number; store_sessions_revoked?: number }>('/admin/users/bulk', payload)
  return data
}

export async function bulkDeleteAdminUsers(userIds: number[]) {
  const { data } = await http.post<{ deleted_ids: number[]; deleted: number }>('/admin/users/bulk-delete', { user_ids: userIds })
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

export async function getAdminAuditLogs(params: {
  page: number
  page_size: number
  action?: string
  keyword?: string
  start?: string
  end?: string
}) {
  const { data } = await http.get<AuditLogPage>('/admin/audit-logs', { params })
  return data
}

export async function deleteAdminAuditLogs(ids: number[]) {
  const { data } = await http.delete<{ deleted: number }>('/admin/audit-logs', { data: { ids } })
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

// 一键热更新：边车状态查询含 git fetch + docker inspect，可能超过默认 15s，单独放宽超时
const UPDATE_STATUS_TIMEOUT = 60 * 1000

export async function getAdminUpdateStatus() {
  const { data } = await http.get<UpdateStatusResponse>('/admin/update/status', { timeout: UPDATE_STATUS_TIMEOUT })
  return data
}

export async function getAdminUpdateJob(since: number) {
  const { data } = await http.get<UpdateJobResponse>('/admin/update/job', { params: { since }, timeout: UPDATE_STATUS_TIMEOUT })
  return data
}

export async function runAdminUpdate() {
  const { data } = await http.post<UpdateStartResponse>('/admin/update')
  return data
}

export async function rollbackAdminUpdate() {
  const { data } = await http.post<UpdateStartResponse>('/admin/update/rollback')
  return data
}

// 当前版本号与更新日志（管理员接口）
export async function getVersion() {
  const { data } = await http.get<VersionInfo>('/version')
  return data
}
