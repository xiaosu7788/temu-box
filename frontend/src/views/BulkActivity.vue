<script setup lang="ts">
import { computed, onActivated, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Delete, Download, Plus, RefreshRight, UploadFilled } from '@element-plus/icons-vue'
import type { UploadFile, UploadFiles, UploadUserFile } from 'element-plus'
import { activityDownloadUrl, getActivityTask, getActivityTasks, getSettings, previewActivitySkuRules, processBulkActivity } from '../api'
import { notifyError, notifySuccess } from '../feedback'
import type { ActivityIdProfitRule, ActivityIdType, ActivitySetMapping, ActivitySingleParseMode, ActivitySkuPreview, ActivitySkuPreviewItem, ActivitySkuRules, ActivityTaskItem } from '../types'
import CostRules from '../components/CostRules.vue'
import { selectedCategoryCode as categoryCode, selectedRegionCode as regionCode } from '../regionState'

const files = ref<UploadUserFile[]>([])
const task = ref<ActivityTaskItem | null>(null)
const loading = ref(false)
const loadingTask = ref(false)
const useCustomUplift = ref(false)
const customUpliftLimit = ref(1)
const defaultUpliftLimit = ref(1)
const useCustomSkuRules = ref(false)
const useCustomIdProfitRules = ref(false)
const idProfitRules = ref<ActivityIdProfitRule[]>([])
const defaultIdProfitRules = ref<ActivityIdProfitRule[]>([])
const idProfitDialogVisible = ref(false)
const idDialogOpenedBySwitch = ref(false)
const idRuleDraftType = ref<ActivityIdType>('SPU')
const idRuleDraftIds = ref('')
const idRuleDraftProfit = ref(0)
const skuRulesDialogVisible = ref(false)
const skuRulesConfigured = ref(false)
const dialogOpenedBySwitch = ref(false)
const setKeywords = ref<string[]>([])
const includeEmptySetKeyword = ref(false)
const setMappings = ref<ActivitySetMapping[]>([])
const singleMode = ref<ActivitySingleParseMode>('last_segment')
const singleDelimiter = ref('-')
const singleMarker = ref('price')
const skuPreview = ref<ActivitySkuPreview | null>(null)
const previewDialogVisible = ref(false)
const previewPage = ref(1)
const previewPageSize = ref(100)
const previewFilter = ref<ActivitySkuPreviewItem['result'] | ''>('')
const previewing = ref(false)
const previewLoading = ref(false)
let pollTimer: ReturnType<typeof setInterval> | undefined
const idRuleTypes: ActivityIdType[] = ['SPU', 'SKC', 'SKU']
const defaultSkuRules = ref<ActivitySkuRules>({
  set_keywords: [],
  set_mappings: [],
  single_mode: 'last_segment',
  single_delimiter: '-',
  single_marker: 'price',
})
const keywordOptions = computed(() => defaultSkuRules.value.set_keywords.filter(Boolean))
const supportedSetPieces = computed(() => (defaultSkuRules.value.allowed_pieces || []).slice().sort((left, right) => left - right))
const hasSetPieces = computed(() => supportedSetPieces.value.length > 0)

function createDefaultSkuRules(): ActivitySkuRules {
  return {
    ...defaultSkuRules.value,
    set_keywords: [...defaultSkuRules.value.set_keywords],
    set_mappings: defaultSkuRules.value.set_mappings.map((item) => ({ ...item })),
  }
}

