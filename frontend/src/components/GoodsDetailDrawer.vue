<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { getProductLifecycle, getStoreGoodsDetail } from '../api'
import { notifyError } from '../feedback'
import type { StoreGoodsDetail, StoreGoodsLifecycle } from '../types'

const props = defineProps<{ modelValue: boolean; productId: string | null }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

const loading = ref(false)
const detail = ref<StoreGoodsDetail | null>(null)
const lifecycle = ref<StoreGoodsLifecycle | null>(null)
const failed = ref('')
const activeImage = ref('')

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const gallery = computed(() => {
  const images = detail.value?.carousel?.filter(Boolean) ?? []
  const extras = detail.value?.outer_package?.filter(Boolean) ?? []
  return [...images, ...extras]
})

const translatedTitles = computed(() =>
  (detail.value?.i18n ?? []).filter((item) => item.name && item.language),
)

const categoryChain = computed(() => {
  const categories = detail.value?.categories ?? null
  if (!categories) return []
  return Object.entries(categories)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([, value]) => value?.catName)
    .filter((name): name is string => !!name)
})

const skuCount = computed(() =>
  (detail.value?.skc_list ?? []).reduce((total, skc) => total + (skc.skus?.length ?? 0), 0),
)

function formatDate(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  const pad = (input: number) => String(input).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

async function load(productId: string) {
  loading.value = true
  failed.value = ''
  detail.value = null
  lifecycle.value = null
  activeImage.value = ''
  try {
    const data = await getStoreGoodsDetail(productId)
    detail.value = data
    activeImage.value = data.main_image || data.carousel?.[0] || ''
  } catch (error) {
    notifyError(error)
    failed.value = '商品详情加载失败'
  }
  // 生命周期时间轴（独立接口，失败不阻塞详情）
  try {
    lifecycle.value = await getProductLifecycle(productId)
  } catch {
    lifecycle.value = null
  }
  loading.value = false
}

watch(
  () => [props.modelValue, props.productId] as const,
  ([open, productId]) => {
    if (open && productId) void load(productId)
  },
  { immediate: true },
)
</script>

<template>
  <el-drawer v-model="visible" size="min(760px, 100vw)" :with-header="true" title="商品详情" destroy-on-close>
    <div v-if="loading" class="detail-loading">
      <el-skeleton :rows="6" animated />
    </div>

    <el-empty v-else-if="failed" :description="failed" />

    <template v-else-if="detail">
      <div class="detail-head">
        <el-image
          v-if="activeImage"
          :src="activeImage"
          :preview-src-list="gallery"
          fit="cover"
          class="detail-cover"
          preview-teleported
        />
        <div class="detail-meta">
          <h3>{{ detail.product_name || '（无标题）' }}</h3>
          <div class="detail-ids">
            <span>SPU {{ detail.product_id }}</span>
            <span v-if="detail.skc_list.length">SKC {{ detail.skc_list.length }} 个</span>
            <span v-if="skuCount">SKU {{ skuCount }} 个</span>
          </div>
          <div class="detail-flags">
            <el-tag v-if="detail.videos.carousel.length" size="small" effect="plain">轮播视频</el-tag>
            <el-tag v-if="detail.videos.detail.length" size="small" effect="plain">详情视频</el-tag>
            <el-tag v-if="detail.material_image" size="small" effect="plain" type="info">素材图</el-tag>
            <el-tag v-if="categoryChain.length" size="small" effect="plain" type="success">
              {{ categoryChain.join(' / ') }}
            </el-tag>
          </div>
        </div>
      </div>

      <div v-if="gallery.length > 1" class="detail-gallery">
        <button
          v-for="image in gallery"
          :key="image"
          type="button"
          class="gallery-item"
          :class="{ active: image === activeImage }"
          @click="activeImage = image"
        >
          <img :src="image" alt="" loading="lazy" />
        </button>
      </div>

      <el-collapse v-if="translatedTitles.length" class="detail-collapse">
        <el-collapse-item :title="`多语言标题（${translatedTitles.length}）`" name="i18n">
          <ul class="i18n-list">
            <li v-for="item in translatedTitles" :key="`${item.language}`">
              <span class="i18n-lang">{{ item.language }}</span>
              <span>{{ item.name }}</span>
            </li>
          </ul>
        </el-collapse-item>
      </el-collapse>

      <h4 class="detail-section">商品属性</h4>
      <div v-if="lifecycle && (lifecycle.properties || []).length" class="prop-grid">
        <div v-for="item in lifecycle.properties" :key="item.name" class="prop-item">
          <span class="prop-name">{{ item.name }}</span>
          <span class="prop-value">{{ item.value || '—' }}</span>
        </div>
      </div>
      <p v-else class="lc-empty">没有商品属性数据</p>

      <h4 class="detail-section">上新生命周期</h4>
      <div v-if="lifecycle && lifecycle.skc_list.length" class="lc-list">
        <div v-for="skc in lifecycle.skc_list" :key="skc.skc_id" class="skc-card">
          <div class="skc-head">
            <el-tag size="small" :type="skc.select_status === 12 ? 'success' : 'info'">
              {{ skc.status_name }}
            </el-tag>
            <div>
              <strong>SKC {{ skc.skc_id }}</strong>
              <p>
                {{ skc.color_name || '—' }}
                <span v-if="skc.can_off_sale"> · 可下架</span>
                <span v-if="skc.status_desc"> · {{ skc.status_desc }}</span>
              </p>
            </div>
          </div>
          <el-timeline v-if="skc.stages.length" class="lc-timeline">
            <el-timeline-item
              v-for="stage in skc.stages"
              :key="stage.key"
              :timestamp="formatDate(stage.date)"
              placement="top"
            >
              {{ stage.label }}
            </el-timeline-item>
          </el-timeline>
          <p v-else class="lc-empty">暂无时间轴记录</p>
        </div>
      </div>
      <p v-else class="lc-empty">没有查到该商品的生命周期记录（可能不在上新流程里）</p>

      <h4 class="detail-section">SKC / SKU 结构</h4>
      <div v-for="skc in detail.skc_list" :key="skc.product_skc_id" class="skc-card">
        <div class="skc-head">
          <el-image
            v-if="skc.color_image || skc.preview_images[0]"
            :src="skc.color_image || skc.preview_images[0] || ''"
            fit="cover"
            class="skc-thumb"
          />
          <div>
            <strong>SKC {{ skc.product_skc_id }}</strong>
            <p>{{ skc.skus.length }} 个 SKU · {{ skc.preview_images.length }} 张预览图</p>
          </div>
        </div>
        <div class="sku-row">
          <div v-for="sku in skc.skus" :key="sku.product_sku_id" class="sku-chip">
            <el-image v-if="sku.thumb" :src="sku.thumb" fit="cover" class="sku-thumb" />
            <span>{{ sku.product_sku_id }}</span>
          </div>
        </div>
      </div>
      <el-empty v-if="!detail.skc_list.length" description="该商品没有返回 SKC 明细" :image-size="72" />

    </template>
  </el-drawer>
</template>

<style scoped>
.prop-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 6px 16px;
}

