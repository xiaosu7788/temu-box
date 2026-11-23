<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ArrowDown, Refresh, Search } from '@element-plus/icons-vue'
import { getStoreGoods, getStoreGoodsFilters, getStoreSites } from '../api'
import { notifyError, notifySuccess, notifyWarning } from '../feedback'
import type { StoreGoodsFilters, StoreGoodsItem, StoreGoodsPage, StoreSite } from '../types'
import CopyableText from './CopyableText.vue'
import GoodsDetailDrawer from './GoodsDetailDrawer.vue'

const sites = ref<StoreSite[]>([])
const sitesLoading = ref(false)
const loading = ref(false)
const result = ref<StoreGoodsPage | null>(null)
const page = ref(1)
const pageSize = ref(20)
const site = ref('')
/** 生命周期状态筛选：'' 全部 / 9 待选品 / 10 待处理 / 12 已加入站点 / 13 待价格校验 */
const selectStatus = ref<number | ''>('')
/** 搜索类型：由用户明确选择（不再做自动识别） */
const searchType = ref<'name' | 'spu' | 'skc' | 'sku' | 'extcode'>('name')
/** 时间范围：类型 1 创建 / 4 价格确认 / 6 加入站点 / 7 下架（默认创建时间） */
const timeType = ref<number | ''>(1)
const dateRange = ref<[string, string] | null>(null)
const filtersLoading = ref(false)
const stats = ref<StoreGoodsFilters | null>(null)
const keyword = ref('')
const appliedKeyword = ref('')

const drawerVisible = ref(false)
const activeProductId = ref<string | null>(null)
/** 表格勾选（批量复制 ID / 批量投喂客服工具用） */
const selectedRows = ref<StoreGoodsItem[]>([])

const copyKinds = [
  { value: 'spu', label: 'SPU' },
  { value: 'skc', label: 'SKC' },
  { value: 'sku', label: 'SKU' },
  { value: 'ext', label: 'SKC 货号' },
  { value: 'sku_ext', label: 'SKU 货号' },
] as const

async function copySelected(kind: string) {
  const values: string[] = []
  for (const row of selectedRows.value) {
    if (kind === 'spu') values.push(row.spu_id || row.product_id)
    else if (kind === 'skc') values.push(row.skc_id)
    else if (kind === 'sku') values.push(...(row.sku_ids || []))
    else if (kind === 'sku_ext') values.push(...(row.sku_ext_codes || []))
    else values.push(row.ext_code)
  }
  const unique = Array.from(new Set(values.filter(Boolean)))
  if (!unique.length) {
    notifyWarning('选中的行没有该类 ID')
    return
  }
  const label = copyKinds.find((item) => item.value === kind)?.label ?? kind
  try {
    await navigator.clipboard.writeText(unique.join('\n'))
    notifySuccess(`已复制 ${unique.length} 个 ${label}（每行一个）`)
  } catch {
    notifyWarning('复制失败，请检查浏览器剪贴板权限')
  }
}

function clearSelection() {
  selectedRows.value = []
}

const items = computed(() => result.value?.items ?? [])
const total = computed(() => result.value?.total ?? 0)
const keywordHint = computed(() => {
  if (!appliedKeyword.value || !result.value) return ''
  const scanned = result.value.scanned ?? 0
  const suffix = result.value.truncated ? '（已达扫描上限，结果可能不完整）' : ''
  return `关键词「${appliedKeyword.value}」在全量 ${scanned} 条 SKC 中命中 ${result.value.total} 条${suffix}`
})

async function loadSites(force = false) {
  sitesLoading.value = true
  try {
    sites.value = await getStoreSites(force)
  } catch (error) {
    notifyError(error)
  } finally {
    sitesLoading.value = false
  }
}

async function load(force = false) {
  loading.value = true
  try {
    const [begin, end] = dateRange.value ?? []
    result.value = await getStoreGoods({
      page: page.value,
      page_size: pageSize.value,
      site: site.value || undefined,
      keyword: appliedKeyword.value || undefined,
      search_type: searchType.value,
      select_status: selectStatus.value === '' ? undefined : selectStatus.value,
      time_type: timeType.value === '' ? undefined : timeType.value,
      time_begin: begin ? new Date(`${begin}T00:00:00`).getTime() : undefined,
      time_end: end ? new Date(`${end}T00:00:00`).getTime() + 86_399_999 : undefined,
      force: force || undefined,
    })
  } catch (error) {
    notifyError(error)
    result.value = null
  } finally {
    loading.value = false
  }
}

