<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { TopRight } from '@element-plus/icons-vue'
import { getAdminUpdateStatus, getVersion } from '../api'
import { notifyError } from '../feedback'
import type { UpdateSnapshot, VersionEntry, VersionInfo } from '../types'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()

const loading = ref(false)
const checking = ref(false)
const info = ref<VersionInfo | null>(null)
const snapshot = ref<UpdateSnapshot | null>(null)

const currentVersion = computed(() => info.value?.version || '')
const remoteVersion = computed(() => snapshot.value?.remote_version || '')
const updaterEnabled = computed(() => Boolean(snapshot.value))
const entries = computed<VersionEntry[]>(() => info.value?.changelog || [])

function parts(value: string) {
  return value.split(/[.\-+]/).slice(0, 3).map((piece) => Number.parseInt(piece, 10) || 0)
}

const updateAvailable = computed(() => {
  const remote = remoteVersion.value
  if (!remote || !currentVersion.value) return false
  const local = parts(currentVersion.value)
  const latest = parts(remote)
  for (let index = 0; index < 3; index += 1) {
    if (latest[index] !== local[index]) return latest[index] > local[index]
  }
  return false
})

function pillType(title: string) {
  if (title.includes('新增')) return 'new'
  if (title.includes('优化')) return 'better'
  if (title.includes('修复')) return 'fix'
  return 'other'
}

function close() {
  emit('update:modelValue', false)
}

function onVisible(value: boolean) {
  emit('update:modelValue', value)
}

async function load(manual = false) {
  if (manual) checking.value = true
  else loading.value = true
  try {
    info.value = await getVersion()
  } catch (error) {
    info.value = null
    notifyError(error)
  }
  try {
    // 未启用一键热更新时拿不到快照，这里静默降级：只显示当前版本与本地日志
    snapshot.value = (await getAdminUpdateStatus()).data
  } catch {
    snapshot.value = null
  }
  loading.value = false
  checking.value = false
}

function openReleasePage() {
  window.open('https://github.com/xiaosu7788/temu-box/commits/main', '_blank')
}

watch(
  () => props.modelValue,
  (visible) => {
    if (visible) void load()
  },
)
</script>

<template>
  <el-dialog :model-value="props.modelValue" width="720px" append-to-body @update:model-value="onVisible">
    <template #header>
      <div class="dialog-head">
        <strong>版本更新</strong>
        <span>查看当前版本和更新日志</span>
      </div>
    </template>

    <div v-loading="loading" class="version-body">
      <div class="version-row">
        <div class="version-box">
          <span class="box-label">当前版本</span>
          <strong class="box-value">{{ currentVersion ? `v${currentVersion}` : '—' }}</strong>
        </div>
        <div class="version-box">
          <div class="box-head">
            <span class="box-label">最新版本</span>
            <button class="check-button" type="button" :disabled="checking" @click="load(true)">
              {{ checking ? '检查中…' : '检查更新' }}
            </button>
          </div>
          <strong v-if="remoteVersion" class="box-value" :class="{ newer: updateAvailable }">v{{ remoteVersion }}</strong>
          <span v-else class="box-muted">{{ updaterEnabled ? '暂时读不到远端版本' : '未启用更新检查' }}</span>
        </div>
      </div>

      <div v-if="updateAvailable" class="new-banner">
        <span class="banner-text">发现新版本: v{{ remoteVersion }}</span>
        <span class="banner-pill">可更新</span>
      </div>

      <div class="log-list">
        <div v-for="(entry, index) in entries" :key="entry.version" class="log-entry">
          <div class="entry-head">
            <strong>{{ entry.version }}</strong>
            <span class="entry-date">{{ entry.date }}</span>
            <span v-if="index === 0" class="entry-latest">最新</span>
          </div>
          <div v-for="group in entry.groups" :key="group.title" class="entry-group">
            <div v-for="(item, itemIndex) in group.items" :key="itemIndex" class="entry-item">
              <span class="item-pill" :class="pillType(group.title)">{{ group.title }}</span>
              <span class="item-text">{{ item }}</span>
            </div>
          </div>
        </div>
        <el-empty v-if="!loading && !entries.length" description="暂无更新日志" :image-size="60" />
      </div>
    </div>

    <template #footer>
      <div class="dialog-foot">
        <button class="release-link" type="button" @click="openReleasePage">
          打开发布页
          <el-icon><TopRight /></el-icon>
        </button>
        <el-button type="primary" @click="close">知道了</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.version-body {
  display: grid;
  gap: 14px;
}

.version-row {
  display: flex;
  gap: 12px;
}

.version-box {
  flex: 1 1 0;
  min-width: 0;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
}

.box-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.box-label {
  color: var(--text-muted);
  font-size: 12.5px;
}

.box-value {
  display: block;
  margin-top: 6px;
  color: var(--text-strong);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 22px;
  font-weight: 700;
}

.box-value.newer {
  color: var(--danger);
}

.box-muted {
  display: block;
  margin-top: 6px;
  color: var(--text-placeholder);
  font-size: 13px;
}

.check-button {
  padding: 0;
  border: 0;
  color: var(--primary);
  background: transparent;
  font-size: 12.5px;
  cursor: pointer;
}

.check-button:disabled {
  color: var(--text-placeholder);
  cursor: default;
}

.new-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border: 1px solid #bfe0d2;
  border-radius: 8px;
  background: #eef7f2;
}

.banner-text {
  color: var(--success);
  font-size: 14px;
  font-weight: 600;
}

.banner-pill {
  padding: 2px 10px;
  border-radius: 999px;
  background: var(--success);
  color: #fff;
  font-size: 12px;
}

.log-list {
  max-height: 320px;
  overflow: auto;
  padding-right: 4px;
}

.log-entry + .log-entry {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px dashed var(--border);
}

.entry-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.entry-head strong {
  color: var(--text-strong);
  font-size: 15px;
}

.entry-date {
  color: var(--text-muted);
  font-size: 12.5px;
}

.entry-latest {
  padding: 1px 8px;
  border-radius: 999px;
  background: #eef7f2;
  color: var(--success);
  font-size: 12px;
}

.entry-group + .entry-group {
  margin-top: 6px;
}

.entry-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 4px 0;
}

.item-pill {
  flex: 0 0 auto;
  margin-top: 1px;
  padding: 1px 8px;
  border-radius: 4px;
  font-size: 12px;
}

.item-pill.new {
  background: #eef4ff;
  color: var(--primary);
}

.item-pill.better {
  background: #fdf6ec;
  color: #b88230;
}

.item-pill.fix {
  background: #fdeceb;
  color: var(--danger);
}

.item-pill.other {
  background: #f4f6f8;
  color: var(--text-muted);
}

.item-text {
  color: var(--text-regular);
  font-size: 13px;
  line-height: 1.7;
}

.dialog-head strong {
  display: block;
  color: var(--text-strong);
  font-size: 16px;
}

.dialog-head span {
  color: var(--text-muted);
  font-size: 12.5px;
}

.dialog-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.release-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0;
  border: 0;
  color: var(--text-muted);
  background: transparent;
  font-size: 13px;
  cursor: pointer;
}

.release-link:hover {
  color: var(--primary);
}
</style>
