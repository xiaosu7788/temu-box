<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Refresh, Shop, SwitchButton } from '@element-plus/icons-vue'
import { addStoreAccount, deleteStoreAccount, getStoreAccounts, loginStore, logoutStore, refreshStoreSession as probeStoreSession, sendStoreLoginCode } from '../api'
import { confirmAction, notifyError, notifySuccess, notifyWarning } from '../feedback'
import { refreshStoreSession, storeFeatureEnabled, storeSession } from '../storeState'
import type { StoreAccount } from '../types'

const router = useRouter()
const route = useRoute()
const session = storeSession
const submitting = ref(false)
/** 切页自动探测的节流时间戳（switchTo 也会更新它） */
let lastAutoCheck = 0
/** 顶栏目标元素挂载后才存在，Teleport 需等它就绪 */
const topbarReady = ref(false)
const cooldownLeft = ref(0)
let timer: number | undefined

// 默认保存账号密码：管理员可在用户管理里查看（后端密文存储）
const form = reactive({ account: '', password: '', remember: true })

// ==================== 店铺账号库（可保存多个，同一时刻只登录一个） ====================
const accounts = ref<StoreAccount[]>([])
const accountsLoading = ref(false)
const switchVisible = ref(false)
const addingAccount = ref(false)
const accountForm = reactive({ account: '', password: '', label: '' })

const activeAccountMasked = computed(() => session.value?.account_masked || '')

async function loadAccounts() {
  if (!storeFeatureEnabled.value) return
  accountsLoading.value = true
  try {
    accounts.value = await getStoreAccounts()
  } catch {
    accounts.value = []
  } finally {
    accountsLoading.value = false
  }
}

function openSwitch() {
  switchVisible.value = true
  void loadAccounts()
}

async function saveAccount() {
  const account = accountForm.account.trim()
  const password = accountForm.password
  if (!account || !password) {
    notifyWarning('请填写账号和密码')
    return
  }
  addingAccount.value = true
  try {
    await addStoreAccount({ account, password, label: accountForm.label.trim() })
    accountForm.account = ''
    accountForm.password = ''
    accountForm.label = ''
    switchVisible.value = false
    await loadAccounts()
    notifySuccess('已保存到店铺账号库')
  } catch (error) {
    notifyError(error)
  } finally {
    addingAccount.value = false
  }
}

async function removeAccount(item: StoreAccount) {
  const ok = await confirmAction(`删除已保存的店铺「${item.label || item.account_masked}」？`, '删除店铺账号')
  if (!ok) return
  try {
    await deleteStoreAccount(item.id)
    await loadAccounts()
    notifySuccess('已删除')
  } catch (error) {
    notifyError(error)
  }
}

const switchingId = ref<number | null>(null)

async function switchTo(item: StoreAccount) {
  if (switchingId.value !== null) return
  // 只有「会话确实在线且就是这家店」才跳过；失效/未登录时必须允许重新登录
  if (status.value === 'active' && item.account_masked === activeAccountMasked.value) {
    switchVisible.value = false
    notifySuccess('该店铺已在登录状态')
    return
  }
  switchVisible.value = false
  switchingId.value = item.id
  submitting.value = true
  try {
    // 先退出当前店铺（释放 Temu 账号占用），再用库里的凭据登录目标店铺
    if (status.value === 'active') {
      await logoutStore()
      storeSession.value = await probeStoreSession()
    }
    const next = await loginStore({ account_id: item.id, remember: true })
    storeSession.value = next
    lastAutoCheck = Date.now()
    notifySuccess(`已登录「${item.label || item.mall_name || item.account_masked}」`)
    await loadAccounts()
  } catch (error) {
    notifyError(error)
    // 失败后同步一次真实状态，避免界面停在旧的 invalid/active
    try {
      storeSession.value = await probeStoreSession()
    } catch {
      /* 状态同步失败不影响错误提示 */
    }
  } finally {
    switchingId.value = null
    submitting.value = false
  }
}

