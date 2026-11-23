export interface InventoryStatus {
  path: string
  exists: boolean
  cache_exists: boolean
  cache_valid: boolean
  sku_count: number
  size: number | null
  modified_at: string | null
}

export interface InventoryCategory {
  key: string
  label: string
  mode?: string
  code_pattern?: string | null
  price_max?: number
  enabled?: boolean
  sort_order?: number
}

export interface InventoryDiffItem {
  sku: string
  old?: { price?: number; set_type?: string; source_sheet?: string; source_row?: number; source_column?: number }
  new?: { price?: number; set_type?: string; source_sheet?: string; source_row?: number; source_column?: number }
}
export interface InventoryPreview {
  uploaded_at: string
  sku_count: number
  inventory_category?: string
  diff: {
    changed: InventoryDiffItem[]
    added: InventoryDiffItem[]
    removed: InventoryDiffItem[]
    unchanged: string[]
  }
}

export interface TaskStats {
  total?: number
  matched?: number
  unmatched?: number
  unmatched_pos?: string[]
  missing_skus?: string[]
  type_stats?: Record<string, number>
}

export interface TaskItem {
  id: string
  owner_id?: number
  owner_name?: string
  owner_username?: string
  status: 'preparing' | 'queued' | 'running' | 'completed' | 'failed'
  progress: number
  message: string
  created_at: string
  started_at?: string
  finished_at?: string
  stats: TaskStats
  logs: string[]
  download_ready: boolean
  region_code: string
  region_name: string
  category_code: string
  category_name: string
  config_version: number
}

export interface SkuResult {
  sku: string
  found: boolean
  price?: number
  set_type?: string
  source_sheet?: string
  source_row?: number
  source_column?: number
  manual_combo?: boolean
}

export interface HalfHeadcostItem {
  sku: string
  set_type: string
}

export interface WeightHeadcostItem {
  sku: string
  weight_g: number
  headcost: number
}

export interface BulkActivityStats {
  sheet: string
  header_row: number
  input_data_rows: number
  processed_rows: number
  updated_rows: number
  unchanged_rows: number
  removed_rows: number
  skipped_rows: number
  remaining_data_rows: number
  uplift_limit?: number
  custom_skc_rules?: boolean
  id_profit_rule_matches?: number
  custom_id_profit_rules?: boolean
  dropped_unrecognized_rows?: number
  drop_all_unrecognized?: boolean
  drop_unrecognized_skcs?: string[]
}

export type ActivitySingleParseMode = 'first_segment' | 'last_segment' | 'after_marker'

export interface ActivitySingleRule {
  mode: ActivitySingleParseMode
  delimiter?: string
  marker?: string
}

export interface ActivitySetMapping {
  pattern: string
  pieces: number
}

export interface ActivitySkuRules {
  set_keywords: string[]
  set_mappings: ActivitySetMapping[]
  single_rules: ActivitySingleRule[]
  // 兼容字段：后端仍会回显，前端不再使用
  single_mode?: ActivitySingleParseMode
  single_delimiter?: string
  single_marker?: string
  allowed_pieces?: number[]
}

export type ActivityIdType = 'SPU' | 'SKC' | 'SKU'

export interface ActivityIdProfitRule {
  id_type: ActivityIdType
  id: string
  profit: number
}

export type ActivitySkuPreviewAction = '上浮' | '不变' | '将被删除' | '无法识别'

export interface ActivitySkuPreviewItem {
  row: number
  skc: string
  result: '单品' | '套装' | '无法识别'
  value: number | null
  base_price: number | null
  reference_price: number | null
  final_price_low: number | null
  final_price_high: number | null
  action: ActivitySkuPreviewAction
  profit_adjustment: number
  matched_id_type: ActivityIdType | null
  matched_id: string | null
  spu_id?: string | null
  skc_id?: string | null
  sku_id?: string | null
  method: string
}

export interface ActivityUnrecognizedSkc {
  skc: string
  count: number
  rows: number[]
  rows_truncated: boolean
}

export interface ActivitySkuPreview {
  sheet: string
  header_row: number
  total_rows: number
  single_rows: number
  set_rows: number
  unrecognized_rows: number
  unrecognized_skc_total?: number
  unrecognized_skcs?: ActivityUnrecognizedSkc[]
  unrecognized_skcs_truncated?: boolean
  id_profit_rule_matches: number
  uplift_limit: number
  page: number
  page_size: number
  total_items: number
  total_pages: number
  result_filter: '单品' | '套装' | '无法识别' | null
  items: ActivitySkuPreviewItem[]
}

