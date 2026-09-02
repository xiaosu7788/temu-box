export interface InventoryStatus {
  path: string
  exists: boolean
  cache_exists: boolean
  cache_valid: boolean
  sku_count: number
  size: number | null
  modified_at: string | null
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
}

export type ActivitySingleParseMode = 'first_segment' | 'last_segment' | 'after_marker'

export interface ActivitySetMapping {
  pattern: string
  pieces: number
}

export interface ActivitySkuRules {
  set_keywords: string[]
  set_mappings: ActivitySetMapping[]
  single_mode: ActivitySingleParseMode
  single_delimiter: string
  single_marker: string
}

export interface ActivitySkuPreviewItem {
  row: number
  skc: string
  result: '单品' | '套装' | '无法识别'
  value: number | null
  base_price: number | null
  method: string
}

export interface ActivitySkuPreview {
  sheet: string
  header_row: number
  total_rows: number
  single_rows: number
  set_rows: number
  unrecognized_rows: number
  preview_limit: number
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

export interface RegionProfile extends RegionSummary {
  order_strategy: string
  activity_strategy: string
  order_version: number
  activity_version: number
  settings: AppSettings
}