.prop-item {
  display: flex;
  gap: 8px;
  font-size: 12px;
  line-height: 1.5;
}

.prop-name {
  flex: none;
  min-width: 84px;
  color: var(--text-muted, #9ca3af);
}

.prop-value {
  color: var(--text, #303133);
}

.detail-loading {
  padding: 8px;
}

.detail-head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}

.detail-cover {
  width: 168px;
  height: 168px;
  border-radius: 12px;
  flex-shrink: 0;
  background: #f3f4f6;
}

.detail-meta h3 {
  margin: 0 0 8px;
  font-size: 16px;
  line-height: 1.5;
}

.detail-ids {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  color: var(--text-muted, #6b7280);
  font-size: 12px;
  font-family: Consolas, Menlo, monospace;
}

.detail-flags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.detail-gallery {
  display: flex;
  gap: 8px;
  margin-top: 14px;
  overflow-x: auto;
  padding-bottom: 4px;
}

.gallery-item {
  padding: 0;
  border: 2px solid transparent;
  border-radius: 8px;
  background: none;
  cursor: pointer;
  line-height: 0;
  flex-shrink: 0;
}

.gallery-item.active {
  border-color: var(--primary, #2563eb);
}

.gallery-item img {
  width: 64px;
  height: 64px;
  object-fit: cover;
  border-radius: 6px;
}

.detail-collapse {
  margin-top: 14px;
}

.i18n-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.i18n-list li {
  display: flex;
  gap: 10px;
  font-size: 13px;
}

.i18n-lang {
  color: var(--text-muted, #6b7280);
  min-width: 52px;
  font-family: Consolas, Menlo, monospace;
}

.detail-section {
  margin: 20px 0 10px;
  font-size: 14px;
}

.skc-card {
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 10px;
  padding: 10px 12px;
  margin-bottom: 10px;
}

.skc-head {
  display: flex;
  gap: 10px;
  align-items: center;
}

.skc-thumb {
  width: 44px;
  height: 44px;
  border-radius: 8px;
  background: #f3f4f6;
  flex-shrink: 0;
}

.skc-head p {
  margin: 2px 0 0;
  color: var(--text-muted, #6b7280);
  font-size: 12px;
}

.sku-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.sku-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px 4px 4px;
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 999px;
  font-size: 12px;
  font-family: Consolas, Menlo, monospace;
}

.sku-thumb {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #f3f4f6;
}

</style>
