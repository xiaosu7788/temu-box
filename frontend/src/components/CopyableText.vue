<script setup lang="ts">
import { ref } from 'vue'
import { Check, CopyDocument } from '@element-plus/icons-vue'
import { notifySuccess } from '../feedback'

const props = withDefaults(
  defineProps<{
    text: string
    copyText?: string
    mono?: boolean
    tooltip?: string
  }>(),
  {
    copyText: '',
    mono: true,
    tooltip: '点击复制',
  },
)

const copied = ref(false)
let timer: number | undefined

async function doCopy() {
  const content = props.copyText || props.text
  if (!content) return
  try {
    await navigator.clipboard.writeText(content)
    copied.value = true
    notifySuccess(`已复制: ${content}`)
    if (timer) window.clearTimeout(timer)
    timer = window.setTimeout(() => {
      copied.value = false
    }, 1800)
  } catch {
    // 兼容剪贴板 API 不可用的情况
    const textarea = document.createElement('textarea')
    textarea.value = content
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    try {
      document.execCommand('copy')
      copied.value = true
      notifySuccess(`已复制: ${content}`)
      if (timer) window.clearTimeout(timer)
      timer = window.setTimeout(() => {
        copied.value = false
      }, 1800)
    } finally {
      document.body.removeChild(textarea)
    }
  }
}
</script>

<template>
  <span
    class="copyable-text"
    :class="{ 'is-copied': copied, 'is-mono': mono }"
    :title="tooltip"
    role="button"
    tabindex="0"
    @click.stop="doCopy"
    @keydown.enter.stop="doCopy"
  >
    <span class="copyable-label"><slot>{{ text }}</slot></span>
    <el-icon class="copy-icon">
      <Check v-if="copied" class="success-icon" />
      <CopyDocument v-else />
    </el-icon>
  </span>
</template>

<style scoped>
.copyable-text {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  padding: 1px 5px;
  border-radius: 4px;
  transition: background-color 0.15s ease, color 0.15s ease;
  user-select: all;
}

.copyable-text:hover {
  background-color: rgba(37, 99, 235, 0.08);
  color: var(--primary, #2563eb);
}

.copyable-text.is-mono {
  font-family: Consolas, "SFMono-Regular", Menlo, Monaco, monospace;
}

.copyable-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.copy-icon {
  font-size: 13px;
  opacity: 0.55;
  flex-shrink: 0;
  transition: opacity 0.15s ease, transform 0.15s ease;
}

.copyable-text:hover .copy-icon {
  opacity: 1;
  transform: scale(1.08);
}

.copyable-text.is-copied .copy-icon {
  opacity: 1;
  color: var(--success, #16855b);
}
</style>
