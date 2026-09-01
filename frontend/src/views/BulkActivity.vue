<script setup lang="ts">
import { computed, ref } from 'vue'
import { Download, RefreshRight, UploadFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import type { UploadFile, UploadFiles, UploadUserFile } from 'element-plus'
import { activityDownloadUrl, errorMessage, processBulkActivity } from '../api'
import type { BulkActivityResult } from '../types'

const files = ref<UploadUserFile[]>([])
const result = ref<BulkActivityResult | null>(null)
const loading = ref(false)
const canSubmit = computed(() => files.value.length === 1 && !!files.value[0]?.raw)

function fileChanged(_file: UploadFile, uploadFiles: UploadFiles) {
  files.value = uploadFiles.slice(-1)
  result.value = null
}

async function submit() {
  const file = files.value[0]?.raw
  if (!file) return
  loading.value = true
  try {
    result.value = await processBulkActivity(file)
    ElMessage.success('批量报名活动处理完成')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function reset() {
  files.value = []
  result.value = null
}
</script>

<template>
  <section class="section-band activity-upload-panel">
    <div class="section-heading">
      <div>
        <h2>上传报名商品表</h2>
        <p>根据 SKC货号计算活动申报价格，并过滤参考价低于活动价的商品</p>
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

    <div class="action-row left">
      <el-button type="primary" :loading="loading" :disabled="!canSubmit" @click="submit">开始处理</el-button>
    </div>
  </section>

  <section v-if="result" class="section-band activity-result-panel">
    <div class="section-heading">
      <div><h2>处理结果</h2><p>{{ result.filename }}</p></div>
      <el-button type="success" :icon="Download" tag="a" :href="activityDownloadUrl(result.job_id)">下载处理结果</el-button>
    </div>
    <div class="metric-strip activity-metrics">
      <div><span>处理行数</span><strong>{{ result.stats.processed_rows }}</strong></div>
      <div><span>替换价格</span><strong>{{ result.stats.updated_rows }}</strong></div>
      <div><span>保留参考价</span><strong>{{ result.stats.unchanged_rows }}</strong></div>
      <div><span>删除行数</span><strong>{{ result.stats.removed_rows }}</strong></div>
      <div><span>跳过行数</span><strong>{{ result.stats.skipped_rows }}</strong></div>
    </div>
    <p class="activity-summary">工作表：{{ result.stats.sheet }}，表头第 {{ result.stats.header_row }} 行。原表其他内容保持不变。</p>
  </section>
</template>
