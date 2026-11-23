<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowRight, Search } from '@element-plus/icons-vue'
import { getCategoryChildren, getCategoryHints, searchProductCategories } from '../api'
import { notifyError, notifyWarning } from '../feedback'
import type { ProductCategoryNode, ProductCategoryPath } from '../types'

/**
 * Temu 平台类目选择器（区别于项目内的业务品类 CategoryPicker）。
 *
 * 两种方式并存：
 *  - 逐级展开（categoryChildren）：从一级点到最后一级；
 *  - 按名称搜索（querySuggestion）：填商品名让平台推荐完整路径。
 */
const props = defineProps<{ modelValue: boolean; selected?: number[] }>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  confirm: [payload: { catPath: number[]; names: string[] }]
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const mode = ref<'tree' | 'search'>('search')
const loading = ref(false)
const keyword = ref('')
const searching = ref(false)

/** 每一级的节点列表；index 0 = 一级 */
const levels = ref<ProductCategoryNode[][]>([])
/** 每一级已选中的节点 */
const picked = ref<ProductCategoryNode[]>([])
const candidates = ref<ProductCategoryPath[]>([])

const currentNodes = computed(() => levels.value[levels.value.length - 1] || [])
const breadcrumb = computed(() => picked.value.map((n) => n.catName))
const resultPath = computed(() => picked.value.map((n) => n.catId))

async function loadLevel(parentCatId: number, depth: number) {
  loading.value = true
  try {
    const data = await getCategoryChildren(parentCatId)
    levels.value = [...levels.value.slice(0, depth), (data.nodes || []).filter((n) => !n.isHidden)]
    picked.value = picked.value.slice(0, depth)
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
  }
}

async function pickNode(node: ProductCategoryNode) {
  const depth = levels.value.length - 1
  picked.value = [...picked.value.slice(0, depth), node]
  if (node.isLeaf) {
    emit('confirm', { catPath: resultPath.value, names: breadcrumb.value })
    visible.value = false
    return
  }
  await loadLevel(node.catId, depth + 1)
}

async function backTo(depth: number) {
  picked.value = picked.value.slice(0, depth)
  levels.value = levels.value.slice(0, depth + 1)
}

let searchTimer: number | undefined

function onKeywordInput() {
  if (searchTimer) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => void search(true), 450)
}

async function search(silent = false) {
  const name = keyword.value.trim()
  if (!name) {
    candidates.value = []
    return
  }
  searching.value = true
  candidates.value = []
  try {
    // searchCategories：官网分类搜索框的真实接口（多条模糊，常见几十条）
    const result = await searchProductCategories(name)
    candidates.value = result.paths
    if (!result.total && !silent) notifyWarning('没有匹配到类目，换个更通用的词，或改用逐级选择')
  } catch (error) {
    notifyError(error)
  } finally {
    searching.value = false
  }
}

function pickCandidate(item: ProductCategoryPath) {
  emit('confirm', { catPath: item.catPath, names: item.names })
  visible.value = false
}

function applyCurrent() {
  if (!resultPath.value.length) return
  emit('confirm', { catPath: resultPath.value, names: breadcrumb.value })
  visible.value = false
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    levels.value = []
    picked.value = []
    candidates.value = []
    keyword.value = ''
    void loadLevel(0, 0)
    // 官网新建页顶部的「常用推荐」
    void getCategoryHints()
      .then((hints) => {
        if (!keyword.value.trim()) candidates.value = hints.paths
      })
      .catch(() => { /* 推荐拿不到不影响搜索 */ })
  },
)
</script>