function search() {
  const value = keyword.value.trim()
  // SPU / SKC / SKU 只能是数字（批量用空格、逗号、换行分隔）
  if (['spu', 'skc', 'sku'].includes(searchType.value) && value && !/^\d[\d\s,，、|]*$/.test(value)) {
    notifyWarning('SPU / SKC / SKU 只能填数字，多个用空格或逗号分隔')
    return
  }
  appliedKeyword.value = value
  page.value = 1
  void load()
}

function resetKeyword() {
  keyword.value = ''
  appliedKeyword.value = ''
  page.value = 1
  void load()
}

function changeSite() {
  page.value = 1
  void load()
}

async function loadStats() {
  if (filtersLoading.value) return
  filtersLoading.value = true
  try {
    stats.value = await getStoreGoodsFilters()
  } catch (error) {
    notifyError(error)
    stats.value = null
  } finally {
    filtersLoading.value = false
  }
}

function changeStatus() {
  page.value = 1
  void load()
}

function changePageSize() {
  page.value = 1
  void load()
}

function openDetail(item: StoreGoodsItem) {
  activeProductId.value = item.product_id
  drawerVisible.value = true
}

function statusOf(item: StoreGoodsItem): { text: string; type: 'success' | 'info' | 'warning' | 'danger' } {
  const removed = item.remove_status !== null && item.remove_status !== undefined && item.remove_status !== 0
  if (removed) {
    return { text: String(item.remove_status) === '1' ? '已删除' : '删除中', type: 'info' }
  }
  // 生命周期状态（selectStatus）：12 已加入站点，9/10 待处理类，13 待价格校验
  if (item.select_status === 12) return { text: item.status_name || '已加入站点', type: 'success' }
  if (item.select_status === 9 || item.select_status === 10) {
    return { text: item.status_name || '待处理', type: 'warning' }
  }
  if (item.select_status === 13) return { text: item.status_name || '待价格校验', type: 'info' }
  if (item.select_status === null || item.select_status === undefined) {
    return { text: '未知', type: 'info' }
  }
  return { text: item.status_name || `状态 ${item.select_status}`, type: 'warning' }
}

function statusTip(item: StoreGoodsItem): string {
  return item.status_desc || item.status_name || ''
}

function formatNumber(value: number | null): string {
  if (value === null || value === undefined) return '—'
  return value.toLocaleString('zh-CN')
}

onMounted(async () => {
  await loadSites()
  await load()
})
</script>

