<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Delete, Edit, Goods, Plus, Refresh } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { createAdminCategory, deleteAdminCategory, getAdminCategories, getAdminRegions, updateAdminCategory } from '../api'
import { confirmAction, notifyError, notifySuccess } from '../feedback'
import type { CategorySummary, CategoryTemplateType, RegionSummary, TemplateTypeInfo } from '../types'

const router = useRouter()
const categories = ref<CategorySummary[]>([])
const regions = ref<RegionSummary[]>([])
const templateTypes = ref<Record<string, TemplateTypeInfo>>({})
const loading = ref(false)
const saving = ref(false)
const createVisible = ref(false)
const editVisible = ref(false)
const editingCode = ref('')
const createForm = reactive<{ code: string; name: string; template_type: CategoryTemplateType; setTypesText: string; regionCodes: string[]; sort_order: number }>({
  code: '', name: '', template_type: 'set_based', setTypesText: '', regionCodes: [], sort_order: 100,
})
const editForm = reactive<{ name: string; template_type: CategoryTemplateType; setTypesText: string; regionCodes: string[]; enabled: boolean; is_default: boolean; sort_order: number }>({
  name: '', template_type: 'set_based', setTypesText: '', regionCodes: [], enabled: true, is_default: false, sort_order: 100,
})

const templateOptions = computed(() => Object.entries(templateTypes.value).map(([value, info]) => ({ value, ...info })))
const createTemplateInfo = computed(() => templateTypes.value[createForm.template_type])
const editTemplateInfo = computed(() => templateTypes.value[editForm.template_type])
const allRegionCodes = computed(() => regions.value.map((region) => region.code))

function regionName(code: string) {
  return regions.value.find((region) => region.code === code)?.name || code
}

function allowedRegionsLabel(category: CategorySummary) {
  if (category.allowed_regions === null) return '全部区域'
  return category.allowed_regions.map(regionName).join('、') || '全部区域'
}

// 下拉不选 = 全部区域（allowed_regions 存 null）；选中全部区域同样视为全部开放
function toAllowedRegions(codes: string[]): string[] | null {
  if (!codes.length || codes.length === allRegionCodes.value.length) return null
  return [...codes].sort()
}

function parseSetTypes(text: string): number[] {
  return [...new Set(text.split(/[,，\s]+/).map((item) => item.trim()).filter(Boolean).map((item) => Number(item)))]
}

function setTypesValid(pieces: number[]): boolean {
  return pieces.length > 0 && pieces.every((item) => Number.isInteger(item) && item >= 2 && item <= 30) && pieces.length <= 12
}

function templateTypeLabel(category: CategorySummary) {
  const info = templateTypes.value[category.template_type]
  const suffix = category.template_type === 'custom_set' && category.set_types.length
    ? `（${category.set_types.map((item) => `${item}件套`).join('、')}）`
    : ''
  return `${info?.label || category.template_type}${suffix}`
}

async function load() {
  loading.value = true
  try {
    const [page, regionItems] = await Promise.all([getAdminCategories(), getAdminRegions()])
    categories.value = page.items
    templateTypes.value = page.template_types
    regions.value = regionItems
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
  }
}

async function create() {
  const setTypes = createForm.template_type === 'custom_set' ? parseSetTypes(createForm.setTypesText) : []
  if (createForm.template_type === 'custom_set' && !setTypesValid(setTypes)) {
    notifyError(new Error('自定义套装档位需为 2-30 的整数件数，至少 1 个、最多 12 个'))
    return
  }
  const allowedRegions = toAllowedRegions(createForm.regionCodes)
  saving.value = true
  try {
    await createAdminCategory({
      code: createForm.code.trim().toUpperCase(),
      name: createForm.name.trim(),
      template_type: createForm.template_type,
      set_types: setTypes,
      allowed_regions: allowedRegions,
      sort_order: createForm.sort_order,
    })
    createVisible.value = false
    Object.assign(createForm, { code: '', name: '', template_type: 'set_based', setTypesText: '', sort_order: 100 })
    notifySuccess('品类已创建，已为开放区域生成默认参数')
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    saving.value = false
  }
}

function openEdit(category: CategorySummary) {
  editingCode.value = category.code
  Object.assign(editForm, {
    name: category.name,
    template_type: category.template_type,
    setTypesText: category.set_types.join('、'),
    regionCodes: category.allowed_regions === null ? [] : [...category.allowed_regions],
    enabled: category.enabled,
    is_default: category.is_default,
    sort_order: category.sort_order,
  })
  editVisible.value = true
}

