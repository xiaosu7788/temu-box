<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ArrowLeft, Delete, Plus, Refresh, Select } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { errorMessage, getAdminSettings, saveAdminSettings } from '../api'
import { notifyError, notifySuccess } from '../feedback'
import type { AppSettings } from '../types'

const router = useRouter()
const loading = ref(false)
const saving = ref(false)
const loaded = ref(false)
const loadError = ref('')
const settings = reactive<AppSettings>({
  order: {
    headcost: { '单品': 5, '4件套': 5, '5件套': 5, '6件套': 5, '8件套': 10, '10件套': 10, '12件套': 15 },
    operation_fee: 7,
    extra_item_fee: 2,
    tail_fee: 0,
    shipping_subsidy: 0,
  },
  activity: {
    headcost: 5,
    operation_fee: 7,
    uplift_limit: 1,
    set_prices: { '4': 42, '5': 45, '6': 48, '8': 71, '10': 75, '12': 92 },
    single_tiers: [{ min_price: 0, profit: 0 }],
  },
})
const orderTypes = ['单品', '4件套', '5件套', '6件套', '8件套', '10件套', '12件套']
const setTypes = ['4', '5', '6', '8', '10', '12']

async function load() {
  loading.value = true
  loadError.value = ''
  loaded.value = false
  try {
    Object.assign(settings, await getAdminSettings())
    loaded.value = true
  } catch (error) {
    loadError.value = errorMessage(error)
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    Object.assign(settings, await saveAdminSettings(settings))
    notifySuccess('成本参数已保存')
  } catch (error) {
    notifyError(error)
  } finally {
    saving.value = false
  }
}

function addTier() {
  settings.activity.single_tiers.push({ min_price: 0, profit: 0 })
}

function removeTier(index: number) {
  if (settings.activity.single_tiers.length > 1) settings.activity.single_tiers.splice(index, 1)
}

onMounted(load)
</script>

<template>
  <div class="admin-page admin-settings-page">
    <section class="section-band">
      <div class="section-heading">
        <div class="subpage-title"><el-button text :icon="ArrowLeft" @click="router.push('/admin')">后台管理</el-button><div><h2>成本参数</h2><p>参数保存后，后续新任务立即生效</p></div></div>
        <div class="admin-settings-actions"><el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button><el-button type="primary" :icon="Select" :loading="saving" :disabled="!loaded" @click="save">保存全部参数</el-button></div>
      </div>

      <el-skeleton v-if="!loaded && !loadError" :rows="8" animated />
      <div v-else-if="loadError" class="settings-load-error"><p>{{ loadError }}</p><el-button :icon="Refresh" :loading="loading" @click="load">重新加载</el-button></div>
      <el-tabs v-else type="border-card" class="settings-tabs">
        <el-tab-pane label="订单计算">
          <div class="settings-category-grid">
            <div class="settings-category"><h3>头程设置</h3><div class="settings-form-grid"><label v-for="type in orderTypes" :key="type">{{ type }}头程<el-input-number v-model="settings.order.headcost[type]" :min="0" :precision="2" controls-position="right" /></label></div></div>
            <div class="settings-category"><h3>基础费用</h3><div class="settings-form-grid"><label>操作费<el-input-number v-model="settings.order.operation_fee" :min="0" :precision="2" controls-position="right" /></label><label>续件费<el-input-number v-model="settings.order.extra_item_fee" :min="0" :precision="2" controls-position="right" /></label><label>尾程<el-input-number v-model="settings.order.tail_fee" :min="0" :precision="2" controls-position="right" /></label><label>运费补贴<el-input-number v-model="settings.order.shipping_subsidy" :min="0" :precision="2" controls-position="right" /></label></div><p class="settings-note">尾程计入成本，运费补贴从最终成本中扣减。</p></div>
          </div>
        </el-tab-pane>
        <el-tab-pane label="批量报名活动">
          <div class="settings-category-grid">
            <div class="settings-category"><h3>单品费用</h3><div class="settings-form-grid"><label>单品头程<el-input-number v-model="settings.activity.headcost" :min="0" :precision="2" controls-position="right" /></label><label>操作费<el-input-number v-model="settings.activity.operation_fee" :min="0" :precision="2" controls-position="right" /></label><label>默认浮动上限<el-input-number v-model="settings.activity.uplift_limit" :min="0" :max="1000" :precision="2" controls-position="right" /></label></div><p class="settings-note">用户未设置自定义浮动值时使用此默认值。</p></div>
            <div class="settings-category"><h3>多件套活动价</h3><div class="settings-form-grid"><label v-for="type in setTypes" :key="type">{{ type }}件套活动价<el-input-number v-model="settings.activity.set_prices[type]" :min="0" :precision="2" controls-position="right" /></label></div></div>
          </div>
          <div class="settings-category settings-category--wide"><h3>单品货值利润条件</h3><p class="settings-note">按货值从低到高设置条件，计算时使用不超过当前货值的最高条件。</p><div v-for="(tier, index) in settings.activity.single_tiers" :key="index" class="tier-row"><span>货值 ≥</span><el-input-number v-model="tier.min_price" :min="0" :precision="2" controls-position="right" /><span>利润 +</span><el-input-number v-model="tier.profit" :min="0" :precision="2" controls-position="right" /><el-button circle text type="danger" :icon="Delete" @click="removeTier(index)" /><span v-if="index === settings.activity.single_tiers.length - 1" class="tier-hint">按最高匹配条件计算</span></div><el-button class="add-tier" text type="primary" :icon="Plus" @click="addTier">新增条件</el-button></div>
        </el-tab-pane>
      </el-tabs>
    </section>
  </div>
</template>