<template>
  <el-dialog v-model="visible" title="选择 Temu 类目" width="min(760px, calc(100vw - 32px))">
    <el-radio-group v-model="mode" size="small" class="cat-mode">
      <el-radio-button value="search">按名称搜索</el-radio-button>
      <el-radio-button value="tree">逐级选择</el-radio-button>
    </el-radio-group>

    <div v-if="mode === 'tree'" v-loading="loading" class="cat-tree">
      <div class="cat-breadcrumb">
        <button type="button" class="cat-crumb" @click="backTo(0)">全部类目</button>
        <template v-for="(name, index) in breadcrumb" :key="name + index">
          <el-icon class="cat-sep"><ArrowRight /></el-icon>
          <button type="button" class="cat-crumb" @click="backTo(index + 1)">{{ name }}</button>
        </template>
      </div>

      <div class="cat-nodes">
        <button
          v-for="node in currentNodes"
          :key="node.catId"
          type="button"
          class="cat-node"
          :class="{ 'cat-node--leaf': node.isLeaf }"
          @click="pickNode(node)"
        >
          <span class="cat-node__name">{{ node.catName }}</span>
          <span v-if="node.isLeaf" class="cat-node__badge">末级</span>
          <el-icon v-else class="cat-node__arrow"><ArrowRight /></el-icon>
        </button>
        <el-empty v-if="!loading && !currentNodes.length" description="该层没有子类目" :image-size="56" />
      </div>
    </div>

    <div v-else class="cat-search">
      <div class="cat-search__bar">
        <el-input
          v-model="keyword"
          placeholder="输入关键字自动匹配类目（如：情趣、连衣裙）"
          clearable
          autofocus
          :prefix-icon="Search"
          @input="onKeywordInput"
          @keyup.enter="search()"
        />
      </div>
      <div v-if="candidates.length" class="cat-candidates">
        <button
          v-for="item in candidates"
          :key="item.label"
          type="button"
          class="cat-candidate"
          @click="pickCandidate(item)"
        >{{ item.label }}</button>
      </div>
      <el-empty v-else description="输入关键字即时匹配，或从上方常用推荐里选" :image-size="56" />
    </div>

    <template #footer>
      <span class="cat-current">
        已选：{{ breadcrumb.length ? breadcrumb.join(' > ') : '未选择' }}
      </span>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :disabled="!resultPath.length" @click="applyCurrent">使用该路径</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.cat-mode {
  margin-bottom: 12px;
}

.cat-tree {
  min-height: 260px;
}

.cat-breadcrumb {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  padding-bottom: 8px;
  margin-bottom: 8px;
  border-bottom: 1px solid var(--border, #e5e7eb);
}

.cat-crumb {
  padding: 2px 6px;
  color: var(--primary, #2563eb);
  font-size: 12px;
  background: transparent;
  border: 0;
  border-radius: 4px;
  cursor: pointer;
}

.cat-crumb:hover {
  background: #eff6ff;
}

.cat-sep {
  font-size: 10px;
  color: var(--muted, #9ca3af);
}

.cat-nodes {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 44vh;
  overflow-y: auto;
}

.cat-node {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  font-size: 13px;
  text-align: left;
  background: transparent;
  border: 0;
  border-radius: 6px;
  cursor: pointer;
}

.cat-node:hover {
  background: var(--surface-subtle, #f3f4f6);
}

.cat-node--leaf:hover {
  color: var(--primary, #2563eb);
  background: #eff6ff;
}

.cat-node__name {
  flex: 1;
}

.cat-node__badge {
  padding: 1px 6px;
  color: #059669;
  font-size: 11px;
  background: #ecfdf5;
  border-radius: 4px;
}

.cat-node__arrow {
  font-size: 12px;
  color: var(--muted, #9ca3af);
}

.cat-search__bar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.cat-candidates {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 44vh;
  overflow-y: auto;
}

.cat-candidate {
  padding: 9px 12px;
  color: var(--primary, #2563eb);
  font-size: 12px;
  text-align: left;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  border-radius: 6px;
  cursor: pointer;
}

.cat-candidate:hover {
  background: #dbeafe;
}

.cat-current {
  margin-right: auto;
  color: var(--muted, #6b7280);
  font-size: 12px;
}
</style>