export interface BulkActivityResult {
  message: string
  job_id: string
  filename: string
  download_url: string
  stats: BulkActivityStats
}

export type ActivityTaskStatus = 'queued' | 'running' | 'completed' | 'failed'

export interface ActivityTaskItem {
  id: string
  owner_id?: number
  owner_name?: string
  owner_username?: string
  status: ActivityTaskStatus
  progress: number
  message: string
  filename: string
  created_at?: string
  stats: BulkActivityStats
  logs: string[]
  download_ready: boolean
  region_code: string
  region_name: string
  category_code: string
  category_name: string
  config_version: number
}

export interface User {
  id: number
  username: string
  display_name: string
  note?: string
  role: 'admin' | 'user'
  status: 'pending' | 'approved' | 'rejected'
  /** 店铺管理模块准入（管理员逐个开通；管理员恒为 true） */
  store_access?: boolean
  created_at?: string | null
  approved_at?: string | null
  /** 一级模块权限 */
  modules?: string[]
  /** 店铺管理二级模块权限 */
  store_modules?: string[]
  is_admin?: boolean
}

export type StoreSessionStatus = 'logged_out' | 'logging_in' | 'active' | 'invalid' | 'cooldown'

export interface StoreMallOption {
  mallId?: string | number
  mallName?: string
  mallMode?: number
  mallStatus?: number
}

export interface StoreSession {
  status: StoreSessionStatus
  account_masked: string
  mall_id: string | null
  mall_name: string
  mall_list: StoreMallOption[]
  login_at: string | null
  expires_at: string | null
  cooldown_until: string | null
  cooldown_seconds: number
  last_error: string
  last_error_code: string
  remember: boolean
  remember_account: string
  bridge_configured: boolean
  remember_available: boolean
  /** 客服写操作总熔断开关（false = 全员只读） */
  writes_enabled: boolean
  expires_in_seconds: number
}

export interface AdminStoreSession extends Omit<StoreSession, 'remember_account'> {
  user_id: number
  username: string
  display_name: string
  store_access: boolean
}

export interface StoreLoginLog {
  id: number
  user_id: number | null
  account_masked: string
  action: string
  code: string
  message: string
  ip: string
  created_at: string | null
}

export interface StoreSite {
  site_id: string
  site_name: string
  dr: string
}

/** 生命周期时间轴节点（skcList[].statusTime） */
export interface StoreGoodsStage {
  key: string
  label: string
  at: number | null
  date: string | null
}

export interface StoreGoodsItem {
  product_id: string
  /** SPU（同 product_id，语义别名） */
  spu_id: string
  /** 商品货号（goodsId） */
  goods_id: string
  product_skc_id: string
  skc_id: string
  product_name: string
  main_image: string
  /** SKC 货号（skcList[].extCode） */
  ext_code: string
  /** 该 SKC 下的 SKU ID 列表 */
  sku_ids: string[]
  sku_ext_codes: string[]
  sku_count: number
  /** SKU 明细（参考图「SKU属性集」列，最多 8 条） */
  sku_items: Array<{ sku_id: string; ext_code: string; price: string; goods_sku_id: string }>
  /** SKC 下方 3 张预览图 */
  preview_images: string[]
  /** 已加入站点数（加站详情列） */
  added_site_count: number
  category_name: string
  full_category: string[]
  /** 生命周期状态：9 待选品 / 10 待处理 / 12 已加入站点 / 13 待价格校验 */
  select_status: number | null
  status_name: string
  status_desc: string
  can_on_sale: boolean | null
  can_off_sale: boolean | null
  color_name: string
  stages: StoreGoodsStage[]
  added_to_site_at: string | null
  un_published_at: string | null
  site_ids: string[]
  site_names: string[]
  supplier_price: string
  supplier_price_currency: string
  /** 兼容旧字段：生命周期接口不返回，恒为 null */
  skc_status: number | null
  /** 兼容旧字段：selectStatus=12 → 1，其余 0 */
  site_status: number | null
  remove_status: number | string | null
  sales_total: number | null
  sales_7d: number | null
  created_at: string | null
  updated_at: string | null
}

export interface StoreGoodsStatusCount {
  select_status: number
  status_name: string
  count: number
}

