<script setup lang="ts">
import { computed, onActivated, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Delete, Download, Plus, RefreshRight, UploadFilled } from '@element-plus/icons-vue'
import type { UploadFile, UploadFiles, UploadUserFile } from 'element-plus'
import { activityDownloadUrl, deleteActivityTask, getActivityTask, getActivityTasks, getMe, getSettings, previewActivitySkuRules, processBulkActivity } from '../api'
import { confirmAction, notifyError, notifySuccess } from '../feedback'
import type { ActivitySetMapping, ActivitySingleParseMode, ActivitySkuPreview, ActivitySkuPreviewItem, ActivitySkuRules, ActivityTaskItem } from '../types'
import CostRules from '../components/CostRules.vue'
import { selectedRegionCode as regionCode } from '../regionState'

const files = ref<UploadUserFile[]>([])
const tasks = ref<ActivityTaskItem[]>([])
const loading = ref(false)
const loadingTasks = ref(false)
const deleting = ref<string | null>(null)
const currentUserId = ref<number | null>(null)
const useCustomUplift = ref(false)
const customUpliftLimit = ref(1)
const defaultUpliftLimit = ref(1)
const useCustomSkuRules = ref(false)
const skuRulesDialogVisible = ref(false)
const skuRulesConfigured = ref(false)
const dialogOpenedBySwitch = ref(false)
const setKeywords = ref<string[]>(['piece', '件套', '套装'])
const includeEmptySetKeyword = ref(false)
const setMappings = ref<ActivitySetMapping[]>([])
const singleMode = ref<ActivitySingleParseMode>('last_segment')
const singleDelimiter = ref('-')
const singleMarker = ref('price')
const skuPreview = ref<ActivitySkuPreview | null>(null)
const previewing = ref(false)
let pollTimer: ReturnType<typeof setInterval> | undefined
const activityTaskStore = new Map<number, ActivityTaskItem[]>()
const supportedSetPieces = [4, 5, 6, 8, 10, 12]
const defaultSetKeywords = ['piece', '件套', '套装']
const defaultSkuRules = ref<ActivitySkuRules>({
  set_keywords: [...defaultSetKeywords],
  set_mappings: [],
  single_mode: 'last_segment',
  single_delimiter: '-',
  single_marker: 'price',
})

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
const appliedSetKeywords = computed(() => appliedSkuRules.value.set_keywords.map((item) => item || '空标识'))
const appliedMappings = computed(() => appliedSkuRules.value.set_mappings.map((item) => `${item.pattern} → ${item.pieces}件套`))
const appliedSingleRule = computed(() => {
  if (appliedSkuRules.value.single_mode === 'first_segment') return `第一个“${appliedSkuRules.value.single_delimiter}”前的数字`
  if (appliedSkuRules.value.single_mode === 'after_marker') return `“${appliedSkuRules.value.single_marker}”后的数字`
  return `最后一个“${appliedSkuRules.value.single_delimiter}”后的数字`
})
const canPreview = computed(() => !!regionCode.value && files.value.length === 1 && !!files.value[0]?.raw && useCustomSkuRules.value && skuRulesConfigured.value)
const canSubmit = computed(() => !!regionCode.value && files.value.length === 1 && !!files.value[0]?.raw && (!useCustomSkuRules.value || (!!skuPreview.value && skuRulesConfigured.value)))
const activeTasks = computed(() => tasks.value.filter((task) => task.status === 'queued' || task.status === 'running'))

function fileChanged(_file: UploadFile, uploadFiles: UploadFiles) {
  files.value = uploadFiles.slice(-1)
  skuPreview.value = null
}

