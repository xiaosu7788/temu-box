<script setup lang="ts">
import { ArrowRight, Box, Clock, PriceTag, SetUp, UserFilled } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'

const router = useRouter()

function open(path: string) {
  router.push(path)
}
</script>

<template>
  <div class="admin-page admin-home">
    <section class="section-band admin-intro">
      <div class="section-heading">
        <div><h2>后台管理</h2><p>集中管理用户账号和业务计算规则，后续功能可以继续扩展到这里。</p></div>
      </div>
      <div class="admin-module-grid">
        <button class="admin-module-entry" type="button" @click="open('/admin/users')">
          <span class="admin-module-icon"><el-icon><UserFilled /></el-icon></span>
          <span class="admin-module-copy"><strong>用户管理</strong><small>审核、编辑和删除普通用户账号</small></span>
          <el-icon class="admin-module-arrow"><ArrowRight /></el-icon>
        </button>
        <button class="admin-module-entry" type="button" @click="open('/admin/settings')">
          <span class="admin-module-icon"><el-icon><SetUp /></el-icon></span>
          <span class="admin-module-copy"><strong>成本参数</strong><small>配置订单计算和批量报名活动的规则</small></span>
          <el-icon class="admin-module-arrow"><ArrowRight /></el-icon>
        </button>
        <button class="admin-module-entry" type="button" @click="open('/admin/activity-settings')">
          <span class="admin-module-icon"><el-icon><PriceTag /></el-icon></span>
          <span class="admin-module-copy"><strong>批量报活动设置</strong><small>配置默认SKC格式和识别规则</small></span>
          <el-icon class="admin-module-arrow"><ArrowRight /></el-icon>
        </button>
        <button class="admin-module-entry" type="button" @click="open('/admin/inventory')">
          <span class="admin-module-icon"><el-icon><Box /></el-icon></span>
          <span class="admin-module-copy"><strong>库存管理</strong><small>更新库存表、重建缓存和删除库存明细</small></span>
          <el-icon class="admin-module-arrow"><ArrowRight /></el-icon>
        </button>
        <button class="admin-module-entry" type="button" @click="open('/admin/tasks')">
          <span class="admin-module-icon"><el-icon><Clock /></el-icon></span>
          <span class="admin-module-copy"><strong>任务记录</strong><small>查看所有用户的订单和报名活动任务</small></span>
          <el-icon class="admin-module-arrow"><ArrowRight /></el-icon>
        </button>
      </div>
    </section>
  </div>
</template>
