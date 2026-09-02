<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { errorMessage, getSettings } from '../api'
import type { AppSettings } from '../types'

const props = defineProps<{ mode: 'order' | 'activity' }>()
const settings = ref<AppSettings | null>(null)
const loading = ref(false)
const loadError = ref('')

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
    settings.value = await getSettings()
  } catch (error) {
    loadError.value = errorMessage(error)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="section-band calculation-rules">
    <div class="section-heading">
      <div>
        <h2>{{ props.mode === 'order' ? '当前成本计算规则' : '当前活动价计算规则' }}</h2>
        <p>以下参数由管理员统一设置，仅供查看</p>
      </div>
    </div>

    <el-skeleton v-if="loading && !settings" :rows="3" animated />
    <div v-else-if="loadError" class="rules-load-error">
      <span>{{ loadError }}</span>
      <el-button text type="primary" :icon="Refresh" :loading="loading" @click="load">重新加载</el-button>
    </div>

    <template v-else-if="settings && props.mode === 'order'">
      <div class="rule-formula">
        <span>成本公式</span>
        <strong>货值 + 头程 + 操作费 + 续件费 + 尾程 - 运费补贴</strong>
        <small>多件商品按数量累加货值和头程；续件费 = (总件数 - 1) × 续件单价；命中头程减半名单时，该 SKU 头程按 50% 计算。</small>
      </div>
      <div class="rule-groups">
        <div class="rule-group">
          <h3>订单费用</h3>
          <dl class="rule-values">
            <div><dt>操作费</dt><dd>{{ money(settings.order.operation_fee) }}</dd></div>
            <div><dt>续件费</dt><dd>{{ money(settings.order.extra_item_fee) }} / 件</dd></div>
            <div><dt>尾程</dt><dd>{{ money(settings.order.tail_fee) }}</dd></div>
            <div><dt>运费补贴</dt><dd>-{{ money(settings.order.shipping_subsidy) }}</dd></div>
          </dl>
        </div>
        <div class="rule-group">
          <h3>头程参数</h3>
          <dl class="rule-values rule-values--headcost">
            <div v-for="([type, value]) in orderHeadcosts" :key="type"><dt>{{ type }}</dt><dd>{{ money(value) }}</dd></div>
          </dl>
        </div>
      </div>
    </template>

    <template v-else-if="settings">
      <div class="rule-formula">
        <span>单品活动底价</span>
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
        <div class="rule-group">
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
  </section>
</template>