<template>
  <div class="goods-panel">
    <div class="goods-fixed">
    <div class="goods-toolbar">
      <el-select
        v-model="site"
        class="goods-site"
        placeholder="全部站点"
        :loading="sitesLoading"
        clearable
        filterable
        @change="changeSite"
      >
        <el-option label="全部站点" value="" />
        <el-option
          v-for="item in sites"
          :key="item.site_id"
          :label="`${item.site_name}（${item.site_id}）`"
          :value="item.site_id"
        />
      </el-select>

      <el-select
        v-model="selectStatus"
        class="goods-site"
        placeholder="全部生命周期"
        clearable
        @change="changeStatus"
      >
        <el-option label="全部生命周期" value="" />
        <el-option label="已加入站点" :value="12" />
        <el-option label="价格申报中" :value="13" />
        <el-option label="未发布到站点" :value="10" />
        <el-option label="已下架" :value="9" />
      </el-select>

      <el-select v-model="searchType" class="goods-site" placeholder="搜索类型">
        <el-option label="商品名" value="name" />
        <el-option label="SPU" value="spu" />
        <el-option label="SKC" value="skc" />
        <el-option label="SKU" value="sku" />
        <el-option label="货号" value="extcode" />
      </el-select>

      <el-input
        v-model="keyword"
        class="goods-keyword"
        placeholder="商品名 / 批量 ID（空格、逗号、换行分隔）/ SKC 货号"
        clearable
        :prefix-icon="Search"
        @keyup.enter="search"
        @clear="resetKeyword"
      />

      <el-select v-model="timeType" class="goods-site" placeholder="时间类型" clearable @change="changeStatus">
        <el-option label="不限时间" value="" />
        <el-option label="创建时间" :value="1" />
        <el-option label="价格确认时间" :value="4" />
        <el-option label="加入站点时间" :value="6" />
        <el-option label="下架时间" :value="7" />
      </el-select>

      <div class="goods-date-wrap">
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          :disabled="timeType === ''"
          @change="changeStatus"
        />
      </div>

      <el-button type="primary" :icon="Search" :loading="loading" @click="search">搜索</el-button>
      <el-button :icon="Refresh" :loading="loading" @click="load(true)">刷新</el-button>
      <el-button :loading="filtersLoading" @click="loadStats">统计</el-button>
    </div>

    <el-alert
      v-if="stats"
      class="goods-hint"
      type="info"
      :closable="false"
      show-icon
      :title="`待办 ${stats.todo_total} 项 · 快捷筛选：${stats.filters.filter((item) => item.count > 0).map((item) => `${item.name} ${item.count}`).join(' / ') || '暂无数据'}`"
    />

    <div class="goods-bulk">
      <span class="goods-bulk__count">已选 {{ selectedRows.length }} 条</span>
      <el-dropdown trigger="click" :disabled="!selectedRows.length" @command="copySelected">
        <el-button size="small" type="primary">
          批量复制所选 ID<el-icon class="el-icon--right"><ArrowDown /></el-icon>
        </el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-for="item in copyKinds" :key="item.value" :command="item.value">
              {{ item.label }}
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <el-button size="small" text :disabled="!selectedRows.length" @click="clearSelection">取消选择</el-button>
    </div>

    <el-alert v-if="keywordHint" class="goods-hint" type="info" :closable="false" show-icon :title="keywordHint" />
    </div>

    <el-alert
      v-if="result?.source === 'none'"
      class="goods-hint"
      type="error"
      :closable="false"
      show-icon
      title="商品列表加载失败：店铺会话可能已失效，请重新登录后重试"
    />

    <el-table
      v-loading="loading"
      :data="items"
      row-key="product_skc_id"
      class="goods-table"
      @row-dblclick="openDetail"
      @selection-change="(rows: StoreGoodsItem[]) => (selectedRows = rows)"
    >
      <el-table-column type="selection" width="40" fixed="left" />

      <!-- 商品信息（图 + 颜色 + 预览图 + 价格 + 名称 + 类目 + 站点 + SPU） -->
      <el-table-column label="商品信息" min-width="360">
        <template #default="scope">
          <div class="g-info">
            <el-image
              v-if="scope.row.main_image"
              :src="scope.row.main_image"
              :preview-src-list="[scope.row.main_image]"
              fit="cover"
              class="g-thumb"
              preview-teleported
            />
            <div class="g-body">
              <div class="g-color">{{ scope.row.color_name || '—' }}</div>
              <div v-if="(scope.row.preview_images || []).length" class="g-previews">
                <el-image
                  v-for="img in scope.row.preview_images"
                  :key="img"
                  :src="img"
                  fit="cover"
                  class="g-preview"
                  :preview-src-list="scope.row.preview_images"
                  preview-teleported
                />
              </div>
              <div class="g-price">{{ scope.row.supplier_price || '—' }}</div>
              <div class="g-name">{{ scope.row.product_name || '（无标题）' }}</div>
              <div class="g-cat">
                {{ (scope.row.full_category || []).join(' / ') || scope.row.category_name || '—' }}
              </div>
              <div class="g-meta">站点：{{ (scope.row.site_names || []).join('、') || '—' }}</div>
              <div class="g-meta">
                SPU：<CopyableText :text="scope.row.spu_id || scope.row.product_id" />
              </div>
            </div>
          </div>
        </template>
      </el-table-column>

      <!-- SKC 属性 -->
      <el-table-column label="SKC 属性" min-width="210">
        <template #default="scope">
          <div class="g-kv">
            <CopyableText :text="scope.row.skc_id" />
            <div>颜色：{{ scope.row.color_name || '—' }}</div>
            <div>申报价格：{{ scope.row.supplier_price || '—' }}</div>
            <div>
              状态：
              <el-tag :type="statusOf(scope.row).type" size="small" effect="plain">
                {{ statusOf(scope.row).text }}
              </el-tag>
            </div>
            <div>货号：{{ scope.row.ext_code || '—' }}</div>
          </div>
        </template>
      </el-table-column>

      <!-- SKU 属性集 -->
      <el-table-column label="SKU 属性集" min-width="190">
        <template #default="scope">
          <div v-if="scope.row.sku_count" class="g-skus">
            <div v-for="sku in scope.row.sku_items.slice(0, 3)" :key="sku.sku_id" class="g-sku">
              <CopyableText :text="sku.sku_id" />
              <span class="g-sku__ext">货号：{{ sku.ext_code || '—' }}</span>
            </div>
            <div v-if="scope.row.sku_count > 3" class="g-more">… 共 {{ scope.row.sku_count }} 个 SKU</div>
          </div>
          <span v-else class="goods-num">—</span>
        </template>
      </el-table-column>

      <!-- 加站详情 -->
      <el-table-column label="加站详情" width="130">
        <template #default="scope">
          <div class="g-meta">已加入站点：{{ scope.row.added_site_count || 0 }}</div>
          <div class="g-meta">{{ (scope.row.site_names || []).join('、') || '—' }}</div>
        </template>
      </el-table-column>

      <!-- 待关注信息（状态已在上方 SKC 属性里，这里不重复） -->
      <el-table-column label="待关注信息" min-width="200">
        <template #default="scope">
          <div class="g-table">
            <div class="g-table__head">
              <span>站点</span><span>申报价格</span><span>加站</span>
            </div>
            <div class="g-table__row">
              <span>{{ (scope.row.site_names || [])[0] || '—' }}</span>
              <span>{{ scope.row.supplier_price || '—' }}</span>
              <span>{{ scope.row.added_site_count ? `已加 ${scope.row.added_site_count}` : '—' }}</span>
            </div>
          </div>
        </template>
      </el-table-column>

      <el-table-column label="操作" width="110" fixed="right">
        <template #default="scope">
          <el-button link type="primary" size="small" @click="openDetail(scope.row)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty
      v-if="!loading && !items.length"
      :description="appliedKeyword ? '没有匹配的商品' : '该站点下暂无商品'"
    />

    <div v-if="total" class="goods-pager">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        background
        @current-change="() => load()"
        @size-change="changePageSize"
      />
    </div>


    <GoodsDetailDrawer v-model="drawerVisible" :product-id="activeProductId" />
  </div>