const rawStatus = computed(() => session.value?.status ?? 'logged_out')
/**
 * 冷却倒计时归零后就不再按 cooldown 处理：
 * 后端状态偶尔会停在 cooldown（过期未复位），否则登录按钮会永久禁用。
 */
const status = computed(() => {
  if (rawStatus.value === 'cooldown' && cooldownLeft.value <= 0) return 'logged_out'
  return rawStatus.value
})
const needsLogin = computed(() => status.value !== 'active')
const cooldownText = computed(() => {
  const total = Math.max(0, cooldownLeft.value)
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return `${minutes} 分 ${String(seconds).padStart(2, '0')} 秒`
})
/** 冷却原因：54001 是图形验证码（30 分钟），RATE_LIMIT 只是登录过密（几秒） */
const cooldownReason = computed(() =>
  String(session.value?.last_error_code || '') === '54001' ? 'captcha' : 'rate',
)

watch(
  () => session.value?.cooldown_seconds,
  (value) => { cooldownLeft.value = Math.max(0, Number(value || 0)) },
  { immediate: true },
)

// 权限被管理员中途收回：立刻退出页面（接口层会返回 403）
watch(storeFeatureEnabled, (enabled) => {
  if (!enabled) {
    notifyWarning('店铺管理模块已被关闭，请联系管理员')
    router.replace('/orders')
  }
})

function syncCooldown() {
  if (cooldownLeft.value > 0) cooldownLeft.value -= 1
}

/* 验证码登录：触发风控（54001）时不用干等 30 分钟 */
const verifyCode = ref('')
const sendingCode = ref(false)
const codeSentAt = ref(0)
const needCode = ref(false)
const codeCountdown = ref(0)
let codeTimer: number | null = null

/** 输入的账号是不是当前会话绑定的那个（脱敏账号只能按域名粗比） */
const isSameAccount = computed(() => {
  const masked = String(session.value?.account_masked || '')
  const input = form.account.trim()
  if (!masked || !input) return false
  const at = masked.indexOf('@')
  if (at > 0) return input.toLowerCase().endsWith(masked.slice(at).toLowerCase())
  const head = masked.replace(/\*/g, '').slice(0, 2)
  return head.length >= 2 ? input.startsWith(head) : false
})

// 换账号：清掉上一个账号的验证码状态
watch(() => form.account, () => {
  needCode.value = false
  verifyCode.value = ''
  codeSentAt.value = 0
  codeCountdown.value = 0
})

const codeButtonText = computed(() =>
  codeCountdown.value > 0 ? `${codeCountdown.value}s 后重发` : codeSentAt.value ? '重新获取' : '获取验证码',
)

function startCodeCountdown() {
  codeCountdown.value = 60
  if (codeTimer !== null) window.clearInterval(codeTimer)
  codeTimer = window.setInterval(() => {
    codeCountdown.value -= 1
    if (codeCountdown.value <= 0 && codeTimer !== null) {
      window.clearInterval(codeTimer)
      codeTimer = null
    }
  }, 1000)
}

onUnmounted(() => {
  if (codeTimer !== null) window.clearInterval(codeTimer)
})

async function requestCode() {
  if (!form.account.trim()) {
    notifyWarning('请先填写邮箱或手机号')
    return
  }
  sendingCode.value = true
  try {
    await sendStoreLoginCode({ account: form.account.trim() })
    codeSentAt.value = Date.now()
    needCode.value = true
    startCodeCountdown()
    notifySuccess('验证码已发送，请查收邮箱或短信')
  } catch (error) {
    notifyError(error)
  } finally {
    sendingCode.value = false
  }
}

async function loadSession() {
  await refreshStoreSession()
}

