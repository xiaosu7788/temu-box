import { ElMessage, ElMessageBox } from 'element-plus'
import { errorMessage } from './api'

const messageOptions = {
  duration: 3500,
  grouping: true,
  offset: 20,
  showClose: true,
} as const

export function notifySuccess(message: string) {
  ElMessage({ ...messageOptions, message, type: 'success' })
}

export function notifyWarning(message: string) {
  ElMessage({ ...messageOptions, message, type: 'warning' })
}

export function notifyError(error: unknown) {
  ElMessage({ ...messageOptions, duration: 5000, message: errorMessage(error), type: 'error' })
}

export async function confirmAction(message: string, title = '确认操作'): Promise<boolean> {
  try {
    await ElMessageBox.confirm(message, title, {
      type: 'warning',
      confirmButtonText: '确认',
      cancelButtonText: '取消',
      closeOnClickModal: false,
      closeOnPressEscape: true,
      distinguishCancelAndClose: true,
      autofocus: false,
    })
    return true
  } catch {
    return false
  }
}