const appliedSkuRules = ref<ActivitySkuRules>(createDefaultSkuRules())
const draftSkuRules = computed<ActivitySkuRules>(() => ({
  set_keywords: [...new Set(setKeywords.value.map((item) => item.trim()).filter(Boolean).concat(includeEmptySetKeyword.value ? [''] : []))],
  set_mappings: setMappings.value.map((item) => ({ pattern: item.pattern.trim(), pieces: item.pieces })),
  single_mode: singleMode.value,
  single_delimiter: singleDelimiter.value.trim(),
  single_marker: singleMarker.value.trim(),
}))
const skuRulesValid = computed(() => {
  if (setMappings.value.some((item) => !item.pattern.trim())) return false
  if (singleMode.value === 'after_marker') return !!singleMarker.value.trim()
  return !!singleDelimiter.value.trim()
})
const idProfitRulesValid = computed(() => !useCustomIdProfitRules.value || (idProfitRules.value.length > 0 && idProfitRules.value.every((rule) => rule.id.trim() && Number.isFinite(rule.profit))))
const idProfitRulesSummary = computed(() => {
  if (!idProfitRules.value.length) return '尚未设置'
  const preview = idProfitRules.value.slice(0, 2).map((rule) => `${rule.id_type} ${rule.id}`).join('、')
  return idProfitRules.value.length > 2 ? `${preview} 等 ${idProfitRules.value.length} 条` : preview
})
const appliedSetKeywords = computed(() => appliedSkuRules.value.set_keywords.map((item) => item || '空标识'))
const appliedMappings = computed(() => appliedSkuRules.value.set_mappings.map((item) => `${item.pattern} → ${item.pieces}件套`))
const appliedSingleRule = computed(() => {
  if (appliedSkuRules.value.single_mode === 'first_segment') return `第一个“${appliedSkuRules.value.single_delimiter}”前的数字`
  if (appliedSkuRules.value.single_mode === 'after_marker') return `“${appliedSkuRules.value.single_marker}”后的数字`
  return `最后一个“${appliedSkuRules.value.single_delimiter}”后的数字`
})
const canPreview = computed(() => !!regionCode.value && !!categoryCode.value && files.value.length === 1 && !!files.value[0]?.raw && ((useCustomSkuRules.value && skuRulesConfigured.value) || idProfitRulesValid.value))
const previewItems = computed(() => skuPreview.value?.items || [])
const previewTotal = computed(() => skuPreview.value?.total_items || 0)
const previewFilterOptions: ActivitySkuPreviewItem['result'][] = ['单品', '套装', '无法识别']
const canSubmit = computed(() => !!regionCode.value && !!categoryCode.value && files.value.length === 1 && !!files.value[0]?.raw && (!useCustomSkuRules.value || skuRulesConfigured.value) && (!useCustomIdProfitRules.value || idProfitRulesValid.value))
const isActiveTask = computed(() => !!task.value && (task.value.status === 'queued' || task.value.status === 'running'))
const hasTaskStats = computed(() => task.value?.stats?.processed_rows !== undefined)
const logLines = computed(() => {
  if (!task.value) return []
  return task.value.logs.length ? task.value.logs : [task.value.message]
})

function fileChanged(_file: UploadFile, uploadFiles: UploadFiles) {
  files.value = uploadFiles.slice(-1)
  skuPreview.value = null
  previewDialogVisible.value = false
}

function resetSkuRules() {
  const rules = createDefaultSkuRules()
  appliedSkuRules.value = rules
  skuRulesConfigured.value = false
  setKeywords.value = rules.set_keywords.filter(Boolean)
  includeEmptySetKeyword.value = rules.set_keywords.includes('')
  setMappings.value = rules.set_mappings.map((item) => ({ ...item }))
  singleMode.value = rules.single_mode
  singleDelimiter.value = rules.single_delimiter
  singleMarker.value = rules.single_marker
  skuPreview.value = null
}

function resetIdProfitRules() {
  idProfitRules.value = []
  useCustomIdProfitRules.value = false
  idProfitDialogVisible.value = false
  idRuleDraftIds.value = ''
  idRuleDraftProfit.value = 0
}

function removeIdProfitRule(index: number) {
  idProfitRules.value.splice(index, 1)
}

function openIdProfitDialog(fromSwitch = false) {
  idDialogOpenedBySwitch.value = fromSwitch
  idRuleDraftType.value = 'SPU'
  idRuleDraftIds.value = ''
  idRuleDraftProfit.value = 0
  idProfitDialogVisible.value = true
}

function handleIdProfitToggle(enabled: boolean) {
  if (enabled) openIdProfitDialog(true)
  else {
    idProfitDialogVisible.value = false
    skuPreview.value = null
  }
}

