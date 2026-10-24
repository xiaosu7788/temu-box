<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ArrowDown, ArrowUp, InfoFilled, Refresh, SetUp } from '@element-plus/icons-vue'
import { errorMessage, getSettings } from '../api'
import type { AppSettings } from '../types'

const props = defineProps<{ mode: 'order' | 'activity'; regionCode: string; categoryCode?: string }>()
const settings = ref<AppSettings | null>(null)
const loading = ref(false)
const loadError = ref('')

// 读取并持久化用户的折叠状态
const STORAGE_KEY = 'temubox:cost_rules_collapsed'
const collapsed = ref<boolean>(localStorage.getItem(STORAGE_KEY) === 'true')

function toggleCollapse() {
  collapsed.value = !collapsed.value
  localStorage.setItem(STORAGE_KEY, String(collapsed.value))
}

const orderHeadcosts = computed(() => Object.entries(settings.value?.order.headcost || {}))
const activitySetPrices = computed(() => Object.entries(settings.value?.activity.set_prices || {})
  .sort(([left], [right]) => Number(left) - Number(right)))
const activityTiers = computed(() => [...(settings.value?.activity.single_tiers || [])]
  .sort((left, right) => left.min_price - right.min_price))

function money(value: number | undefined) {
  return `¥${Number(value || 0).toFixed(2)}`
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    settings.value = await getSettings(props.regionCode, props.categoryCode)
  } catch (error) {
    loadError.value = errorMessage(error)
  } finally {
    loading.value = false
  }
}

watch(() => [props.regionCode, props.categoryCode], ([region]) => { if (region) void load() })
onMounted(() => { if (props.regionCode) void load() })
</script>

