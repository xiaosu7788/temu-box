<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Delete, Plus, Refresh, Select } from '@element-plus/icons-vue'
import { errorMessage, getAdminActivitySkuRules, getAdminCategories, saveAdminActivitySkuRules } from '../api'
import { notifyError, notifySuccess } from '../feedback'
import type { ActivitySetMapping, ActivitySingleRule, ActivitySkuRules, CategorySummary } from '../types'

const loading = ref(false)
const saving = ref(false)
const loaded = ref(false)
const loadError = ref('')
const categories = ref<CategorySummary[]>([])
const categoryCode = ref('')
const availableSetKeywords = ref<string[]>([])
const setKeywords = ref<string[]>([])
const includeEmptySetKeyword = ref(false)
const setMappings = ref<ActivitySetMapping[]>([])
const singleRules = ref<ActivitySingleRule[]>([])
const allowedPieces = ref<number[]>([])
const supportedSetPieces = computed(() => allowedPieces.value.slice().sort((left, right) => left - right))
const hasSetPieces = computed(() => supportedSetPieces.value.length > 0)
const currentCategory = computed(() => categories.value.find((item) => item.code === categoryCode.value))
const keywordOptions = computed(() => availableSetKeywords.value)

const rulesValid = computed(() => {
  if (setMappings.value.some((item) => !item.pattern.trim())) return false
  if (!singleRules.value.length) return false
  return singleRules.value.every((rule) =>
    rule.mode === 'after_marker' ? !!rule.marker?.trim() : !!rule.delimiter?.trim(),
  )
})

function addSingleRule() {
  singleRules.value.push({ mode: 'last_segment', delimiter: '-' })
}

function removeSingleRule(index: number) {
  singleRules.value.splice(index, 1)
}

function applyRules(rules: ActivitySkuRules) {
  availableSetKeywords.value = rules.set_keywords.filter(Boolean)
  setKeywords.value = [...availableSetKeywords.value]
  includeEmptySetKeyword.value = rules.set_keywords.includes('')
  setMappings.value = rules.set_mappings.map((item) => ({ ...item }))
  // 新格式优先；旧数据（只有单值字段）自动升级为一条规则
  singleRules.value = (rules.single_rules?.length
    ? rules.single_rules
    : [{ mode: rules.single_mode || 'last_segment', delimiter: rules.single_delimiter || '-', marker: rules.single_marker || 'price' }]
  ).map((rule) => ({ ...rule }))
  allowedPieces.value = rules.allowed_pieces || []
}

function buildRules(): ActivitySkuRules {
  return {
    set_keywords: [...new Set(setKeywords.value.map((item) => item.trim()).filter(Boolean).concat(includeEmptySetKeyword.value && hasSetPieces.value ? [''] : []))],
    set_mappings: hasSetPieces.value ? setMappings.value.map((item) => ({ pattern: item.pattern.trim(), pieces: item.pieces })) : [],
    single_rules: singleRules.value.map((rule) => (
      rule.mode === 'after_marker'
        ? { mode: rule.mode, marker: (rule.marker || '').trim() }
        : { mode: rule.mode, delimiter: (rule.delimiter || '').trim() }
    )),
  }
}

async function load() {
  if (!categoryCode.value) return
  loading.value = true
  loaded.value = false
  loadError.value = ''
  try {
    applyRules(await getAdminActivitySkuRules(categoryCode.value))
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
    applyRules(await saveAdminActivitySkuRules(buildRules(), categoryCode.value))
    notifySuccess('默认SKC格式已保存')
  } catch (error) {
    notifyError(error)
  } finally {
    saving.value = false
  }
}

function addSetMapping() {
  setMappings.value.push({ pattern: '', pieces: supportedSetPieces.value[0] || 4 })
}

function removeSetMapping(index: number) {
  setMappings.value.splice(index, 1)
}

async function bootstrap() {
  try {
    const page = await getAdminCategories()
    categories.value = page.items
    categoryCode.value = (categories.value.find((item) => item.is_default) || categories.value[0])?.code || ''
    await load()
  } catch (error) {
    loadError.value = errorMessage(error)
  }
}

onMounted(bootstrap)
</script>