function confirmIdProfitRules() {
  const ids = [...new Set(idRuleDraftIds.value.split(/[\s,，;；]+/).map((item) => item.trim()).filter(Boolean))]
  if (!ids.length) {
    notifyError('请至少输入一个商品 ID')
    return
  }
  if (!Number.isFinite(idRuleDraftProfit.value)) {
    notifyError('请输入有效的利润调整值')
    return
  }
  const normalized = new Map(idProfitRules.value.map((rule) => [`${rule.id_type}:${rule.id.trim().toLowerCase()}`, rule]))
  ids.forEach((id) => {
    const rule = { id_type: idRuleDraftType.value, id, profit: Number(idRuleDraftProfit.value.toFixed(2)) }
    normalized.set(`${rule.id_type}:${id.toLowerCase()}`, rule)
  })
  idProfitRules.value = [...normalized.values()]
  useCustomIdProfitRules.value = true
  idDialogOpenedBySwitch.value = false
  idProfitDialogVisible.value = false
  skuPreview.value = null
  notifySuccess(`已添加 ${ids.length} 条 ${idRuleDraftType.value} 利润条件`)
}

function closeIdProfitDialog() {
  if (idDialogOpenedBySwitch.value && !idProfitRules.value.length) useCustomIdProfitRules.value = false
  idDialogOpenedBySwitch.value = false
}

function loadSkuRuleDraft() {
  const rules = skuRulesConfigured.value ? appliedSkuRules.value : createDefaultSkuRules()
  setKeywords.value = rules.set_keywords.filter(Boolean)
  includeEmptySetKeyword.value = rules.set_keywords.includes('')
  setMappings.value = rules.set_mappings.map((item) => ({ ...item }))
  singleMode.value = rules.single_mode
  singleDelimiter.value = rules.single_delimiter
  singleMarker.value = rules.single_marker
}

function openSkuRulesDialog(fromSwitch = false) {
  dialogOpenedBySwitch.value = fromSwitch
  loadSkuRuleDraft()
  skuRulesDialogVisible.value = true
}

function handleSkuRulesToggle(enabled: boolean) {
  if (enabled) openSkuRulesDialog(true)
  else {
    skuRulesDialogVisible.value = false
    skuPreview.value = null
  }
}

function confirmSkuRules() {
  if (!skuRulesValid.value) return
  appliedSkuRules.value = {
    ...draftSkuRules.value,
    set_keywords: [...draftSkuRules.value.set_keywords],
    set_mappings: draftSkuRules.value.set_mappings.map((item) => ({ ...item })),
  }
  skuRulesConfigured.value = true
  useCustomSkuRules.value = true
  skuPreview.value = null
  dialogOpenedBySwitch.value = false
  skuRulesDialogVisible.value = false
  notifySuccess('自定义 SKC 规则已应用')
}

function closeSkuRulesDialog() {
  if (dialogOpenedBySwitch.value) useCustomSkuRules.value = false
  dialogOpenedBySwitch.value = false
}

function addSetMapping() {
  setMappings.value.push({ pattern: '', pieces: 4 })
}

function removeSetMapping(index: number) {
  setMappings.value.splice(index, 1)
}

let previewRequestId = 0

async function fetchPreview(page = previewPage.value) {
  const file = files.value[0]?.raw
  if (!file || !canPreview.value) return
  const requestId = ++previewRequestId
  previewLoading.value = true
  try {
    const payload = await previewActivitySkuRules(
      file,
      useCustomSkuRules.value ? appliedSkuRules.value : undefined,
      regionCode.value,
      useCustomIdProfitRules.value ? idProfitRules.value : undefined,
      categoryCode.value,
      { page, pageSize: previewPageSize.value, resultFilter: previewFilter.value || null },
    )
    if (requestId !== previewRequestId) return  // 已有更新的请求，丢弃过期响应
    skuPreview.value = payload
    previewPage.value = payload.page
  } catch (error) {
    if (requestId === previewRequestId) notifyError(error)
  } finally {
    if (requestId === previewRequestId) previewLoading.value = false
  }
}

async function previewSkuRules() {
  const file = files.value[0]?.raw
  if (!file || !canPreview.value) return
  previewing.value = true
  try {
    previewPage.value = 1
    await fetchPreview(1)
    previewDialogVisible.value = true
    if (skuPreview.value) notifySuccess('SKC识别预览已更新')
  } finally {
    previewing.value = false
  }
}

