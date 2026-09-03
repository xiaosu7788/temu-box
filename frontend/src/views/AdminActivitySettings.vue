<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ArrowLeft, Delete, Plus, Refresh, Select } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { errorMessage, getAdminActivitySkuRules, saveAdminActivitySkuRules } from '../api'
import { notifyError, notifySuccess } from '../feedback'
import type { ActivitySetMapping, ActivitySingleParseMode, ActivitySkuRules } from '../types'

const router = useRouter()
const loading = ref(false)
const saving = ref(false)
const loaded = ref(false)
const loadError = ref('')
const setKeywords = ref<string[]>([])
const includeEmptySetKeyword = ref(false)
const setMappings = ref<ActivitySetMapping[]>([])
const singleMode = ref<ActivitySingleParseMode>('last_segment')
const singleDelimiter = ref('-')
const singleMarker = ref('price')
const defaultSetKeywords = ['piece', '件套', '套装']
const supportedSetPieces = [4, 5, 6, 8, 10, 12]
const keywordOptions = computed(() => [...new Set([...defaultSetKeywords, ...setKeywords.value])])

const rulesValid = computed(() => {
  if (setMappings.value.some((item) => !item.pattern.trim())) return false
  if (singleMode.value === 'after_marker') return !!singleMarker.value.trim()
  return !!singleDelimiter.value.trim()
})

function applyRules(rules: ActivitySkuRules) {
  setKeywords.value = rules.set_keywords.filter(Boolean)
  includeEmptySetKeyword.value = rules.set_keywords.includes('')
  setMappings.value = rules.set_mappings.map((item) => ({ ...item }))
  singleMode.value = rules.single_mode
  singleDelimiter.value = rules.single_delimiter
  singleMarker.value = rules.single_marker
}

function buildRules(): ActivitySkuRules {
  return {
    set_keywords: [...new Set(setKeywords.value.map((item) => item.trim()).filter(Boolean).concat(includeEmptySetKeyword.value ? [''] : []))],
    set_mappings: setMappings.value.map((item) => ({ pattern: item.pattern.trim(), pieces: item.pieces })),
    single_mode: singleMode.value,
    single_delimiter: singleDelimiter.value.trim(),
    single_marker: singleMarker.value.trim(),
  }
}

async function load() {
  loading.value = true
  loaded.value = false
  loadError.value = ''
  try {
    applyRules(await getAdminActivitySkuRules())
    loaded.value = true
  } catch (error) {
    loadError.value = errorMessage(error)
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!rulesValid.value) return
  saving.value = true
  try {
    applyRules(await saveAdminActivitySkuRules(buildRules()))
    notifySuccess('默认SKC格式已保存')
  } catch (error) {
    notifyError(error)
  } finally {
    saving.value = false
  }
}

function addSetMapping() {
  setMappings.value.push({ pattern: '', pieces: 4 })
}

function removeSetMapping(index: number) {
  setMappings.value.splice(index, 1)
}

onMounted(load)
</script>

<template>
  <div class="admin-page admin-activity-settings-page">
    <section class="section-band">
      <div class="section-heading">
        <div class="subpage-title">
          <el-button text :icon="ArrowLeft" @click="router.push('/admin')">后台管理</el-button>
          <div><h2>批量报活动设置</h2><p>配置用户未启用自定义格式时使用的默认SKC识别规则</p></div>
        </div>
        <div class="admin-settings-actions">
          <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
          <el-button type="primary" :icon="Select" :loading="saving" :disabled="!loaded || !rulesValid" @click="save">保存默认格式</el-button>
        </div>
      </div>

      <el-skeleton v-if="!loaded && !loadError" :rows="8" animated />
      <div v-else-if="loadError" class="settings-load-error"><p>{{ loadError }}</p><el-button :icon="Refresh" :loading="loading" @click="load">重新加载</el-button></div>
      <div v-else class="admin-activity-rule-grid">
        <section class="admin-activity-rule-card">
          <h3>套装识别规则</h3>
          <p>先匹配固定映射，再按标识前面的数字提取套装件数。</p>
          <label class="admin-activity-rule-field">
            <span>套装标识</span>
            <el-select v-model="setKeywords" multiple filterable allow-create default-first-option placeholder="输入标识后按回车添加">
              <el-option v-for="keyword in keywordOptions" :key="keyword" :label="keyword" :value="keyword" />
            </el-select>
          </label>
          <el-checkbox v-model="includeEmptySetKeyword">套装标识为空（从货号末尾提取件数）</el-checkbox>
          <el-alert v-if="includeEmptySetKeyword" type="warning" :closable="false" show-icon title="空标识会把末尾为 4/5/6/8/10/12 的货号识别为套装" />

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

        <section class="admin-activity-rule-card">
          <h3>单品货值提取规则</h3>
          <p>套装规则未匹配时，按以下方式从SKC货号中提取单品货值。</p>
          <label class="admin-activity-rule-field">
            <span>提取方式</span>
            <el-select v-model="singleMode">
              <el-option label="第一个分隔符前的数字（5-MB131-A → 5）" value="first_segment" />
              <el-option label="最后一个分隔符后的数字（MB131-A-5 → 5）" value="last_segment" />
              <el-option label="指定文字后的数字（MB131-price17.1 → 17.1）" value="after_marker" />
            </el-select>
          </label>
          <label v-if="singleMode !== 'after_marker'" class="admin-activity-rule-field">
            <span>分隔符</span>
            <el-input v-model="singleDelimiter" maxlength="10" placeholder="例如：-" />
          </label>
          <label v-else class="admin-activity-rule-field">
            <span>指定文字</span>
            <el-input v-model="singleMarker" maxlength="32" placeholder="例如：price" />
          </label>
        </section>
      </div>
    </section>
  </div>
</template>