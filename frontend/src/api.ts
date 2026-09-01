import axios from 'axios'
import type { BulkActivityResult, HalfHeadcostItem, InventoryStatus, SkuResult, TaskItem } from './types'

const http = axios.create({
  baseURL: '/api',
  timeout: 30 * 60 * 1000,
})

export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    return error.response?.data?.detail || error.message
  }
  return error instanceof Error ? error.message : '请求失败'
}

export async function getStatus() {
  const { data } = await http.get<{
    version: string
    inventory: InventoryStatus
    half_headcost_count: number
    tasks: TaskItem[]
  }>('/status')
  return data
}

export async function createTask(form: FormData) {
  const { data } = await http.post<TaskItem>('/tasks', form)
  return data
}

export async function getTask(id: string) {
  const { data } = await http.get<TaskItem>(`/tasks/${id}`)
  return data
}

export async function getTasks(limit = 50) {
  const { data } = await http.get<{ items: TaskItem[] }>('/tasks', { params: { limit } })
  return data.items
}

export function downloadUrl(id: string) {
  return `/api/tasks/${id}/download`
}

export async function querySkus(skus: string[]) {
  const { data } = await http.post<{ total: number; found: number; items: SkuResult[] }>(
    '/skus/query',
    { skus },
  )
  return data
}

export async function getInventory() {
  const { data } = await http.get<InventoryStatus>('/inventory')
  return data
}

export async function getInventoryItems(query = '', page = 1, pageSize = 30) {
  const { data } = await http.get<{ total: number; items: SkuResult[] }>('/inventory/items', {
    params: { query, page, page_size: pageSize },
  })
  return data
}

export async function uploadInventory(file: File) {
  const form = new FormData()
  form.append('file', file)
  const { data } = await http.post<InventoryStatus & { message: string }>('/inventory', form)
  return data
}

export async function rebuildInventory() {
  const { data } = await http.post<InventoryStatus & { message: string }>('/inventory/rebuild')
  return data
}

export async function getHalfHeadcost(query = '', page = 1, pageSize = 30) {
  const { data } = await http.get<{ total: number; items: HalfHeadcostItem[] }>('/half-headcost', {
    params: { query, page, page_size: pageSize },
  })
  return data
}

export async function importHalfHeadcost(file: File) {
  const form = new FormData()
  form.append('file', file)
  const { data } = await http.post<{ message: string; incoming: number; added: number; total: number }>(
    '/half-headcost/import',
    form,
  )
  return data
}

export async function deleteHalfHeadcost(sku: string) {
  await http.delete(`/half-headcost/${encodeURIComponent(sku)}`)
}

export async function processBulkActivity(file: File) {
  const form = new FormData()
  form.append('file', file)
  const { data } = await http.post<BulkActivityResult>('/activities/bulk', form)
  return data
}

export function activityDownloadUrl(jobId: string) {
  return `/api/activities/${encodeURIComponent(jobId)}/download`
}