</template>

<style scoped>
.goods-fixed {
  position: sticky;
  top: 70px;
  z-index: 15;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 0;
  background: var(--bg-body, #f5f7fa);
}

.g-info {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.g-thumb {
  width: 84px;
  height: 84px;
  border-radius: 6px;
  flex: none;
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
}

.g-body {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.g-color {
  font-size: 12px;
  color: var(--text-muted, #9ca3af);
}

.g-previews {
  display: flex;
  gap: 4px;
}

.g-preview {
  width: 26px;
  height: 26px;
  border-radius: 4px;
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
}

.g-price {
  font-size: 13px;
  font-weight: 600;
  color: #e6a23c;
}

.g-name {
  font-size: 13px;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.g-cat,
.g-meta {
  font-size: 12px;
  color: var(--text-muted, #9ca3af);
}

.g-kv {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
}

.g-skus {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.g-sku {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12px;
}

.g-sku__ext,
.g-more {
  font-size: 12px;
  color: var(--text-muted, #9ca3af);
}

.g-table {
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 6px;
  font-size: 12px;
  overflow: hidden;
}

.g-table__head,
.g-table__row {
  display: grid;
  grid-template-columns: 1fr 1fr 0.8fr;
}

.g-table__head {
  background: var(--el-fill-color-light, #f5f7fa);
  font-weight: 600;
}

.g-table__head span,
.g-table__row span {
  padding: 4px 6px;
}
.goods-bulk {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--el-color-primary-light-9, #ecf5ff);
  border: 1px solid var(--el-color-primary-light-7, #a0cfff);
}

.goods-bulk__count {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-color-primary, #409eff);
}

.goods-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.goods-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.goods-site {
  width: 200px;
}

/* 日期区间：外层容器定宽，内部 el-date-editor 自适应撑满（直接给组件加 width 会被 Element 的变量覆盖） */
.goods-date-wrap {
  width: 300px;
  flex: 0 0 300px;
}

.goods-date-wrap :deep(.el-date-editor) {
  width: 100%;
  box-sizing: border-box;
}

.goods-keyword {
  width: min(420px, 100%);
}

.goods-hint {
  margin: 0;
}

.goods-table {
  width: 100%;
}

.goods-thumb {
  width: 44px;
  height: 44px;
  border-radius: 8px;
  background: #f3f4f6;
  display: block;
}

.goods-thumb--empty {
  border: 1px dashed var(--border, #e5e7eb);
}

.goods-name {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.goods-ext {
  display: block;
  color: var(--text-muted, #9ca3af);
  font-size: 11px;
  font-family: Consolas, Menlo, monospace;
}

.goods-num {
  font-variant-numeric: tabular-nums;
}

.goods-pager {
  display: flex;
  justify-content: flex-end;
}


@media (max-width: 760px) {
  .goods-site,
  .goods-keyword {
    width: 100%;
  }

  .goods-date-wrap {
    width: 100%;
    flex: 1 1 100%;
  }
}
</style>