export interface StoreGoodsPage {
  total: number
  page: number
  page_size: number
  items: StoreGoodsItem[]
  keyword: string | null
  /** 本地过滤前扫描了多少条（生命周期全量展平后的 SKC 行数） */
  scanned: number | null
  /** 扫描达到上限仍未扫完时为 true */
  truncated: boolean
  /** 数据来源：lifecycle（上新生命周期管理） */
  source?: string
  /** 各生命周期状态的数量（顶部提示/筛选用） */
  aggregation?: StoreGoodsStatusCount[]
}

export interface StoreAccount {
  id: number
  account: string
  account_masked: string
  label: string
  mall_name: string
  has_password: boolean
  created_at: string | null
  updated_at: string | null
  last_used_at: string | null
  /** 管理员视图：是否当前活跃店铺 */
  active?: boolean
  /** 管理员带 with_password 时返回的明文密码 */
  password?: string
  password_error?: string
}

export interface AdminUserStoreAccounts {
  items: StoreAccount[]
  active_account_masked: string
}

/** 颜色/尺码选项（模板里的原始 values） */
export interface ProductSpecOption {
  vid: number
  value: string
  specId?: number
  group?: { id?: number; name?: string } | number
  extendInfo?: string | null
  /** 尺码值所属的 sizeType（由 sizeTypeConfig 的 specId 反查） */
  sizeType?: number | null
}

/** 尺码类型（常规 / 均码 / 大码…，按 sizeType 分组） */
export interface ProductSizeType {
  sizeType: number
  subTypes?: unknown
  name?: string
  count?: number
}

/** describe 返回的字段：type 决定控件 */
export interface ProductTemplateField {
  key: string
  refPid: number
  pid: number
  templatePid: number
  label: string
  lang2Name?: string
  required: boolean
  type: 'number' | 'select' | 'select+number'
  unit: string
  options: Array<{ label: string; value: number }>
  value: string | number | null
  numberInput: {
    key: string
    title?: string
    min?: number
    max?: number
    precision?: number
    unit: string
    value: string
  } | null
}

/** 类目推荐结果（按商品名联想出的完整路径） */
export interface ProductCategoryPath {
  catPath: number[]
  names: string[]
  label: string
  leafCatId: number
  isLeaf?: boolean
}

/** 类目树节点（逐级展开） */
export interface ProductCategoryNode {
  catId: number
  catName: string
  catEnName?: string
  catLevel?: number
  parentCatId?: number
  isLeaf: boolean
  isHidden?: boolean
}

export interface ProductCategoryChildren {
  parent_cat_id: number
  total: number
  nodes: ProductCategoryNode[]
}

export interface ProductCategoryPathInfo {
  requestCatId: number
  catPath: number[]
  names: string[]
  enNames: string[]
  displayName: string
}

export interface ProductCategorySuggest {
  total: number
  paths: ProductCategoryPath[]
}

export interface ProductTemplateInfo {
  cat_id: number
  template_id: number | null
  summary: Record<string, unknown>
  template: Record<string, unknown>
}

/** 颜色/尺码分组（色系 → 颜色、尺码体系 → 尺码） */
export interface ProductSpecGroupItem {
  groupId: number
  groupName: string
  colors?: ProductSpecOption[]
  sizes?: ProductSpecOption[]
}

/** 统一成 { code, name, shortName }：国家用 shortName 填 countryShortName，省份用 code 填 region2Id */
export interface ProductRegionOption {
  code: string | number
  name: string
  shortName?: string
}

export interface ProductRegions {
  origin_countries: ProductRegionOption[]
  cn_provinces: ProductRegionOption[]
  manufacturing_countries: ProductRegionOption[]
}

/** 尺码表模板（用户保存的尺码表，businessId 填 sizeTemplateIds） */
export interface ProductSizeTemplate {
  businessId: number
  name: string
  generalSizeType?: number
  sizeTypeName?: string
  sizes: string[]
  rowCount?: number
  elements?: Array<{ id: number; name: string; unit?: string }>
  updatedAt?: number | null
}

export interface ProductConstants {
  sku_classification: Array<{ label: string; value: number }>
  sensitive_types: Array<{ label: string; value: number }>
}

export interface ProductSizeSpecConfig {
  ok?: boolean
  data?: {
    groupIdConfigResp?: {
      siteId2GroupIdsMap?: Record<string, number[]>
      euGroupId?: number
      ukGroupId?: number
      usGroupId?: number
      brGroupId?: number
      mxGroupId?: number
      jpGroupId?: number
      krGroupId?: number
    }
    sizeSpecMetaVO?: {
      groups?: Array<{ id: number; name: string }>
      elements?: Array<{ id: number; name: string; unit?: string }>
    }
  } | null
}