function previewPageChanged(page: number) {
  previewPage.value = page
  void fetchPreview(page)
}

function previewPageSizeChanged(size: number) {
  previewPageSize.value = size
  previewPage.value = 1
  void fetchPreview(1)
}

function previewFilterChanged() {
  previewPage.value = 1
  void fetchPreview(1)
}

function previewPriceRange(item: ActivitySkuPreviewItem) {
  if (item.final_price_low === null || item.final_price_high === null) return '-'
  if (item.action === '不变') return `¥${item.final_price_low.toFixed(2)}`
  return `¥${item.final_price_low.toFixed(2)} ~ ¥${item.final_price_high.toFixed(2)}`
}

function previewActionType(action: ActivitySkuPreviewItem['action']) {
  if (action === '将被删除' || action === '无法识别') return 'danger'
  if (action === '不变') return 'info'
  return 'success'
}

function previewValue(item: ActivitySkuPreviewItem) {
  if (item.value === null) return '-'
  return item.result === '套装' ? `${item.value}件` : `¥${item.value.toFixed(2)}`
}

function previewTagType(result: ActivitySkuPreviewItem['result']) {
  if (result === '无法识别') return 'danger'
  return result === '套装' ? 'warning' : 'success'
}

function mergeTask(incoming: ActivityTaskItem) {
  task.value = incoming
}

async function loadActiveTask() {
  loadingTask.value = true
  try {
    const serverTasks = await getActivityTasks()
    const active = serverTasks.find((item) => item.status === 'queued' || item.status === 'running')
    if (active) mergeTask(active)
    else if (task.value) mergeTask(await getActivityTask(task.value.id))
    if (isActiveTask.value) startPolling()
    else stopPolling()
  } catch (error) {
    notifyError(error)
  } finally {
    loadingTask.value = false
  }
}

async function refreshActiveTask() {
  if (!task.value || !isActiveTask.value) {
    stopPolling()
    return
  }
  try {
    mergeTask(await getActivityTask(task.value.id))
  } catch {
    // 轮询临时失败时保留当前任务展示。
  }
  if (!isActiveTask.value) stopPolling()
}

function startPolling() {
  if (pollTimer || !isActiveTask.value) return
  pollTimer = setInterval(() => { void refreshActiveTask() }, 1200)
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = undefined
}

async function submit() {
  const file = files.value[0]?.raw
  if (!file) return
  loading.value = true
  try {
    mergeTask(await processBulkActivity(
      file,
      regionCode.value,
      useCustomUplift.value ? customUpliftLimit.value : undefined,
      useCustomSkuRules.value ? appliedSkuRules.value : undefined,
      useCustomIdProfitRules.value ? idProfitRules.value : undefined,
      categoryCode.value,
    ))
    files.value = []
    useCustomUplift.value = false
    customUpliftLimit.value = defaultUpliftLimit.value
    useCustomSkuRules.value = false
    resetSkuRules()
    resetIdProfitRules()
    startPolling()
    notifySuccess('任务已提交，后台正在处理')
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
  }
}

function reset() {
  files.value = []
  useCustomUplift.value = false
  customUpliftLimit.value = defaultUpliftLimit.value
  useCustomSkuRules.value = false
  resetSkuRules()
  resetIdProfitRules()
}

async function loadRegionDefaults(code: string, category?: string) {
  if (!code) return
  try {
    const settings = await getSettings(code, category)
    defaultUpliftLimit.value = settings.activity.uplift_limit
    customUpliftLimit.value = settings.activity.uplift_limit
    defaultSkuRules.value = {
      ...settings.activity.default_skc_rules,
      set_keywords: [...settings.activity.default_skc_rules.set_keywords],
      set_mappings: settings.activity.default_skc_rules.set_mappings.map((item) => ({ ...item })),
    }
    defaultIdProfitRules.value = settings.activity.id_profit_rules.map((rule) => ({ ...rule }))
    resetSkuRules()
  } catch (error) {
    notifyError(error)
  }
}

async function bootstrap() {
  try {
    await Promise.all([loadActiveTask(), loadRegionDefaults(regionCode.value, categoryCode.value)])
  } catch (error) {
    notifyError(error)
  }
}