<template>
  <section class="section-band calculation-rules-band" :class="{ 'is-collapsed': collapsed }">
    <!-- 折叠状态下的紧凑条 -->
    <div v-if="collapsed && settings" class="collapsed-bar">
      <div class="collapsed-left">
        <span class="collapsed-tag">
          <el-icon><SetUp /></el-icon>
          {{ props.mode === 'order' ? '成本规则' : '活动底价规则' }}
        </span>
        <div class="collapsed-summary">
          <template v-if="props.mode === 'order'">
            <span class="formula-chip">公式: 货值+头程+操作费+续件费+尾程-补贴</span>
            <span class="metric-chip">操作费 <strong>{{ money(settings.order.operation_fee) }}</strong></span>
            <span class="metric-chip">续件费 <strong>{{ money(settings.order.extra_item_fee) }}/件</strong></span>
            <span class="metric-chip">尾程 <strong>{{ money(settings.order.tail_fee) }}</strong></span>
          </template>
          <template v-else>
            <span class="formula-chip">公式: 货值+头程+操作费+匹配利润</span>
            <span class="metric-chip">头程 <strong>{{ money(settings.activity.headcost) }}</strong></span>
            <span class="metric-chip">操作费 <strong>{{ money(settings.activity.operation_fee) }}</strong></span>
            <span class="metric-chip">浮动上限 <strong>{{ money(settings.activity.uplift_limit) }}</strong></span>
          </template>
        </div>
      </div>
      <el-button text size="small" type="primary" :icon="ArrowDown" @click="toggleCollapse">
        展开完整规则
      </el-button>
    </div>

    <!-- 展开状态或者加载/错误状态 -->
    <template v-else>
      <div class="section-heading">
        <div class="title-with-badge">
          <h2>{{ props.mode === 'order' ? '当前成本计算规则' : '当前活动价计算规则' }}</h2>
          <span class="admin-param-hint">后台统一配置，仅供核对</span>
        </div>
        <div class="heading-actions">
          <el-button v-if="settings" text size="small" type="primary" :icon="ArrowUp" @click="toggleCollapse">
            收起规则
          </el-button>
        </div>
      </div>

      <el-skeleton v-if="loading && !settings" :rows="3" animated />
      <div v-else-if="loadError" class="rules-load-error">
        <span>{{ loadError }}</span>
        <el-button text type="primary" :icon="Refresh" :loading="loading" @click="load">重新加载</el-button>
      </div>

      <template v-else-if="settings && props.mode === 'order'">
        <div class="rule-formula">
          <div class="formula-title">
            <el-icon><InfoFilled /></el-icon>
            <span>成本计算公式</span>
          </div>
          <strong>货值 + 头程 + 操作费 + 续件费 + 尾程 - 运费补贴</strong>
          <small>多件商品按数量累加货值和头程；续件费 = (总件数 - 1) × 续件单价；命中头程减半名单时，该 SKU 头程按 50% 计算。</small>
        </div>
        <div class="rule-groups">
          <div class="rule-group">
            <h3>订单费用标准</h3>
            <dl class="rule-values">
              <div><dt>操作费</dt><dd>{{ money(settings.order.operation_fee) }}</dd></div>
              <div><dt>续件费</dt><dd>{{ money(settings.order.extra_item_fee) }} / 件</dd></div>
              <div><dt>尾程</dt><dd>{{ money(settings.order.tail_fee) }}</dd></div>
              <div><dt>运费补贴</dt><dd>-{{ money(settings.order.shipping_subsidy) }}</dd></div>
            </dl>
          </div>
          <div class="rule-group">
            <h3>头程费用参数</h3>
            <dl class="rule-values rule-values--headcost">
              <div v-for="([type, value]) in orderHeadcosts" :key="type"><dt>{{ type }}</dt><dd>{{ money(value) }}</dd></div>
            </dl>
          </div>
        </div>
      </template>

      <template v-else-if="settings">
        <div class="rule-formula">
          <div class="formula-title">
            <el-icon><InfoFilled /></el-icon>
            <span>单品活动底价公式</span>
          </div>
          <strong>货值 + 头程 + 操作费 + 匹配利润</strong>
          <small>参考价低于底价时删除该行；等于底价时保留；高于底价时，在底价以上随机浮动且不超过当前浮动上限和参考价，保留两位小数。多件套直接使用对应活动价。</small>
        </div>
        <div class="rule-groups rule-groups--activity">
          <div class="rule-group">
            <h3>单品费用</h3>
            <dl class="rule-values">
              <div><dt>头程</dt><dd>{{ money(settings.activity.headcost) }}</dd></div>
              <div><dt>操作费</dt><dd>{{ money(settings.activity.operation_fee) }}</dd></div>
              <div><dt>默认浮动上限</dt><dd>{{ money(settings.activity.uplift_limit) }}</dd></div>
            </dl>
          </div>
          <div v-if="activitySetPrices.length" class="rule-group">
            <h3>多件套活动价</h3>
            <dl class="rule-values rule-values--sets">
              <div v-for="([pieces, value]) in activitySetPrices" :key="pieces"><dt>{{ pieces }}件套</dt><dd>{{ money(value) }}</dd></div>
            </dl>
          </div>
          <div class="rule-group">
            <h3>单品利润条件</h3>
            <dl class="rule-values">
              <div v-for="tier in activityTiers" :key="`${tier.min_price}-${tier.profit}`"><dt>货值 ≥ {{ money(tier.min_price) }}</dt><dd>利润 +{{ money(tier.profit) }}</dd></div>
            </dl>
          </div>
        </div>
      </template>
    </template>
  </section>
</template>

<style scoped>
.calculation-rules-band {
  transition: all 0.25s ease;
}

.calculation-rules-band.is-collapsed {
  padding-block: 12px;
}

.title-with-badge {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.admin-param-hint {
  font-size: 12px;
  color: var(--text-muted, #667085);
}

.heading-actions {
  display: flex;
  align-items: center;
}

/* 折叠条样式 */
.collapsed-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.collapsed-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  flex: 1 1 auto;
  flex-wrap: wrap;
}

.collapsed-tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  background: #eff6ff;
  color: #2563eb;
  font-size: 12px;
  font-weight: 600;
  border-radius: 4px;
}

.collapsed-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 12px;
}

.formula-chip {
  color: var(--text-muted, #667085);
  background: var(--surface-subtle, #fafbfc);
  padding: 2px 8px;
  border: 1px solid var(--border, #dfe3e8);
  border-radius: 4px;
}

.metric-chip {
  color: var(--text-regular, #344054);
}

.metric-chip strong {
  color: var(--text-strong, #18212f);
  font-variant-numeric: tabular-nums;
}

.formula-title {
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--primary, #2563eb);
  font-size: 12px;
  font-weight: 600;
}
</style>