export interface ProductDescribeResult {
  summary: Record<string, unknown>
  fields: ProductTemplateField[]
  missing: string[]
  colors: ProductSpecOption[]
  sizes: ProductSpecOption[]
  colorGroups: ProductSpecGroupItem[]
  sizeGroups: ProductSpecGroupItem[]
  sizeTypes: ProductSizeType[]
  /** v1.2.4 官方三层：标准 → 类型 → 尺码值（UI 必须用这个） */
  sizeTree?: Record<string, Record<string, ProductSpecOption[]>>
  /** 尺码标准映射（模板 groupId → sizeSpec groupId），用于欧美/亚洲归并 */
  sizeGroupConfig?: {
    euGroupId?: number
    ukGroupId?: number
    usGroupId?: number
    brGroupId?: number
    mxGroupId?: number
    jpGroupId?: number
    krGroupId?: number
    propValueGroupId2SizeSpecGroupIdMap?: Record<string, number>
  } | null
  /** 诊断：specId 是否与 sizeTypeConfig 同一套 */
  sizeTypeDebug?: {
    sampleSpecIds: Array<number | undefined>
    specIdCount: number
    matchedSizes: number
    totalSizes: number
    rawSizeTypes: number[]
  }
}

export interface ProductWarehouseRoute {
  siteId: number
  /** 仓库 ID 是字符串（如 WH-02192184443012443），不是数字 */
  warehouseIds: string[]
}

export interface ProductStoreEnv {
  siteId?: number
  warehouseIds?: string[]
  freightTemplateId?: string
  freightTemplates?: Array<{ freightTemplateId?: string; templateName?: string }>
  /** 站点 ↔ 发货仓 的绑定关系（官网「选站点与发货仓关系」那份数据） */
  warehouseRoutes?: ProductWarehouseRoute[]
  /** 发货仓（含名称与覆盖站点） */
  warehouses?: Array<{ warehouseId: string; warehouseName: string; siteIds: number[]; siteNames: string[] }>
  /** 诊断：过滤前仓库总数（0 表示接口没返回，>0 表示过滤没匹配上） */
  warehousesAllCount?: number
  warehousesError?: string
  templateId?: string | null
  templateName?: string
}

export interface ProductMaterialImage {
  id?: number | null
  imgUrl: string
  name?: string
  /** 素材 md5，用于去重 */
  md5?: string
  width?: number
  height?: number
  /** 字节数（接口字段 materialSize） */
  size?: number
  /** 接口直接返回的比例，如 3:4 */
  proportion?: string
  materialType?: number
  uploadStatus?: number
  failReason?: string
  /** 视频转码后的 URL（materialType=0 时有值） */
  videoUrl?: string
  /** 视频时长（毫秒） */
  duration?: number
  /** 视频素材 ID（goods-video-tag#…） */
  vid?: string
}

export interface ProductCreateForm {
  catPath: number[]
  siteId: number
  name: string
  enName: string
  selected: Record<string, string | number>
  colorVid: number
  sizeVid: number
  images: string[]
  materialImgUrl: string
  skuPrice: number
  stock: number
  extCode: string
  shipmentLimit: string
  /** 尺码表「蓝本」id：SDK 会先 createSizeSpec 生成新表再提交 */
  sizeTemplateSourceId?: number
  /** 1 均码 / 2 常规 / 3 Tall，供 SDK 预检比对模板类型 */
  generalSizeType?: number
}

export interface ProductPreviewResult {
  form: ProductCreateForm
  size: number
  property_count: number
  summary: Record<string, unknown>
}

export interface ProductCreateResult {
  ok: boolean
  dry_run: boolean
  stage: string
  draft_id: string | null
  product_id: string | null
  skc_list: unknown
  property_count: number
  request_size: number
  form: ProductCreateForm
}

export interface ProductBatchItem {
  index: number
  name: string
  status: string
  error: string
  product_id: string | null
}

export interface ProductBatchState {
  status: string
  total?: number
  done?: number
  succeeded?: number
  failed?: number
  dry_run?: boolean
  current?: string
  items?: ProductBatchItem[]
  started_at?: number
  finished_at?: number | null
}

