<script setup lang="ts">
/**
 * 填写库存弹窗（对齐官网「填写库存」）。
 *
 * 三处共用：手动创建、批量创建的每张卡片、编辑表单模板。
 * 库存以「按仓库明细」为准，总数由明细汇总，不再单独维护一个可能对不上的总库存。
 */
import { computed, ref, watch } from 'vue'

const props = defineProps<{
  modelValue: boolean
  /** 可选的发货仓（来自当前站点的 env.warehouses） */
  warehouses: Array<{ warehouseId: string; warehouseName: string }>
  /** 选中的发货仓 */
  warehouseIds: string[]
  /** 各仓库存 {warehouseId: qty} */
  stockPerWarehouse: Record<string, number>
  /** 覆盖站点名 */
  siteName?: string
  /** 颜色 / 尺码显示（单 SKU 时用） */
  colorLabel?: string
  sizeLabel?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [v: boolean]
  'update:warehouseIds': [v: string[]]
  'update:stockPerWarehouse': [v: Record<string, number>]
  confirm: [total: number]
}>()

const batchValue = ref(0)

const localWh = computed({
  get: () => props.warehouseIds,
  set: (v: string[]) => emit('update:warehouseIds', v),
})

const total = computed(() =>
  props.warehouseIds.reduce((sum, id) => sum + (Number(props.stockPerWarehouse[id]) || 0), 0),
)

/** 一个发货仓一行 */
const rows = computed(() =>
  props.warehouseIds.map((id) => ({
    id,
    name: props.warehouses.find((w) => w.warehouseId === id)?.warehouseName || id,
  })),
)

function setStock(id: string, v: number | undefined) {
  emit('update:stockPerWarehouse', { ...props.stockPerWarehouse, [id]: Math.max(0, Number(v) || 0) })
}

/** 批量填写：把上面的数值填到所有已选仓库 */
function applyBatch() {
  const v = Math.max(0, Math.floor(Number(batchValue.value) || 0))
  const next: Record<string, number> = { ...props.stockPerWarehouse }
  for (const id of props.warehouseIds) next[id] = v
  emit('update:stockPerWarehouse', next)
}

/** 新选了仓库且没有值 → 补 0，避免表格空着 */
watch(
  () => props.warehouseIds,
  (ids) => {
    const missing = ids.filter((id) => props.stockPerWarehouse[id] === undefined)
    if (!missing.length) return
    const next = { ...props.stockPerWarehouse }
    for (const id of missing) next[id] = 0
    emit('update:stockPerWarehouse', next)
  },
)

function close() {
  emit('update:modelValue', false)
}