function resetSkuRules() {
  appliedSkuRules.value = createDefaultSkuRules()
  skuRulesConfigured.value = false
  setKeywords.value = [...defaultSetKeywords]
  includeEmptySetKeyword.value = false
  setMappings.value = []
  singleMode.value = 'last_segment'
  singleDelimiter.value = '-'
  singleMarker.value = 'price'
  skuPreview.value = null
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

async function previewSkuRules() {
  const file = files.value[0]?.raw
  if (!file || !skuRulesConfigured.value) return
  previewing.value = true
  try {
    skuPreview.value = await previewActivitySkuRules(file, appliedSkuRules.value, regionCode.value)
    notifySuccess('SKC识别预览已更新')
  } catch (error) {
    notifyError(error)
  } finally {
    previewing.value = false
  }
}

function previewValue(item: ActivitySkuPreviewItem) {
  if (item.value === null) return '-'
  return item.result === '套装' ? `${item.value}件` : `¥${item.value.toFixed(2)}`
}

function previewTagType(result: ActivitySkuPreviewItem['result']) {
  if (result === '无法识别') return 'danger'
  return result === '套装' ? 'warning' : 'success'
}

function mergeTask(task: ActivityTaskItem) {
  const index = tasks.value.findIndex((item) => item.id === task.id)
  if (index === -1) tasks.value.unshift(task)
  else tasks.value[index] = task
  if (currentUserId.value !== null) {
    const stored = activityTaskStore.get(currentUserId.value) || []
    const storedIndex = stored.findIndex((item) => item.id === task.id)
    if (storedIndex === -1) stored.unshift(task)
    else stored[storedIndex] = task
    activityTaskStore.set(currentUserId.value, stored)
  }
}

async function loadTasks() {
  loadingTasks.value = true
  try {
    const serverTasks = await getActivityTasks()
    const localActive = (activityTaskStore.get(currentUserId.value || -1) || [])
      .filter((task) => task.status === 'queued' || task.status === 'running')
    const merged = new Map(localActive.map((task) => [task.id, task]))
    serverTasks.forEach((task) => merged.set(task.id, task))
    tasks.value = [...merged.values()].sort((left, right) => String(right.created_at).localeCompare(String(left.created_at)))
    activityTaskStore.set(currentUserId.value || -1, tasks.value)
    startPolling()
  } catch (error) {
    notifyError(error)
  } finally {
    loadingTasks.value = false
  }
}

async function refreshActiveTasks() {
  const current = [...activeTasks.value]
  await Promise.all(current.map(async (task) => {
    try {
      mergeTask(await getActivityTask(task.id))
    } catch {
      // A temporary polling failure should not remove a task from the list.
    }
  }))
  if (!activeTasks.value.length) stopPolling()
}

function startPolling() {
  if (pollTimer || !activeTasks.value.length) return
  pollTimer = setInterval(() => { void refreshActiveTasks() }, 1500)
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
    const task = await processBulkActivity(
      file,
      regionCode.value,
      useCustomUplift.value ? customUpliftLimit.value : undefined,
      useCustomSkuRules.value ? appliedSkuRules.value : undefined,
    )
    mergeTask(task)
    files.value = []
    useCustomUplift.value = false
    customUpliftLimit.value = defaultUpliftLimit.value
    useCustomSkuRules.value = false
    resetSkuRules()
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
}

async function remove(task: ActivityTaskItem) {
  if (task.status === 'queued' || task.status === 'running') return
  try {
    if (!await confirmAction('删除后将无法恢复这条任务记录，是否继续？', '确认删除')) return
    deleting.value = task.id
    await deleteActivityTask(task.id)
    tasks.value = tasks.value.filter((item) => item.id !== task.id)
    if (currentUserId.value !== null) activityTaskStore.set(currentUserId.value, tasks.value)
    notifySuccess('活动任务记录已删除')
  } catch (error) {
    notifyError(error)
  } finally {
    deleting.value = null
  }
}

function statusText(status: string) {
  return { queued: '排队中', running: '处理中', completed: '已完成', failed: '失败' }[status] || status
}

function statusType(status: string) {
  if (status === 'completed') return 'success'
  if (status === 'failed') return 'danger'
  return 'primary'
}

function formatTime(value?: string) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

async function loadRegionDefaults(code: string) {
  if (!code) return
  try {
    const settings = await getSettings(code)
    defaultUpliftLimit.value = settings.activity.uplift_limit
    customUpliftLimit.value = settings.activity.uplift_limit
    defaultSkuRules.value = {
      ...settings.activity.default_skc_rules,
      set_keywords: [...settings.activity.default_skc_rules.set_keywords],
      set_mappings: settings.activity.default_skc_rules.set_mappings.map((item) => ({ ...item })),
    }
    resetSkuRules()
  } catch (error) {
    notifyError(error)
  }
}

async function bootstrap() {
  try {
    const user = await getMe()
    currentUserId.value = user.id
    await loadTasks()
  } catch (error) {
    notifyError(error)
  }
}

watch(regionCode, (code) => { void loadRegionDefaults(code) })
onMounted(bootstrap)
onActivated(() => { void loadTasks() })
onBeforeUnmount(stopPolling)
</script>

<template>
  <CostRules mode="activity" :region-code="regionCode" />

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

    <div class="activity-custom-settings">
      <div class="activity-setting-copy">
        <strong>本次任务自定义浮动上限</strong>
        <span>仅对本次提交生效；关闭时使用后台默认值 ¥{{ defaultUpliftLimit.toFixed(2) }}</span>
      </div>
      <el-switch v-model="useCustomUplift" class="activity-setting-switch" :width="45" aria-label="启用自定义浮动上限" />
      <el-input-number v-model="customUpliftLimit" :disabled="!useCustomUplift" :min="0" :max="1000" :precision="2" :step="0.1" controls-position="right" />
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
        <div><strong>已应用的 SKC 规则</strong><span>修改规则后需要重新预览识别结果</span></div>
        <el-button type="primary" plain @click="openSkuRulesDialog(false)">修改规则</el-button>
      </div>
      <dl class="activity-skc-summary-list">
        <div><dt>套装标识</dt><dd>{{ appliedSetKeywords.length ? appliedSetKeywords.join('、') : '未设置' }}</dd></div>
        <div><dt>固定映射</dt><dd>{{ appliedMappings.length ? appliedMappings.join('；') : '未设置' }}</dd></div>
        <div><dt>单品货值</dt><dd>{{ appliedSingleRule }}</dd></div>
      </dl>

      <div class="activity-preview-actions">
        <el-button type="primary" plain :loading="previewing" :disabled="!canPreview" @click="previewSkuRules">预览识别结果</el-button>
        <span>{{ files.length ? '确认识别结果后即可提交任务' : '请先选择报名商品信息表' }}</span>
      </div>

      <div v-if="skuPreview" class="activity-sku-preview">
        <div class="activity-preview-summary">
          <span>有效数据 <strong>{{ skuPreview.total_rows }}</strong></span>
          <span>单品 <strong>{{ skuPreview.single_rows }}</strong></span>
          <span>套装 <strong>{{ skuPreview.set_rows }}</strong></span>
          <span :class="{ danger: skuPreview.unrecognized_rows > 0 }">无法识别 <strong>{{ skuPreview.unrecognized_rows }}</strong></span>
        </div>
        <el-table :data="skuPreview.items" max-height="360" stripe>
          <el-table-column prop="row" label="行号" width="72" />
          <el-table-column prop="skc" label="SKC货号" min-width="210" show-overflow-tooltip />
          <el-table-column label="识别结果" width="110">
            <template #default="scope"><el-tag :type="previewTagType(scope.row.result)" size="small">{{ scope.row.result }}</el-tag></template>
          </el-table-column>
          <el-table-column label="货值/件数" width="120">
            <template #default="scope">{{ previewValue(scope.row) }}</template>
          </el-table-column>
          <el-table-column label="基础活动价" width="120">
            <template #default="scope">{{ scope.row.base_price === null ? '-' : `¥${scope.row.base_price.toFixed(2)}` }}</template>
          </el-table-column>
          <el-table-column prop="method" label="识别依据" min-width="220" show-overflow-tooltip />
        </el-table>
        <p v-if="skuPreview.total_rows > skuPreview.preview_limit" class="activity-preview-note">表格仅展示前 {{ skuPreview.preview_limit }} 行，统计数量包含全部数据。</p>
      </div>
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
        <section class="activity-rule-section">
          <div class="activity-rule-title">
            <div><strong>套装识别规则</strong><span>按固定映射、套装标识的顺序识别</span></div>
          </div>
          <label class="activity-rule-field">
            <span>套装标识</span>
            <el-select v-model="setKeywords" multiple filterable allow-create default-first-option placeholder="输入标识后按回车添加">
              <el-option v-for="keyword in defaultSetKeywords" :key="keyword" :label="keyword" :value="keyword" />
            </el-select>
          </label>
          <el-checkbox v-model="includeEmptySetKeyword">套装标识为空（从货号末尾提取件数）</el-checkbox>
          <el-alert v-if="includeEmptySetKeyword" type="warning" :closable="false" show-icon title="空标识会优先把末尾为 4/5/6/8/10/12 的货号识别为套装" />

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
    <div class="action-row left">
      <el-button type="primary" :loading="loading" :disabled="!canSubmit" @click="submit">提交处理任务</el-button>
      <el-button :icon="RefreshRight" :loading="loadingTasks" @click="loadTasks">刷新任务</el-button>
    </div>
  </section>

  <section class="section-band activity-tasks-panel">
    <div class="section-heading">
      <div><h2>活动处理任务</h2><p>共 {{ tasks.length }} 个任务{{ activeTasks.length ? `，${activeTasks.length} 个处理中` : '' }}</p></div>
    </div>
    <el-empty v-if="!loadingTasks && !tasks.length" description="暂无活动处理任务" />
    <div v-else class="activity-task-list">
      <article v-for="task in tasks" :key="task.id" class="activity-task-item">
        <div class="activity-task-main">
          <div class="activity-task-title">
            <strong>{{ task.filename }}</strong>
            <el-tag :type="statusType(task.status)" size="small">{{ statusText(task.status) }}</el-tag>
            <el-tag size="small" effect="plain">{{ task.region_name }}</el-tag>
          </div>
          <p>{{ task.message }} · {{ formatTime(task.created_at) }}</p>
          <el-progress :percentage="task.progress" :status="task.status === 'failed' ? 'exception' : task.status === 'completed' ? 'success' : undefined" />
          <p v-if="task.status === 'completed'" class="activity-task-stats">处理 {{ task.stats.processed_rows }} 行 · 替换 {{ task.stats.updated_rows }} 行 · 保留 {{ task.stats.unchanged_rows }} 行 · 删除 {{ task.stats.removed_rows }} 行</p>
          <p v-if="task.status === 'failed' && task.logs.length" class="activity-error">{{ task.logs[task.logs.length - 1] }}</p>
        </div>
        <div class="activity-task-action">
          <el-button v-if="task.download_ready" type="success" :icon="Download" tag="a" :href="activityDownloadUrl(task.id)">下载结果</el-button>
          <el-button v-if="task.status !== 'queued' && task.status !== 'running'" link type="danger" :icon="Delete" :loading="deleting === task.id" @click="remove(task)">删除</el-button>
          <span v-if="!task.download_ready && (task.status === 'queued' || task.status === 'running')" class="activity-task-id mono">{{ task.id }}</span>
        </div>
      </article>
    </div>
  </section>
</template>