export interface AdminUserStoreBinding {
  bound: boolean
  has_credential: boolean
  status?: string
  account_masked?: string
  mall_id?: string | null
  mall_name?: string
  mall_list?: Array<{ mallId?: string; mallName?: string }>
  login_at?: string | null
  last_seen_at?: string | null
  expires_at?: string | null
  cooldown_until?: string | null
  cooldown_seconds?: number
  last_error?: string
  last_error_code?: string
  remember?: boolean
  updated_at?: string | null
}

export interface StoreGoodsFilterCount {
  type: number
  name: string
  count: number
}

export interface StoreGoodsFilters {
  filters: StoreGoodsFilterCount[]
  todo_total: number
  todo_items: Array<Record<string, unknown>>
}

export interface StoreGoodsLifecycleSkc {
  skc_id: string
  color_name: string
  select_status: number | null
  status_name: string
  status_desc: string
  can_on_sale: boolean | null
  can_off_sale: boolean | null
  stages: StoreGoodsStage[]
}

export interface StoreGoodsProperty {
  name: string
  value: string
  important: boolean
}

export interface StoreGoodsLifecycle {
  product_id: string
  product_name?: string
  available?: boolean
  properties?: StoreGoodsProperty[]
  skc_list: StoreGoodsLifecycleSkc[]
}

export interface StoreGoodsSku {
  product_sku_id: string
  thumb: string | null
}

export interface StoreGoodsSkc {
  product_skc_id: string
  color_image: string | null
  preview_images: string[]
  skus: StoreGoodsSku[]
}

export interface StoreGoodsDetail {
  product_id: string
  product_name: string
  main_image: string
  carousel: string[]
  material_image: string | null
  outer_package: string[]
  decoration: string[]
  videos: { carousel: unknown[]; detail: unknown[] }
  skc_list: StoreGoodsSkc[]
  categories: Record<string, { catName?: string; catEnName?: string }> | null
  i18n: Array<{ language: string | null; name: string | null }>
}

export interface StoreKefuOption {
  code: number | null
  desc: string
  /** 「其他」选项需要用户补具体原因（2008） */
  need_input: boolean
}

export interface StoreKefuTool {
  name: string
  tool_id: string
  data_id_type: number | null
  data_type: string
  enabled: boolean
  only_fully_hosted: boolean
  option_title: string | null
  options: StoreKefuOption[]
  rate_limit_ms: number | null
  success_tip: string | null
  fail_tip: string | null
}

export interface StoreKefuCard {
  msg_id: string
  tool_id: string
  data_id_type: number | null
  data_type: string
  data_type_names: string[]
  option_title: string | null
  options: StoreKefuOption[]
  popup: { title?: string; confirmBtnText?: string } | null
  card: Record<string, unknown> | null
}

export interface StoreKefuMessage {
  msg_id: string
  content_type: number | null
  content_type_name: string
  timestamp: number | null
  content: string
}

export interface StoreKefuQueryResult {
  ok: boolean
  code: string
  message: string
  found: boolean
  product_name: string
  product_picture: string
  product_skc_id: string
  product_id: string
  algo_product_id: string
  is_semi: boolean | null
  is_cloth: boolean | null
  has_bonded_or_abroad_stock: boolean | null
}

/**
 * preview=待确认，sent=已提交，accepted=平台处理中，
 * degraded=降级（未进同步执行器，将唤醒卡片后重试），
 * retry_pending=失败已排队重试，failed=执行失败，rejected=平台拒绝（不可重试），
 * replied=已完成，unknown=状态未知（平台回了无法归类的话术）
 */
export type StoreKefuActionStage =
  | 'preview'
  | 'sent'
  | 'accepted'
  | 'degraded'
  | 'retry_pending'
  | 'failed'
  | 'rejected'
  | 'replied'
  | 'unknown'

export interface StoreKefuAction {
  id: string
  user_id: number | null
  account_masked: string
  tool_id: string
  tool_name: string
  data_type: number
  data_type_name: string
  data_id: string
  reason_code: number | null
  reason_detail: string
  stage: StoreKefuActionStage
  parent_msg_id: string
  intercept_code: number | null
  intercept_message: string
  msg_id: string
  error: string
  created_at: string | null
  sent_at: string | null
  finished_at: string | null
  preview: Record<string, unknown> | null
  product_snapshot: {
    product_name?: string
    product_picture?: string
    product_skc_id?: string
    product_id?: string
  } | null
  request_body: Record<string, unknown> | null
  response: Record<string, unknown> | null
  robot_reply: { contentType?: number; msgId?: number; content?: string } | null
}

