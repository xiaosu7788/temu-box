<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Brush, Delete, Refresh, Setting } from '@element-plus/icons-vue'
import { getAdminSystemSettings, runAdminCleanup, runAdminPurge, saveAdminSystemSettings } from '../api'
import { confirmAction, notifyError, notifySuccess } from '../feedback'
import type { CleanupResult, SystemSettings, TaskPoolStats } from '../types'

const loading = ref(false)
const saving = ref(false)
const cleaning = ref(false)
const purging = ref(false)
const pools = ref<{ orders: TaskPoolStats; activities: TaskPoolStats } | null>(null)
const cleanupInfo = ref<{ interval_seconds: number; last_run_at: string | null; last_result: CleanupResult | null } | null>(null)

const form = reactive<SystemSettings>({
  task_workers: 2,
  task_queue_limit: 50,
  activity_workers: 2,
  activity_queue_limit: 50,
  cleanup_enabled: true,
  cleanup_retention_days: 30,
  audit_retention_days: 180,
})

async function load() {
  loading.value = true
  try {
    const info = await getAdminSystemSettings()
    Object.assign(form, info.settings)
    pools.value = info.pools
    cleanupInfo.value = info.cleanup
  } catch (error) {
    notifyError(error)
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    const result = await saveAdminSystemSettings({ ...form })
    pools.value = result.pools
    notifySuccess('系统设置已保存，并发与队列限制已实时生效')
  } catch (error) {
    notifyError(error)
  } finally {
    saving.value = false
  }
}

async function cleanupNow() {
  if (!await confirmAction('立即执行一次清理？将删除超过保留期限的任务文件和审计日志。', '手动清理')) return
  cleaning.value = true
  try {
    const result = await runAdminCleanup()
    if (result.skipped) {
      notifySuccess('自动清理当前已关闭，未执行清理')
    } else {
      notifySuccess(`清理完成：删除 ${result.removed_task_dirs} 个订单任务、${result.removed_activity_dirs} 个活动任务、${result.pruned_audit_logs} 条过期审计日志`)
    }
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    cleaning.value = false
  }
}

async function purgeNow() {
  if (!await confirmAction('此操作将立即清空全部历史任务记录、活动记录和审计日志（排队/运行中的任务除外），且不受保留期限制，删除后无法恢复。确定继续？', '清空全部历史数据')) return
  purging.value = true
  try {
    const result = await runAdminPurge()
    notifySuccess(`已清空：${result.removed_task_dirs} 个订单任务、${result.removed_activity_dirs} 个活动任务、${result.pruned_audit_logs} 条审计日志`)
    await load()
  } catch (error) {
    notifyError(error)
  } finally {
    purging.value = false
  }
}

function poolLine(stats: TaskPoolStats | undefined) {
  if (!stats) return '-'
  return `并发 ${stats.workers} · 队列上限 ${stats.queue_limit} · 排队+运行中 ${stats.queued} · 执行中 ${stats.active}`
}

onMounted(load)
</script>

<template>
  <div class="admin-page admin-system-page">
    <section class="section-band">
      <div class="section-heading">
        <div class="subpage-title"><div><h2>系统设置</h2><p>后台任务并发、队列限制与自动清理策略</p></div></div>
        <div class="toolbar-row">
          <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
          <el-button :icon="Brush" :loading="cleaning" @click="cleanupNow">立即清理</el-button>
          <el-button type="danger" plain :icon="Delete" :loading="purging" @click="purgeNow">清空全部</el-button>
          <el-button type="primary" :icon="Setting" :loading="saving" @click="save">保存设置</el-button>
        </div>
      </div>

      <el-form v-loading="loading" label-width="160px" class="system-form">
        <h3>后台任务并发与队列</h3>
        <el-form-item label="订单任务并发数">
          <el-input-number v-model="form.task_workers" :min="1" :max="16" />
          <span class="form-hint">同时处理的订单计算任务数</span>
        </el-form-item>
        <el-form-item label="订单任务队列上限">
          <el-input-number v-model="form.task_queue_limit" :min="1" :max="1000" />
          <span class="form-hint">排队+运行中的任务总数上限，超出后拒绝新任务</span>
        </el-form-item>
        <el-form-item label="活动任务并发数">
          <el-input-number v-model="form.activity_workers" :min="1" :max="16" />
          <span class="form-hint">同时处理的批量报名活动任务数</span>
        </el-form-item>
        <el-form-item label="活动任务队列上限">
          <el-input-number v-model="form.activity_queue_limit" :min="1" :max="1000" />
          <span class="form-hint">排队+运行中的活动任务总数上限</span>
        </el-form-item>
        <p class="pool-status">当前订单任务池：{{ poolLine(pools?.orders) }}</p>
        <p class="pool-status">当前活动任务池：{{ poolLine(pools?.activities) }}</p>

        <h3>自动清理</h3>
        <el-form-item label="启用自动清理">
          <el-switch v-model="form.cleanup_enabled" />
          <span class="form-hint">定期删除超过保留期限的任务文件、任务记录和审计日志</span>
        </el-form-item>
        <el-form-item label="文件保留天数">
          <el-input-number v-model="form.cleanup_retention_days" :min="1" :max="3650" />
          <span class="form-hint">订单任务和活动任务的文件与记录保留天数</span>
        </el-form-item>
        <el-form-item label="审计日志保留天数">
          <el-input-number v-model="form.audit_retention_days" :min="7" :max="3650" />
          <span class="form-hint">超过保留期限的审计日志将被删除</span>
        </el-form-item>
        <p class="pool-status" v-if="cleanupInfo">
          检查周期：每 {{ Math.round(cleanupInfo.interval_seconds / 3600) }} 小时 ·
          上次执行：{{ cleanupInfo.last_run_at || '尚未执行' }}
        </p>
      </el-form>
    </section>
  </div>
</template>

<style scoped>
.system-form h3 {
  margin: 26px 0 18px;
  font-size: 15px;
}
.system-form h3:first-child {
  margin-top: 6px;
}
.form-hint {
  margin-left: 12px;
  color: #98a2b3;
  font-size: 12px;
}
.pool-status {
  margin: 4px 0 12px;
  color: #667085;
  font-size: 13px;
}
</style>
