import { createRouter, createWebHistory } from 'vue-router'

const OrderWorkspace = () => import('./views/OrderWorkspace.vue')
const InventoryManage = () => import('./views/InventoryManage.vue')
const BulkActivity = () => import('./views/BulkActivity.vue')
const TaskHistory = () => import('./views/TaskHistory.vue')
const AdminHome = () => import('./views/AdminHome.vue')
const AdminUsers = () => import('./views/AdminUsers.vue')
const AdminSettings = () => import('./views/AdminSettings.vue')
const AdminActivitySettings = () => import('./views/AdminActivitySettings.vue')
const AdminInventory = () => import('./views/AdminInventory.vue')
const AdminTasks = () => import('./views/AdminTasks.vue')

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
    { path: '/admin', component: AdminHome, meta: { title: '后台管理' } },
    { path: '/admin/users', component: AdminUsers, meta: { title: '用户管理' } },
    { path: '/admin/settings', component: AdminSettings, meta: { title: '成本参数' } },
    { path: '/admin/activity-settings', component: AdminActivitySettings, meta: { title: '批量报活动设置' } },
    { path: '/admin/inventory', component: AdminInventory, meta: { title: '库存管理' } },
    { path: '/admin/tasks', component: AdminTasks, meta: { title: '任务记录' } },
  ],
})
