import { createRouter, createWebHistory } from 'vue-router'

const OrderWorkspace = () => import('./views/OrderWorkspace.vue')
const InventoryManage = () => import('./views/InventoryManage.vue')
const BulkActivity = () => import('./views/BulkActivity.vue')
const TaskHistory = () => import('./views/TaskHistory.vue')

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/orders' },
    { path: '/orders', component: OrderWorkspace, meta: { title: '订单计算' } },
    { path: '/sku', redirect: '/inventory' },
    { path: '/inventory', component: InventoryManage, meta: { title: '库存管理' } },
    { path: '/half-headcost', redirect: '/inventory?tab=half-headcost' },
    { path: '/activities', component: BulkActivity, meta: { title: '批量报名活动' } },
    { path: '/tasks', component: TaskHistory, meta: { title: '任务记录' } },
  ],
})