function confirmStock() {
  emit('confirm', total.value)
  emit('update:modelValue', false)
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="填写库存"
    width="min(1100px, calc(100vw - 32px))"
    align-center
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <div class="stockdlg__tip">
      <p>1、创建时库存非必填，若填写，请确保该商品有实物库存且库存数准确；</p>
      <p>2、若不填写库存，创建后可在【商品列表-操作栏】点击【修改库存】及时完善；</p>
      <p>3、商品发布时请务必保证库存准确，商家不得因缺货、超卖等而漏发、错发，否则将按商家协议、Temu商家规则等规定处理。</p>
    </div>

    <div class="stockdlg__section">
      <div class="stockdlg__label">站点与发货仓关系</div>
      <div class="stockdlg__body">
        <div class="stockdlg__row">
          <span class="stockdlg__hint">我的模板</span>
          <span class="stockdlg__muted">暂无模板，填写以下信息并点击右侧按钮即可保存模板</span>
          <el-button size="small" class="stockdlg__save" disabled>保存为模板</el-button>
        </div>
        <div class="stockdlg__row">
          <span class="stockdlg__field">覆盖站点</span>
          <span class="stockdlg__site">{{ siteName || '—' }}</span>
          <span class="stockdlg__req">* 卖家自发货仓</span>
          <span class="stockdlg__muted">多发货仓时，订单就近发货</span>
        </div>
        <div class="stockdlg__row">
          <span class="stockdlg__field">{{ siteName || '本店' }}</span>
          <el-select
            v-model="localWh"
            multiple
            collapse-tags
            collapse-tags-tooltip
            clearable
            filterable
            placeholder="选择发货仓"
            class="stockdlg__whselect"
          >
            <el-option
              v-for="w in warehouses"
              :key="w.warehouseId"
              :label="w.warehouseName || w.warehouseId"
              :value="w.warehouseId"
            />
          </el-select>
        </div>
      </div>
    </div>

    <div class="stockdlg__section">
      <div class="stockdlg__label">库存</div>
      <div class="stockdlg__body">
        <div class="stockdlg__row">
          <span class="stockdlg__field">当前库存</span>
          <el-input-number v-model="batchValue" :min="0" :max="999999" controls-position="right" class="stockdlg__batchinput" />
          <el-button size="small" :disabled="!warehouseIds.length" @click="applyBatch">批量填写</el-button>
        </div>

        <el-table :data="rows" size="small" class="stockdlg__table" empty-text="请先选择发货仓">
          <el-table-column label="颜色" width="110">
            <template #default>{{ colorLabel || '—' }}</template>
          </el-table-column>
          <el-table-column label="尺码" width="110">
            <template #default>
              <div>{{ sizeLabel || '—' }}</div>
              <el-tag v-if="sizeLabel === '当前 sku'" size="small" type="info">当前 sku</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="发货仓" width="130">
            <template #default="{ row }">{{ row.name }}</template>
          </el-table-column>
          <el-table-column label="当前库存">
            <template #header>
              <div>当前库存</div>
              <div class="stockdlg__colhint">（选填，若填写请确保库存数准确）</div>
            </template>
            <template #default="{ row }">
              <el-input-number
                :model-value="stockPerWarehouse[row.id] ?? 0"
                :min="0"
                :max="999999"
                size="small"
                controls-position="right"
                class="stockdlg__input"
                @update:model-value="(v: number | undefined) => setStock(row.id, v)"
              />
            </template>
          </el-table-column>
          <el-table-column label="覆盖站点" width="120">
            <template #default>{{ siteName || '—' }}</template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <template #footer>
      <span class="stockdlg__total">合计 {{ total }}</span>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" @click="confirmStock">确认</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.stockdlg__tip {
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 4px;
  padding: 10px 12px;
  margin-bottom: 14px;
}

.stockdlg__tip p {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  color: #b88230;
}

.stockdlg__section {
  display: flex;
  gap: 12px;
  margin-bottom: 14px;
}

.stockdlg__label {
  width: 110px;
  flex: none;
  padding-top: 8px;
  font-size: 13px;
  color: #606266;
  text-align: right;
}

.stockdlg__body {
  flex: 1;
  min-width: 0;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  padding: 10px 12px;
}

.stockdlg__row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 32px;
}

.stockdlg__row + .stockdlg__row {
  margin-top: 8px;
}

.stockdlg__field {
  width: 90px;
  flex: none;
  font-size: 13px;
  color: #606266;
}

.stockdlg__hint {
  font-weight: 600;
  font-size: 13px;
}

.stockdlg__muted {
  font-size: 12px;
  color: #909399;
}

.stockdlg__save {
  margin-left: auto;
}

.stockdlg__site {
  font-size: 13px;
}

.stockdlg__req {
  color: #f56c6c;
  font-size: 13px;
}

.stockdlg__whselect {
  flex: 1;
  min-width: 0;
}

.stockdlg__batchinput {
  width: 160px;
}

.stockdlg__table {
  margin-top: 10px;
}

.stockdlg__input {
  width: 100%;
}

.stockdlg__colhint {
  font-size: 11px;
  color: #c0c4cc;
  font-weight: normal;
  line-height: 1.3;
}

.stockdlg__total {
  float: left;
  line-height: 32px;
  font-size: 12px;
  color: #606266;
}
</style>