watch([regionCode, categoryCode], ([code, category]) => { void loadRegionDefaults(code, category) })
onMounted(bootstrap)
onActivated(() => { void loadActiveTask() })
onBeforeUnmount(stopPolling)
</script>

<template>
  <CostRules mode="activity" :region-code="regionCode" :category-code="categoryCode" />

  <section class="section-band activity-upload-panel">
    <div class="section-heading">
      <div>
        <h2>上传报名商品表</h2>
        <p>提交后由后台处理，切换模块或刷新页面也可以继续查看任务状态</p>
      </div>
      <el-button :icon="RefreshRight" :disabled="loading" @click="reset">重置</el-button>
    </div>

    <el-upload
      v-model:file-list="files"
      drag
      :auto-upload="false"
      :limit="1"
      accept=".xlsx,.xlsm"
      @change="fileChanged"
    >
      <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
      <div class="el-upload__text">选择报名商品信息表</div>
    </el-upload>

    <div class="activity-settings-row">
      <div class="activity-custom-settings">
        <div class="activity-setting-copy">
          <strong>本次任务自定义浮动上限</strong>
          <span>仅对本次提交生效；关闭时使用后台默认值 ¥{{ defaultUpliftLimit.toFixed(2) }}</span>
        </div>
        <el-switch v-model="useCustomUplift" class="activity-setting-switch" :width="45" aria-label="启用自定义浮动上限" />
        <el-input-number v-model="customUpliftLimit" :disabled="!useCustomUplift" :min="0" :max="1000" :precision="2" :step="0.1" controls-position="right" />
      </div>
      <div class="activity-id-profit-entry">
        <div class="activity-setting-copy">
          <strong>本次任务 ID 利润条件</strong>
          <span>{{ useCustomIdProfitRules ? idProfitRulesSummary : `关闭时使用后台默认值（${defaultIdProfitRules.length} 条）` }}</span>
        </div>
        <el-switch v-model="useCustomIdProfitRules" class="activity-setting-switch" :width="45" aria-label="启用本次任务 ID 利润条件" @change="handleIdProfitToggle" />
        <el-button type="primary" plain :disabled="!useCustomIdProfitRules" @click="openIdProfitDialog(false)">设置条件</el-button>
      </div>
    </div>

    <div class="activity-custom-settings activity-skc-settings-heading">
      <div class="activity-setting-copy">
        <strong>本次任务自定义 SKC 格式</strong>
        <span>关闭时使用系统格式；自定义规则仅对本次提交生效</span>
      </div>
      <el-switch
        v-model="useCustomSkuRules"
        class="activity-setting-switch"
        :width="45"
        aria-label="启用自定义 SKC 格式"
        @change="handleSkuRulesToggle"
      />
    </div>

    <div v-if="useCustomSkuRules && skuRulesConfigured" class="activity-skc-summary">
      <div class="activity-skc-summary-heading">
        <div><strong>已应用的 SKC 规则</strong><span>规则已生效，可直接提交或先预览识别结果</span></div>
        <el-button type="primary" plain @click="openSkuRulesDialog(false)">修改规则</el-button>
      </div>
      <dl class="activity-skc-summary-list">
        <div v-if="hasSetPieces"><dt>套装标识</dt><dd>{{ appliedSetKeywords.length ? appliedSetKeywords.join('、') : '未设置' }}</dd></div>
        <div v-if="hasSetPieces"><dt>固定映射</dt><dd>{{ appliedMappings.length ? appliedMappings.join('；') : '未设置' }}</dd></div>
        <div><dt>单品货值</dt><dd>{{ appliedSingleRule }}</dd></div>
        <div v-if="!hasSetPieces"><dt>套装规则</dt><dd>当前品类无套装档位，仅按单品规则识别</dd></div>
      </dl>
    </div>

    <el-dialog
      v-model="idProfitDialogVisible"
      title="设置本次任务 ID 利润条件"
      width="min(720px, calc(100vw - 32px))"
      class="activity-id-profit-dialog"
      :close-on-click-modal="false"
      destroy-on-close
      @closed="closeIdProfitDialog"
    >
      <div class="activity-id-profit-form">
        <div class="activity-id-profit-form-heading">
          <strong>批量添加条件</strong>
          <span>匹配优先级：SPU &gt; SKC &gt; SKU；同一行只使用一条最高优先级规则</span>
        </div>
        <div class="activity-id-profit-form-grid">
          <label><span>ID 类型</span><el-select v-model="idRuleDraftType"><el-option v-for="type in idRuleTypes" :key="type" :label="`${type} ID`" :value="type" /></el-select></label>
          <label><span>利润调整</span><el-input-number v-model="idRuleDraftProfit" :min="-100000" :max="100000" :precision="2" controls-position="right" /></label>
        </div>
        <label class="activity-id-profit-textarea"><span>商品 ID（支持换行、空格、逗号或分号分隔）</span><el-input v-model="idRuleDraftIds" type="textarea" :rows="4" placeholder="例如：\n1169783790\n4248497589\n5878227564" /></label>
        <el-button type="primary" :icon="Plus" @click="confirmIdProfitRules">添加到条件列表</el-button>
      </div>
      <div class="activity-id-profit-current">
        <div class="activity-id-profit-heading-row"><div><strong>已设置条件（{{ idProfitRules.length }}）</strong><span>同一类型和 ID 重复添加时将更新原条件</span></div></div>
        <div v-if="idProfitRules.length" class="activity-id-profit-list">
          <div v-for="(rule, index) in idProfitRules" :key="`${rule.id_type}-${rule.id}-${index}`" class="activity-id-profit-row">
            <el-tag size="small" effect="plain">{{ rule.id_type }} ID</el-tag>
            <span class="activity-id-profit-value">{{ rule.id }}</span>
            <span class="activity-id-profit-label">利润 {{ rule.profit >= 0 ? '+' : '' }}{{ rule.profit.toFixed(2) }}</span>
            <el-button link type="danger" :icon="Delete" aria-label="删除 ID 利润条件" @click="removeIdProfitRule(index)" />
          </div>
        </div>
        <el-empty v-else :image-size="42" description="暂无本次任务 ID 利润条件" />
      </div>
      <template #footer><el-button @click="idProfitDialogVisible = false">关闭</el-button></template>
    </el-dialog>

    <div class="activity-preview-actions">
      <el-button type="primary" plain :loading="previewing" :disabled="!canPreview" @click="previewSkuRules">预览识别结果</el-button>
      <span>{{ files.length ? '预览为可选操作，可直接提交任务' : '请先选择报名商品信息表' }}</span>
    </div>

    <el-dialog
      v-model="skuRulesDialogVisible"
      title="设置自定义 SKC 格式"
      width="min(880px, calc(100vw - 32px))"
      class="activity-skc-dialog"
      :close-on-click-modal="false"
      destroy-on-close
      @closed="closeSkuRulesDialog"
    >
      <div class="activity-rule-grid">
        <section v-if="hasSetPieces" class="activity-rule-section">
          <div class="activity-rule-title">
            <div><strong>套装识别规则</strong><span>按固定映射、套装标识的顺序识别</span></div>
          </div>
          <label class="activity-rule-field">
            <span>套装标识</span>
            <el-select v-model="setKeywords" multiple filterable allow-create default-first-option placeholder="输入标识后按回车添加">
              <el-option v-for="keyword in keywordOptions" :key="keyword" :label="keyword" :value="keyword" />
            </el-select>
          </label>
          <el-checkbox v-model="includeEmptySetKeyword">套装标识为空（从货号末尾提取件数）</el-checkbox>
          <el-alert v-if="includeEmptySetKeyword" type="warning" :closable="false" show-icon :title="`空标识会优先把末尾为 ${supportedSetPieces.join('/')} 的货号识别为套装`" />

          <div class="activity-mapping-heading">
            <span>固定映射</span>
            <el-button link type="primary" :icon="Plus" @click="addSetMapping">增加映射</el-button>
          </div>
          <div v-if="setMappings.length" class="activity-mapping-list">
            <div v-for="(mapping, index) in setMappings" :key="index" class="activity-mapping-row">
              <el-input v-model="mapping.pattern" placeholder="例如：四件组合" maxlength="64" />
              <el-select v-model="mapping.pieces" aria-label="套装件数">
                <el-option v-for="pieces in supportedSetPieces" :key="pieces" :label="`${pieces}件套`" :value="pieces" />
              </el-select>
              <el-button link type="danger" :icon="Delete" aria-label="删除固定映射" @click="removeSetMapping(index)" />
            </div>
          </div>
          <el-empty v-else :image-size="42" description="暂无固定映射" />
        </section>
        <section v-else class="activity-rule-section">
          <div class="activity-rule-title">
            <div><strong>套装识别规则</strong><span>当前品类无套装档位</span></div>
          </div>
          <el-alert type="info" :closable="false" show-icon title="该品类为无套装型，所有货号按单品规则提取货值" />
        </section>

        <section class="activity-rule-section">
          <div class="activity-rule-title">
            <div><strong>单品货值提取规则</strong><span>套装未匹配时再按此规则提取货值</span></div>
          </div>
          <label class="activity-rule-field">
            <span>提取方式</span>
            <el-select v-model="singleMode">
              <el-option label="第一个分隔符前的数字（5-MB131-A → 5）" value="first_segment" />
              <el-option label="最后一个分隔符后的数字（MB131-A-5 → 5）" value="last_segment" />
              <el-option label="指定文字后的数字（MB131-price17.1 → 17.1）" value="after_marker" />
            </el-select>
          </label>
          <label v-if="singleMode !== 'after_marker'" class="activity-rule-field">
            <span>分隔符</span>
            <el-input v-model="singleDelimiter" maxlength="10" placeholder="例如：-" />
          </label>
          <label v-else class="activity-rule-field">
            <span>指定文字</span>
            <el-input v-model="singleMarker" maxlength="32" placeholder="例如：price" />
          </label>
        </section>
      </div>
      <template #footer>
        <el-button @click="skuRulesDialogVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!skuRulesValid" @click="confirmSkuRules">确认使用</el-button>
      </template>
    </el-dialog>
    <el-dialog
      v-model="previewDialogVisible"
      title="SKC 识别预览"
      width="min(1100px, calc(100vw - 32px))"
      class="activity-preview-dialog"
      append-to-body
      destroy-on-close
    >
      <div v-if="skuPreview" v-loading="previewLoading" class="activity-sku-preview">
        <div class="activity-preview-summary">
          <span>有效数据 <strong>{{ skuPreview.total_rows }}</strong></span>
          <span>单品 <strong>{{ skuPreview.single_rows }}</strong></span>
          <span>套装 <strong>{{ skuPreview.set_rows }}</strong></span>
          <span :class="{ danger: skuPreview.unrecognized_rows > 0 }">无法识别 <strong>{{ skuPreview.unrecognized_rows }}</strong></span>
        </div>
        <div class="activity-preview-toolbar">
          <div class="activity-preview-toolbar-info">
            浮动上限 <strong>¥{{ skuPreview.uplift_limit.toFixed(2) }}</strong>
            <span>实际写入价 = 基础活动价（含 ID 利润调整）加上不超过该上限的随机浮动，且不高于申报价；申报价低于基础价的行会被删除。</span>
          </div>
          <el-select
            v-model="previewFilter"
            class="activity-preview-filter"
            placeholder="全部明细"
            clearable
            aria-label="明细筛选"
            @change="previewFilterChanged"
            @clear="previewFilterChanged"
          >
            <el-option v-for="option in previewFilterOptions" :key="option" :label="`只看${option}`" :value="option" />
          </el-select>
        </div>
        <div class="activity-preview-table-wrap">
          <el-table :data="previewItems" height="100%" stripe>
            <el-table-column prop="row" label="行号" width="72" />
            <el-table-column prop="skc" label="SKC货号" min-width="210" show-overflow-tooltip />
            <el-table-column label="识别结果" width="100">
              <template #default="scope"><el-tag :type="previewTagType(scope.row.result)" size="small">{{ scope.row.result }}</el-tag></template>
            </el-table-column>
            <el-table-column label="货值/件数" width="110">
              <template #default="scope">{{ previewValue(scope.row) }}</template>
            </el-table-column>
            <el-table-column label="申报价" width="100">
              <template #default="scope">{{ scope.row.reference_price === null ? '-' : `¥${scope.row.reference_price.toFixed(2)}` }}</template>
            </el-table-column>
            <el-table-column label="基础活动价" width="120">
              <template #default="scope">{{ scope.row.base_price === null ? '-' : `¥${scope.row.base_price.toFixed(2)}` }}</template>
            </el-table-column>
            <el-table-column label="ID利润调整" width="130">
              <template #default="scope">
                <span v-if="scope.row.matched_id_type">{{ scope.row.matched_id_type }} {{ scope.row.profit_adjustment >= 0 ? '+' : '' }}{{ scope.row.profit_adjustment.toFixed(2) }}</span>
                <span v-else>-</span>
              </template>
            </el-table-column>
            <el-table-column label="处理动作" width="110">
              <template #default="scope"><el-tag :type="previewActionType(scope.row.action)" size="small" effect="plain">{{ scope.row.action }}</el-tag></template>
            </el-table-column>
            <el-table-column label="调整后活动价" width="180">
              <template #default="scope">{{ previewPriceRange(scope.row) }}</template>
            </el-table-column>
            <el-table-column prop="method" label="识别依据" min-width="200" show-overflow-tooltip />
          </el-table>
        </div>
        <p class="activity-preview-note">
          统计数量基于全表；明细共 {{ skuPreview.total_items }} 条，当前第 {{ skuPreview.page }} / {{ skuPreview.total_pages }} 页。
        </p>
      </div>
      <el-empty v-else description="暂无预览数据" />
      <template #footer>
        <div class="activity-preview-footer">
          <el-pagination
            v-model:current-page="previewPage"
            v-model:page-size="previewPageSize"
            :total="previewTotal"
            :page-sizes="[50, 100, 200, 500]"
            layout="total, sizes, prev, pager, next, jumper"
            :disabled="previewLoading"
            background
            @current-change="previewPageChanged"
            @size-change="previewPageSizeChanged"
          />
          <el-button @click="previewDialogVisible = false">关闭</el-button>
        </div>
      </template>
    </el-dialog>
    <div class="action-row left">
      <el-button type="primary" :loading="loading" :disabled="!canSubmit" @click="submit">提交处理任务</el-button>
      <el-button :icon="RefreshRight" :loading="loadingTask" @click="loadActiveTask">刷新任务状态</el-button>
    </div>
  </section>

  <section v-if="task" class="section-band task-panel">
    <div class="section-heading">
      <div>
        <h2>任务进度</h2>
        <p>
          <el-tag size="small" effect="plain">{{ task.region_name }}</el-tag>
          <el-tag v-if="task.category_name" size="small" effect="plain" type="info">{{ task.category_name }}</el-tag>
          <span class="mono">{{ task.filename }}</span>
          <span class="mono">{{ task.id }}</span>
        </p>
      </div>
      <el-tag :type="task.status === 'completed' ? 'success' : task.status === 'failed' ? 'danger' : 'primary'">
        {{ task.message }}
      </el-tag>
    </div>
    <el-progress :percentage="task.progress" :status="task.status === 'failed' ? 'exception' : task.status === 'completed' ? 'success' : undefined" />

    <div v-if="hasTaskStats" class="metric-strip activity-metrics">
      <div><span>处理行数</span><strong>{{ task.stats.processed_rows }}</strong></div>
      <div><span>替换行数</span><strong>{{ task.stats.updated_rows }}</strong></div>
      <div><span>保留行数</span><strong>{{ task.stats.unchanged_rows }}</strong></div>
      <div><span>删除行数</span><strong>{{ task.stats.removed_rows }}</strong></div>
      <div><span>命中 ID 条件</span><strong>{{ task.stats.id_profit_rule_matches || 0 }}</strong></div>
    </div>

    <div class="log-view" aria-live="polite">
      <div v-for="(line, index) in logLines" :key="index">{{ line }}</div>
    </div>

    <div v-if="task.download_ready" class="action-row left">
      <el-button type="success" :icon="Download" tag="a" :href="activityDownloadUrl(task.id)">下载结果</el-button>
    </div>
  </section>
</template>
