<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'

// 数值变化时的平滑滚动动画（ease-out cubic）
const props = withDefaults(defineProps<{
  value: number
  decimals?: number
  duration?: number
}>(), { decimals: 0, duration: 700 })

const display = ref(props.value)
let raf = 0

watch(() => props.value, (target) => {
  cancelAnimationFrame(raf)
  const start = display.value
  const delta = target - start
  if (Math.abs(delta) < 1e-9) {
    display.value = target
    return
  }
  const startTime = performance.now()
  const step = (now: number) => {
    const t = Math.min(1, (now - startTime) / props.duration)
    display.value = start + delta * (1 - Math.pow(1 - t, 3))
    if (t < 1) raf = requestAnimationFrame(step)
  }
  raf = requestAnimationFrame(step)
})

onBeforeUnmount(() => cancelAnimationFrame(raf))
</script>

<template>
  <span class="animated-number">{{ display.toFixed(decimals) }}</span>
</template>