async function saveEdit() {
  const setTypes = editForm.template_type === 'custom_set' ? parseSetTypes(editForm.setTypesText) : []
  if (editForm.template_type === 'custom_set' && !setTypesValid(setTypes)) {
    notifyError(new Error('自定义套装档位需为 2-30 的整数件数，至少 1 个、最多 12 个'))
    return
  }
  const allowedRegions = toAllowedRegions(editForm.regionCodes)
  saving.value = true
  try {
    await updateAdminCategory(editingCode.value, {
      name: editForm.name.trim(),
      template_type: editForm.template_type,
      set_types: setTypes,
      allowed_regions: allowedRegions,
      enabled: editForm.enabled,
      is_default: editForm.is_default,
      sort_order: editForm.sort_order,
    })
    editVisible.value = false
    notifySuccess('品类已更新')
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(category: CategorySummary) {
  try {
    await updateAdminCategory(category.code, {
      name: category.name,
      template_type: category.template_type,
      set_types: category.set_types,
      allowed_regions: category.allowed_regions,
      enabled: !category.enabled,
      is_default: category.is_default,
      sort_order: category.sort_order,
    })
    notifySuccess(category.enabled ? '品类已停用' : '品类已启用')
    await load()
  } catch (error) {
    notifyError(error)
  }
}

async function remove(category: CategorySummary) {
  if (!await confirmAction(`确认删除品类“${category.name}”？其参数配置和减半名单将一并删除。`, '删除品类')) return
  try {
    await deleteAdminCategory(category.code)
    notifySuccess('品类已删除')
    await load()
  } catch (error) {
    notifyError(error)
  }
}

function editParameters(category: CategorySummary) {
  router.push({ path: '/admin/settings', query: { category: category.code } })
}

async function openCreate() {
  createForm.regionCodes = [...allRegionCodes.value]
  createVisible.value = true
}

onMounted(load)
</script>

<template>
  <div class="admin-page admin-categories-page">
    <section class="section-band">
      <div class="section-heading">
        <div class="subpage-title"><div><h2>品类管理</h2><p>按品类配置不同的成本参数模版，参数在「成本参数」页按品类维护</p></div></div>
        <div class="admin-settings-actions"><el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button><el-button type="primary" :icon="Plus" @click="openCreate">新增品类</el-button></div>
      </div>

      <el-table :data="categories" v-loading="loading" stripe>
        <el-table-column prop="name" label="品类" min-width="120" />
        <el-table-column prop="code" label="代码" width="90" />
        <el-table-column label="模版类型" min-width="180"><template #default="scope">{{ templateTypeLabel(scope.row) }}</template></el-table-column>
        <el-table-column label="开放区域" min-width="150"><template #default="scope">{{ allowedRegionsLabel(scope.row) }}</template></el-table-column>
        <el-table-column prop="sort_order" label="排序" width="70" />
        <el-table-column label="状态" width="110"><template #default="scope"><el-tag :type="scope.row.enabled ? 'success' : 'info'">{{ scope.row.enabled ? '启用' : '停用' }}</el-tag></template></el-table-column>
        <el-table-column label="默认品类" width="120"><template #default="scope"><el-tag v-if="scope.row.is_default" type="primary">默认</el-tag></template></el-table-column>
        <el-table-column label="操作" min-width="330" align="right">
          <template #default="scope">
            <el-button link type="primary" :icon="Edit" @click="openEdit(scope.row)">编辑</el-button>
            <el-button link type="primary" @click="editParameters(scope.row)">配置参数</el-button>
            <el-button link :type="scope.row.enabled ? 'warning' : 'success'" :disabled="scope.row.is_default" @click="toggleEnabled(scope.row)">{{ scope.row.enabled ? '停用' : '启用' }}</el-button>
            <el-button link type="danger" :icon="Delete" :disabled="scope.row.is_default" @click="remove(scope.row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <el-dialog v-model="createVisible" title="新增品类" width="min(560px, calc(100vw - 32px))" :close-on-click-modal="false">
      <el-form label-position="top">
        <div class="region-create-grid category-code-grid">
          <el-form-item label="品类代码"><el-input v-model="createForm.code" maxlength="16" placeholder="例如：B" /><div class="region-code-hint">1-16位大写字母/数字/下划线/短横线，创建后不可修改</div></el-form-item>
          <el-form-item label="品类名称"><el-input v-model="createForm.name" maxlength="80" placeholder="例如：B品类" /></el-form-item>
        </div>
        <el-form-item label="参数模版">
          <div class="category-template-options">
            <button
              v-for="template in templateOptions"
              :key="template.value"
              type="button"
              class="category-template-option"
              :class="{ active: createForm.template_type === template.value }"
              @click="createForm.template_type = template.value as CategoryTemplateType"
            >
              <strong>{{ template.label }}</strong>
              <small>{{ template.description }}</small>
            </button>
          </div>
        </el-form-item>
        <el-form-item v-if="createForm.template_type === 'custom_set'">
          <template #label>自定义套装档位<span class="category-field-hint">输入件数（2-30），用逗号或空格分隔，如：4、6、8</span></template>
          <el-input v-model="createForm.setTypesText" placeholder="4 6 8" />
        </el-form-item>
        <el-form-item>
          <template #label>开放区域<span class="category-field-hint">选择该品类对用户可见的区域，不选 = 全部区域</span></template>
          <el-select v-model="createForm.regionCodes" multiple collapse-tags collapse-tags-tooltip clearable placeholder="全部区域" class="category-region-select">
            <el-option v-for="region in regions" :key="region.code" :label="region.name" :value="region.code" />
          </el-select>
        </el-form-item>
        <el-form-item label="显示排序"><el-input-number v-model="createForm.sort_order" :min="-10000" :max="10000" controls-position="right" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="createVisible = false">取消</el-button><el-button type="primary" :loading="saving" :disabled="!createForm.code.trim() || !createForm.name.trim()" @click="create">创建品类</el-button></template>
    </el-dialog>

    <el-dialog v-model="editVisible" title="编辑品类" width="min(560px, calc(100vw - 32px))" :close-on-click-modal="false">
      <el-form label-position="top">
        <el-form-item label="品类名称"><el-input v-model="editForm.name" maxlength="80" /></el-form-item>
        <el-form-item>
          <template #label>参数模版<span class="category-field-hint">模版类型创建后不可变更</span></template>
          <el-input :model-value="`${editTemplateInfo?.label || editForm.template_type}（不可变更）`" disabled />
        </el-form-item>
        <el-form-item v-if="editForm.template_type === 'custom_set'">
          <template #label>自定义套装档位<span class="category-field-hint">输入件数（2-30），用逗号或空格分隔</span></template>
          <el-input v-model="editForm.setTypesText" placeholder="4 6 8" />
        </el-form-item>
        <el-form-item>
          <template #label>开放区域<span class="category-field-hint">选择该品类对用户可见的区域，不选 = 全部区域</span></template>
          <el-select v-model="editForm.regionCodes" multiple collapse-tags collapse-tags-tooltip clearable placeholder="全部区域" class="category-region-select">
            <el-option v-for="region in regions" :key="region.code" :label="region.name" :value="region.code" />
          </el-select>
        </el-form-item>
        <div class="region-create-grid">
          <el-form-item label="显示排序"><el-input-number v-model="editForm.sort_order" :min="-10000" :max="10000" controls-position="right" /></el-form-item>
          <el-form-item label="状态">
            <el-switch v-model="editForm.enabled" active-text="启用" inactive-text="停用" :disabled="editForm.is_default" />
          </el-form-item>
        </div>
        <el-form-item label="默认品类"><el-switch v-model="editForm.is_default" active-text="设为默认" :disabled="!editForm.enabled && !editForm.is_default" /><div class="region-code-hint">用户未选择品类时使用默认品类</div></el-form-item>
      </el-form>
      <template #footer><el-button @click="editVisible = false">取消</el-button><el-button type="primary" :loading="saving" :disabled="!editForm.name.trim() || !editForm.regionCodes.length" @click="saveEdit">保存修改</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.category-template-options {
  display: grid;
  gap: 10px;
  width: 100%;
}

.category-template-option {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 14px;
  border: 1px solid var(--el-border-color);
  border-radius: 10px;
  background: var(--el-fill-color-blank);
  text-align: left;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
}

.category-template-option:hover {
  border-color: var(--el-color-primary-light-5);
}

.category-template-option.active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.category-template-option small {
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}

.category-field-hint {
  margin-left: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-weight: normal;
}

.category-region-select {
  width: 100%;
}

/* 品类代码短（≤16字符），收紧为 1:2 比例 */
.category-code-grid {
  grid-template-columns: 1fr 2fr;
}
</style>
