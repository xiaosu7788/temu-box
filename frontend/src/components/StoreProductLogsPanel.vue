<script setup lang="ts">
/** 建品记录：操作记录页的「商品创建」标签。按成功/失败分类，失败带原因。 */
import { computed, onMounted, reactive, ref } from 'vue'
import { getProductLogs } from '../api'
import { notifyError } from '../feedback'

interface LogRow {
  id: number
  product_name: string
  status: string
  error: string
  product_id: number | null
  batch: boolean
  created_at: string
}

const items = ref<LogRow[]>([])
const loading = ref(false)
const stats = reactive({ total: 0, ok: 0, failed: 0 })

const query = reactive({ status: '', keyword: '', page: 1, size: 20 })

const tone = computed(() => (stats.failed ? 'warning' : 'success'))

function fmtTime(v: string) {
  if (!v) return ''
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return v
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function load() {
  loading.value = true
  try {
    const r = await getProductLogs({
      status: query.status || undefined,
      keyword: query.keyword.trim() || undefined,
      page: query.page,
      size: query.size,
    })
    items.value = r.items
    stats.total = r.total
    stats.ok = r.ok
    stats.failed = r.failed
  } catch (error) {
    notifyError(error as Error)
  } finally {
    loading.value = false
  }
}

function search() {
  query.page = 1
  void load()
}

function setStatus(s: string) {
  query.status = s
  search()
}

function onPage(p: number) {
  query.page = p
  void load()
}

onMounted(load)
</script>

<template>
  <div class="plogs">
    <div class="plogs__bar">
      <div class="plogs__stats">
        <button
          type="button"
          class="plogs__chip"
          :class="{ 'plogs__chip--on': !query.status }"
          @click="setStatus('')"
        >
          全部 <b>{{ stats.total }}</b>
        </button>
        <button
          type="button"
          class="plogs__chip plogs__chip--ok"
          :class="{ 'plogs__chip--on': query.status === 'ok' }"
          @click="setStatus('ok')"
        >
          成功 <b>{{ stats.ok }}</b>
        </button>
        <button
          type="button"
          class="plogs__chip plogs__chip--fail"
          :class="{ 'plogs__chip--on': query.status === 'failed' }"
          @click="setStatus('failed')"
        >
          失败 <b>{{ stats.failed }}</b>
        </button>
      </div>
      <div class="plogs__search">
        <el-input
          v-model="query.keyword"
          placeholder="商品名关键字"
          clearable
          size="small"
          @keyup.enter="search"
          @clear="search"
        />
        <el-button size="small" type="primary" @click="search">查询</el-button>
      </div>
    </div>

    <el-table v-loading="loading" :data="items" row-key="id" size="small" empty-text="还没有建品记录">
      <el-table-column label="时间" width="112">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column prop="product_name" label="商品" min-width="240" show-overflow-tooltip />
      <el-table-column label="来源" width="88">
        <template #default="{ row }">
          <el-tag size="small" effect="plain" type="info">{{ row.batch ? '批量' : '单个' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag size="small" effect="plain" :type="row.status === 'ok' ? 'success' : 'danger'">
            {{ row.status === 'ok' ? '成功' : '失败' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="商品 ID" width="130">
        <template #default="{ row }">
          <span v-if="row.product_id">{{ row.product_id }}</span>
          <span v-else class="plogs__muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="失败原因" min-width="220">
        <template #default="{ row }">
          <span v-if="row.error" class="plogs__error">{{ row.error }}</span>
          <span v-else class="plogs__muted">—</span>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="stats.total > query.size" class="plogs__pager">
      <el-pagination
        layout="prev, pager, next"
        :current-page="query.page"
        :page-size="query.size"
        :total="stats.total"
        small
        @current-change="onPage"
      />
    </div>

    <el-progress v-if="false" :percentage="0" :status="tone" />
  </div>
</template>

<style scoped>
.plogs {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.plogs__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.plogs__stats {
  display: flex;
  gap: 8px;
}

.plogs__chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border: 1px solid var(--border, #dcdfe6);
  border-radius: 14px;
  background: transparent;
  font-size: 12px;
  color: #606266;
  cursor: pointer;
  transition:
    border-color 0.18s ease,
    background 0.18s ease,
    color 0.18s ease;
}

.plogs__chip:hover {
  border-color: #c0c4cc;
}

.plogs__chip b {
  font-variant-numeric: tabular-nums;
}

.plogs__chip--on {
  border-color: #409eff;
  background: rgba(64, 158, 255, 0.1);
  color: #409eff;
}

.plogs__chip--ok.plogs__chip--on {
  border-color: #67c23a;
  background: rgba(103, 194, 58, 0.1);
  color: #67c23a;
}

.plogs__chip--fail.plogs__chip--on {
  border-color: #f56c6c;
  background: rgba(245, 108, 108, 0.1);
  color: #f56c6c;
}

.plogs__search {
  display: flex;
  gap: 8px;
  align-items: center;
}

.plogs__search :deep(.el-input) {
  width: 200px;
}

.plogs__error {
  color: #f56c6c;
  font-size: 12px;
  line-height: 1.5;
  word-break: break-all;
}

.plogs__muted {
  color: #c0c4cc;
}

.plogs__pager {
  display: flex;
  justify-content: flex-end;
}
</style>
