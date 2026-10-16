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
  role: 'admin' | 'user'
  status: 'pending' | 'approved' | 'rejected'
  created_at?: string
  approved_at?: string
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