async function submitLogin() {
  if (submitting.value) return
  if (!form.account.trim() || !form.password) {
    notifyWarning('请填写 Temu 账号和密码')
    return
  }
  submitting.value = true
  try {
    await loginStore({
      account: form.account.trim(),
      password: form.password,
      remember: form.remember,
      verify_code: verifyCode.value.trim() || undefined,
    })
    form.password = ''
    verifyCode.value = ''
    needCode.value = false
    notifySuccess('店铺登录成功')
    await loadSession()
  } catch (error) {
    // 触发风控：直接展开验证码输入并自动发一次码
    const msg = String((error as { message?: string })?.message || '')
    if (/54001|验证码|captcha/i.test(msg)) {
      // 只展开验证码框，发码交给用户手动点（自动发码会消耗平台的发送额度）
      needCode.value = true
    }
    notifyError(error)
    await loadSession()
  } finally {
    submitting.value = false
  }
}

async function doLogout() {
  if (!await confirmAction('退出后需要重新输入 Temu 账号密码才能继续使用店铺功能。', '退出店铺登录')) return
  try {
    await logoutStore()
    notifySuccess('已退出店铺登录')
    await loadSession()
  } catch (error) {
    notifyError(error)
  }
}

async function probe() {
  try {
    await probeStoreSession()
    await loadSession()
    if (status.value === 'active') notifySuccess('会话正常')
    else notifyWarning('会话已失效，请重新登录')
  } catch (error) {
    notifyError(error)
  }
}

