<script setup lang="ts">
import { ref } from 'vue'
import { Collection, Lock, User as UserIcon } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { errorMessage, login, register } from '../api'
import type { User } from '../types'

const emit = defineEmits<{ authenticated: [user: User] }>()
const registerMode = ref(false)
const username = ref('')
const password = ref('')
const displayName = ref('')
const loading = ref(false)
const formError = ref('')

function clearFormError() {
  formError.value = ''
}

async function submit() {
  formError.value = ''
  if (!username.value.trim()) {
    formError.value = '请输入用户名'
    return
  }
  if (!password.value) {
    formError.value = '请输入密码'
    return
  }
  if (username.value.trim().length < 3 || password.value.length < 8) {
    formError.value = registerMode.value ? '用户名至少 3 位，密码至少 8 位' : '密码至少 8 位'
    return
  }
  loading.value = true
  try {
    if (registerMode.value) {
      await register(username.value, password.value, displayName.value)
      ElMessage.success('注册成功，请等待管理员审核')
      registerMode.value = false
      password.value = ''
    } else {
      emit('authenticated', await login(username.value, password.value))
    }
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="auth-shell">
    <section class="auth-panel">
      <div class="auth-brand"><span class="brand-mark"><el-icon><Collection /></el-icon></span><div><strong>销售成本工具</strong><small>Operations Console</small></div></div>
      <div class="auth-heading"><h1>{{ registerMode ? '创建账号' : '欢迎回来' }}</h1><p>{{ registerMode ? '注册后由管理员审核账号' : '登录后进入业务工作台' }}</p></div>
      <el-form @submit.prevent="submit">
        <el-form-item><el-input v-model="username" placeholder="用户名" size="large" :prefix-icon="UserIcon" autocomplete="username" @input="clearFormError" /></el-form-item>
        <el-form-item v-if="registerMode"><el-input v-model="displayName" placeholder="显示名称（可选）" size="large" /></el-form-item>
        <el-form-item><el-input v-model="password" type="password" show-password placeholder="密码" size="large" :prefix-icon="Lock" autocomplete="current-password" @input="clearFormError" @keyup.enter="submit" /></el-form-item>
        <p v-if="formError" class="auth-form-error" role="alert">{{ formError }}</p>
        <el-button class="auth-submit" type="primary" size="large" :loading="loading" @click="submit">{{ registerMode ? '提交注册' : '登录' }}</el-button>
      </el-form>
      <button class="auth-switch" type="button" @click="registerMode = !registerMode">{{ registerMode ? '已有账号，返回登录' : '注册新账号' }}</button>
    </section>
  </main>
</template>