export interface StoreKefuActionPayload {
  tool_id?: string
  tool_name?: string
  data_type?: string
  data_id: string
  reason_code?: number | null
  reason_detail?: string
  parent_msg_id?: string
}

export interface StoreKefuQueryBatchItem {
  data_id: string
  ok: boolean
  found: boolean
  product_name: string
  product_picture: string
  product_skc_id: string
  product_id: string
  message: string
  error_code: string
}

export interface StoreKefuQueryBatchResult {
  data_type: string
  total: number
  items: StoreKefuQueryBatchItem[]
  truncated: boolean
}

export interface StoreKefuBatchItem {
  action_id: string
  data_id: string
  data_type_name: string
  stage: StoreKefuActionStage
  product_name: string
  product_picture: string
  msg_id: string
  error: string
  intercept_code: number | null
  robot_reply: { contentType?: number; msgId?: number; content?: string } | null
  created_at: string | null
  finished_at: string | null
}

export type StoreKefuBatchStatus = 'queued' | 'running' | 'done' | 'cancelled' | 'failed'

export interface StoreKefuBatch {
  id: string
  user_id: number | null
  account_masked: string
  tool_id: string
  tool_name: string
  data_type_name: string
  reason_code: number | null
  reason_detail: string
  status: StoreKefuBatchStatus
  total: number
  succeeded: number
  failed: number
  skipped: number
  error: string
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

export interface StoreKefuBatchProgress {
  total: number
  processed: number
  succeeded: number
  failed: number
  pending: number
  /** 已发送但还没拿到机器人回执的数量 */
  awaiting_reply: number
}

export interface StoreKefuBatchView {
  batch: StoreKefuBatch
  items: StoreKefuBatchItem[]
  progress: StoreKefuBatchProgress
}

export interface StoreKefuBatchPayload {
  tool_id?: string
  tool_name?: string
  data_type?: string
  ids: string
  reason_code?: number | null
  reason_detail?: string
}

export interface AppSettings {
  order: {
    headcost: Record<string, number>
    operation_fee: number
    extra_item_fee: number
    tail_fee: number
    shipping_subsidy: number
  }
  activity: {
    headcost: number
    operation_fee: number
    uplift_limit: number
    set_prices: Record<string, number>
    single_tiers: Array<{ min_price: number; profit: number }>
    id_profit_rules: ActivityIdProfitRule[]
    default_skc_rules: ActivitySkuRules
  }
}

export interface RegionSummary {
  id: number
  code: string
  name: string
  currency: string
  enabled: boolean
  is_default: boolean
  sort_order: number
}

export type CategoryTemplateType = 'set_based' | 'no_set' | 'custom_set'

export interface CategoryBrief {
  id: number
  code: string
  name: string
  template_type: CategoryTemplateType
  template_label: string
  set_types: number[]
}

export interface CategorySummary extends CategoryBrief {
  // null = 全部区域开放；数组 = 开放的区域代码列表
  allowed_regions: string[] | null
  // 绑定的库存类目（A/B...；null = 默认 A）
  inventory_category?: string | null
  enabled: boolean
  is_default: boolean
  sort_order: number
  created_at?: string | null
  updated_at?: string | null
}

export interface TemplateTypeInfo {
  label: string
  description: string
  customizable: boolean
}

export interface CategoryPage {
  items: CategorySummary[]
  template_types: Record<CategoryTemplateType, TemplateTypeInfo>
}

export interface RegionProfile extends RegionSummary {
  order_strategy: string
  activity_strategy: string
  order_version: number
  activity_version: number
  category: CategoryBrief
  settings: AppSettings
}

export interface SystemSettings {
  task_workers: number
  task_queue_limit: number
  activity_workers: number
  activity_queue_limit: number
  cleanup_enabled: boolean
  cleanup_retention_days: number
  audit_retention_days: number
  /** 店铺客服写操作总熔断：关闭后所有人只能预览不能提交 */
  kefu_writes_enabled: boolean
  /** 平台「系统忙碌」这类临时失败是否自动重试 */
  kefu_auto_retry_enabled: boolean
  /** 店铺登录日志保留天数 */
  store_login_log_retention_days: number
  /** 客服操作审计保留天数 */
  kefu_action_retention_days: number
}

export interface TaskPoolStats {
  workers: number
  queue_limit: number
  queued: number
  active: number
}

export interface SystemSettingsInfo {
  settings: SystemSettings
  pools: { orders: TaskPoolStats; activities: TaskPoolStats }
  cleanup: {
    interval_seconds: number
    last_run_at: string | null
    last_result: CleanupResult | null
  }
}

export interface CleanupResult {
  skipped?: boolean
  reason?: string
  removed_task_dirs: number
  removed_activity_dirs: number
  pruned_audit_logs: number
  retention_days: number
}

export interface AuditLogItem {
  id: number
  actor_id: number | null
  actor_username: string
  action: string
  target_type: string
  target_id: string
  detail: string
  ip: string
  created_at: string
}

export interface AuditLogPage {
  total: number
  items: AuditLogItem[]
  actions: string[]
}

export interface MonitoringSnapshot {
  disk: {
    path: string
    total: number
    used: number
    free: number
    percent: number
  }
  memory: {
    available: boolean
    total?: number
    used?: number
    free?: number
    percent?: number
    process_rss?: number
  }
  task_pools: { orders: TaskPoolStats; activities: TaskPoolStats }
  task_counts: { orders: Record<string, number>; activities: Record<string, number> }
  storage: {
    tasks_dir: string
    tasks_bytes: number
    tasks_size: string
    activities_dir: string
    activities_bytes: number
    activities_size: string
  }
  database: { status: string }
}
export interface UpdateCommit {
  sha: string
  short: string
  subject: string
  date: string
}

export interface UpdateRemote {
  at: number
  sha: string
  subject: string
  date?: string
  error?: string | null
}

export interface UpdateIncomingCommit {
  short: string
  subject: string
}

export interface UpdateProjectState {
  dir: string
  branch: string
  commit: UpdateCommit
  dirty: boolean
  remote: UpdateRemote
  behind: number
  fast_forward: boolean
  update_available: boolean
  incoming: UpdateIncomingCommit[]
}

export interface UpdateEnvInfo {
  internal_db: boolean
  web_port: string
  state_dir: string
  health_urls: string[]
}

export interface UpdateServiceState {
  name: string
  state: string
  status: string
  health: string
  image: string
  image_id: string
  started_at: string
}

export interface UpdateImageState {
  ref: string
  present: boolean
  id: string
  short: string
  created: string
}

export interface UpdateToolVersions {
  docker: string
  compose: string
  git: string
}

export interface UpdateRollbackImage {
  ref: string
  previous: string
  id: string
  tagged: boolean
}

export interface UpdateRollbackPoint {
  available: boolean
  commit: string
  short: string
  at: string
  images: Record<string, UpdateRollbackImage>
}

export interface UpdateJobStep {
  label: string
  state: 'done' | 'running' | 'pending' | 'failed'
}

export interface UpdateJob {
  id: string
  kind: 'update' | 'rollback'
  title: string
  status: 'running' | 'ok' | 'failed'
  started_at: string
  finished_at: string
  message: string
  detail: Record<string, string>
  current: number
  total: number
  steps: UpdateJobStep[]
  lines: string[]
  line_count: number
}

export interface UpdateResult {
  kind?: string
  status?: string
  message?: string
  at?: string
  before?: string
  after?: string
  images?: string
  step?: string
  target?: string
}

export interface UpdateSnapshot {
  updater: { version: string; time: string; busy: boolean; ready: boolean }
  version?: string
  remote_version?: string
  notes: string[]
  project: UpdateProjectState
  env: UpdateEnvInfo
  services: UpdateServiceState[]
  images: Record<string, UpdateImageState>
  tools: UpdateToolVersions
  last_result: UpdateResult
  rollback: UpdateRollbackPoint
}

export interface UpdateStatusResponse {
  enabled: boolean
  url: string
  error: string
  data: UpdateSnapshot | null
  job: UpdateJob | null
}

export interface UpdateJobResponse {
  enabled: boolean
  job: UpdateJob | null
  last_result: UpdateResult
  busy: boolean
}

export interface UpdateStartResponse {
  enabled: boolean
  job: UpdateJob | null
  action: string
}

export interface VersionGroup {
  title: string
  items: string[]
}

export interface VersionEntry {
  version: string
  date: string
  groups: VersionGroup[]
}

export interface VersionInfo {
  version: string
  commit: string
  commit_date: string
  changelog: VersionEntry[]
}