<template>
  <div class="admin-page admin-activity-settings-page">
    <section class="section-band">
      <div class="section-heading">
        <div class="subpage-title">
          <div><h2>批量报活动设置</h2><p>配置用户未启用自定义格式时使用的默认SKC识别规则</p></div>
        </div>
        <div class="admin-settings-actions">
          <el-select v-model="categoryCode" class="admin-region-select" placeholder="选择品类" @change="load">
            <el-option v-for="category in categories" :key="category.code" :label="category.name" :value="category.code">
              <span>{{ category.name }}</span><small style="float: right; color: var(--el-text-color-secondary); font-size: 12px;">{{ category.template_label }}</small>
            </el-option>
          </el-select>
          <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
          <el-button type="primary" :icon="Select" :loading="saving" :disabled="!loaded || !rulesValid" @click="save">保存默认格式</el-button>
        </div>
      </div>

      <el-skeleton v-if="!loaded && !loadError" :rows="8" animated />
      <div v-else-if="loadError" class="settings-load-error"><p>{{ loadError }}</p><el-button :icon="Refresh" :loading="loading" @click="load">重新加载</el-button></div>
      <div v-else class="admin-activity-rule-grid">
        <section class="admin-activity-rule-card">
          <h3>套装识别规则</h3>
          <template v-if="hasSetPieces">
            <p>先匹配固定映射，再按标识前面的数字提取套装件数。</p>
            <label class="admin-activity-rule-field">
              <span>套装标识</span>
              <el-select v-model="setKeywords" multiple filterable allow-create default-first-option placeholder="输入标识后按回车添加">
                <el-option v-for="keyword in keywordOptions" :key="keyword" :label="keyword" :value="keyword" />
              </el-select>
            </label>
            <el-checkbox v-model="includeEmptySetKeyword">套装标识为空（从货号末尾提取件数）</el-checkbox>
            <el-alert v-if="includeEmptySetKeyword" type="warning" :closable="false" show-icon :title="`空标识会把末尾为 ${supportedSetPieces.join('/')} 的货号识别为套装`" />

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
          </template>
          <template v-else>
            <el-alert type="info" :closable="false" show-icon :title="`「${currentCategory?.name || '当前品类'}」为无套装型，无需配置套装识别规则`" description="该品类所有货号将按单品规则提取货值。" />
          </template>
        </section>

        <section class="admin-activity-rule-card">
          <h3>单品货值提取规则</h3>
          <p>套装规则未匹配时，按以下顺序依次尝试提取单品货值，先匹配先用。</p>
          <div class="activity-mapping-heading">
            <span>规则顺序（从上到下依次尝试）</span>
            <el-button link type="primary" :icon="Plus" :disabled="singleRules.length >= 20" @click="addSingleRule">增加规则</el-button>
          </div>
          <div v-if="singleRules.length" class="activity-single-rule-list">
            <div v-for="(rule, index) in singleRules" :key="index" class="activity-single-rule-row">
              <span class="activity-single-rule-order">{{ index + 1 }}</span>
              <el-select v-model="rule.mode" aria-label="单品货值提取方式">
                <el-option label="第一个分隔符前的数字（5-MB131-A → 5）" value="first_segment" />
                <el-option label="最后一个分隔符后的数字（MB131-A-5 → 5）" value="last_segment" />
                <el-option label="指定文字后的数字（MB131-price17.1 → 17.1）" value="after_marker" />
              </el-select>
              <el-input
                v-if="rule.mode === 'after_marker'"
                v-model="rule.marker"
                maxlength="32"
                placeholder="指定文字，例如：price"
              />
              <el-input v-else v-model="rule.delimiter" maxlength="10" placeholder="分隔符，例如：-" />
              <el-button link type="danger" :icon="Delete" :disabled="singleRules.length <= 1" aria-label="删除该规则" @click="removeSingleRule(index)" />
            </div>
          </div>
          <el-empty v-else :image-size="42" description="至少需要一条规则" />
          <el-alert
            v-if="singleRules.length > 1"
            type="info"
            :closable="false"
            show-icon
            title="多条规则按从上到下的顺序尝试，命中第一条即停止"
            description="建议把最可靠的规则放在最上面；前置规则若误匹配，后面的规则不会再执行。"
          />
        </section>
      </div>
    </section>
  </div>
</template>