onMounted(async () => {
  // 顶栏目标在 App.vue 里，挂载完成后才可 Teleport
  topbarReady.value = true
  if (!storeFeatureEnabled.value) {
    notifyWarning('未开通店铺管理模块，请联系管理员')
    router.replace('/orders')
    return
  }
  await loadSession()
  // 账号库独立于会话状态：未登录时也要列出已保存的店铺供一键登录
  void loadAccounts()
  timer = window.setInterval(syncCooldown, 1000)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <div class="store-page">
    <!-- 店铺状态条：Teleport 到页面顶栏（App.vue 的 #store-topbar-target） -->
    <Teleport v-if="topbarReady && status === 'active' && session" to="#store-topbar-target">
    <div class="store-bar">
      <div class="store-bar__main">
        <el-icon class="store-bar__icon"><Shop /></el-icon>
        <strong>{{ session?.mall_name || '已登录店铺' }}</strong>
        <span class="store-bar__account">{{ session?.account_masked }}</span>
        <el-tag type="success" effect="light" size="small">在线</el-tag>
        <span class="store-bar__expire">会话剩余约 {{ Math.round((session?.expires_in_seconds || 0) / 3600) }} 小时</span>
        <span v-if="session && session.writes_enabled === false" class="store-bar__expire">· 写操作已暂停</span>
      </div>
      <div class="store-bar__actions">
        <el-button :icon="Refresh" size="small" @click="probe">检测会话</el-button>
        <el-button size="small" @click="openSwitch">切换店铺</el-button>
        <el-button :icon="SwitchButton" size="small" type="danger" plain @click="doLogout">退出登录</el-button>
      </div>
    </div>
    </Teleport>

    <el-alert
      v-if="session && !session.bridge_configured"
      title="店铺管理服务未配置"
      type="warning"
      :closable="false"
      show-icon
      description="请联系管理员设置 BRIDGE_TOKEN 并启动 bridge 容器。"
    />

    <div v-else-if="!session" class="store-loading">
      <el-skeleton :rows="4" animated />
    </div>

    <!-- 冷却中：图形验证码 / 登录过密 -->
    <el-alert
      v-else-if="status === 'cooldown'"
      type="warning"
      :closable="false"
      show-icon
      :title="cooldownReason === 'captcha'
        ? `需要图形验证码，请在 ${cooldownText} 后重试`
        : `登录请求过于频繁，请在 ${cooldownText} 后重试`"
      :description="cooldownReason === 'captcha'
        ? 'Temu 侧的登录频率限制（54001）。冷却期内重复尝试会刷新冷却时间，请耐心等待。'
        : 'Temu 侧要求两次登录间隔几秒。稍等片刻直接重试即可，不需要等太久。'"
    />

    <!-- 会话失效：不自动重登 -->
    <el-alert
      v-else-if="status === 'invalid'"
      type="error"
      :closable="false"
      show-icon
      title="店铺会话已失效"
      :description="session.last_error || '账号可能在其他地方登录。请重新登录后再使用店铺功能。'"
    />

    <!-- 未登录 / 登录中 / 失效后的登录卡 -->
    <div v-if="needsLogin" class="store-login">
      <el-card shadow="never" class="store-login__card">
        <div class="store-login__head">
          <el-icon class="store-login__icon"><Shop /></el-icon>
          <div>
            <h2>登录 Temu 店铺</h2>
            <p>用自己的卖家账号登录，服务端只保留会话，不保存密码（除非勾选记住）。</p>
          </div>
        </div>

        <!-- 「会话已失效」上方横幅已经提示过，这里只说冷却剩余时间 -->
        <el-alert
          v-if="status === 'cooldown'"
          type="warning"
          :closable="false"
          class="store-login__notice"
          :title="`需等待 ${cooldownText}`"
        />

        <div v-if="accounts.length" class="store-login__accounts">
          <div class="store-login__accounts-title">已保存的店铺（{{ accounts.length }}）</div>
          <div v-for="item in accounts" :key="item.id" class="store-account">
            <el-icon class="store-account__icon"><Shop /></el-icon>
            <span class="store-account__body">
              <span class="store-account__name">{{ item.label || item.mall_name || item.account_masked }}</span>
              <span class="store-account__sub">{{ item.account_masked }}</span>
            </span>
            <el-button
              link
              type="primary"
              size="small"
              :loading="switchingId === item.id"
              :disabled="(submitting && switchingId !== item.id) || status === 'cooldown'"
              @click="switchTo(item)"
            >{{ switchingId === item.id ? '登录中' : '登录' }}</el-button>
            <el-button link type="danger" size="small" @click="removeAccount(item)">删除</el-button>
          </div>
        </div>

        <el-form label-position="top" @submit.prevent="submitLogin">
          <el-form-item label="Temu 账号">
            <el-input v-model="form.account" placeholder="邮箱或手机号" autocomplete="username" maxlength="160" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="form.password"
              type="password"
              show-password
              placeholder="Temu 登录密码"
              autocomplete="current-password"
              maxlength="128"
              @keyup.enter="submitLogin"
            />
          </el-form-item>
          <!-- 触发风控（54001）时平台要求验证码；提前填好就不用干等 30 分钟 -->
          <el-form-item v-if="needCode" label="验证码">
            <div class="store-login__coderow">
              <el-input
                v-model="verifyCode"
                placeholder="邮箱 / 短信收到的验证码"
                maxlength="8"
                @keyup.enter="submitLogin"
              />
              <el-button :loading="sendingCode" :disabled="codeCountdown > 0" @click="requestCode">
                {{ codeButtonText }}
              </el-button>
            </div>
          </el-form-item>
          <div class="store-login__switch">
            <el-switch v-model="form.remember" :disabled="!session?.remember_available" />
            <span>记住密码（会话过期可一键重登）</span>
          </div>
          <p v-if="session && !session.remember_available" class="store-login__hint">
            服务端未配置 CREDENTIAL_KEY，暂不支持记住密码。
          </p>
          <el-button
            type="primary"
            class="store-login__submit"
            :loading="submitting"
            :disabled="status === 'cooldown'"
            @click="submitLogin"
          >
            {{ isSameAccount ? (status === 'invalid' ? '重新登录' : '登录') : '登录' }}
          </el-button>
        </el-form>

        <p class="store-login__tip">
          提示：同一 Temu 账号在浏览器登录会让这里的会话掉线；一个账号同一时间只能在一处使用。
        </p>
      </el-card>
    </div>

    <!-- 已登录 -->
    <template v-else>
      <router-view />
    </template>
  </div>
    <el-dialog v-model="switchVisible" title="店铺账号库" width="min(560px, calc(100vw - 32px))">
      <div v-loading="accountsLoading">
        <el-table :data="accounts" size="small" empty-text="还没有保存的店铺账号">
          <el-table-column label="店铺" min-width="180">
            <template #default="scope">
              <div class="acct-main">
                <span>{{ scope.row.label || scope.row.mall_name || scope.row.account_masked }}</span>
                <el-tag v-if="scope.row.account_masked === activeAccountMasked" size="small" type="success">当前</el-tag>
              </div>
              <div class="acct-sub">{{ scope.row.account_masked }}</div>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="150" align="right">
            <template #default="scope">
              <el-button
                link
                type="primary"
                size="small"
                :disabled="scope.row.account_masked === activeAccountMasked || submitting"
                @click="switchTo(scope.row)"
              >切换</el-button>
              <el-button link type="danger" size="small" @click="removeAccount(scope.row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <el-divider>添加店铺账号</el-divider>
      <el-form label-width="70px" @submit.prevent>
        <el-form-item label="账号">
          <el-input v-model="accountForm.account" placeholder="手机号或邮箱" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="accountForm.password" type="password" show-password placeholder="店铺密码" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="accountForm.label" placeholder="选填，如「美国主店」" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="switchVisible = false">关闭</el-button>
        <el-button type="primary" :loading="addingAccount" @click="saveAccount">保存账号</el-button>
      </template>
    </el-dialog>

</template>

<style scoped>
.store-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 100%;
  max-width: none;
}

.store-loading {
  padding: 24px;
}

.store-login {
  display: flex;
  justify-content: center;
  padding: 24px 0;
}

.store-login__card {
  width: min(460px, 100%);
  border-radius: 16px;
}

.store-login__head {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  margin-bottom: 16px;
}

.store-login__icon {
  font-size: 28px;
  color: #2563eb;
  margin-top: 4px;
}

.store-login__head h2 {
  margin: 0 0 6px;
  font-size: 18px;
}

.store-login__head p {
  margin: 0;
  color: #6b7280;
  font-size: 13px;
  line-height: 1.5;
}

.store-login__notice {
  margin-bottom: 12px;
}


.store-login__submit {
  width: 100%;
  margin-top: 16px;
}

.store-login__switch {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #374151;
  margin-top: 4px;
}

.store-login__hint {
  margin: 6px 0 0;
  color: #b45309;
  font-size: 12px;
}

.store-login__tip {
  margin: 14px 0 0;
  color: #9ca3af;
  font-size: 12px;
  line-height: 1.6;
}

.acct-main {
  display: flex;
  align-items: center;
  gap: 6px;
}

.acct-sub {
  color: var(--muted, #6b7280);
  font-size: 12px;
}

/* 登录卡里的「已保存的店铺」列表 */
.store-login__accounts {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 12px;
}

.store-login__accounts-title {
  color: var(--muted, #6b7280);
  font-size: 12px;
}

.store-account {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 6px 12px;
  background: var(--surface-subtle, #f8fafc);
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 8px;
}

.store-account__icon {
  font-size: 16px;
  color: #2563eb;
}

.store-account__body {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.store-account__name {
  font-size: 13px;
  color: var(--text, #111827);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.store-account__sub {
  color: var(--muted, #6b7280);
  font-size: 12px;
}

.store-account .el-button {
  margin-left: 0;
}


.store-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 28px;
  font-size: 12px;
  color: var(--muted, #6b7280);
}

.store-bar__main {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.store-bar__icon {
  font-size: 15px;
  color: #2563eb;
}

.store-bar__account {
  margin-left: 8px;
  color: #6b7280;
  font-size: 13px;
}

.store-bar__expire {
  color: #9ca3af;
  font-size: 12px;
}

.store-bar__actions {
  display: flex;
  gap: 8px;
}

.store-tabs {
  padding: 0 4px;
}
.store-login__coderow {
  display: flex;
  gap: 8px;
  width: 100%;
}

.store-login__codelink {
  margin: -4px 0 8px;
  text-align: right;
}
</style>
