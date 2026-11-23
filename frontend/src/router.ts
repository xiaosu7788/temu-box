import { createRouter, createWebHistory } from 'vue-router'
import { canVisit, firstAllowedPath } from './permissions'

const OrderWorkspace = () => import('./views/OrderWorkspace.vue')
const InventoryManage = () => import('./views/InventoryManage.vue')
const BulkActivity = () => import('./views/BulkActivity.vue')
const StoreConsole = () => import('./views/StoreConsole.vue')
const TaskHistory = () => import('./views/TaskHistory.vue')
const AdminUsers = () => import('./views/AdminUsers.vue')
const AdminRegions = () => import('./views/AdminRegions.vue')
const AdminCategories = () => import('./views/AdminCategories.vue')
const AdminSettings = () => import('./views/AdminSettings.vue')
const AdminActivitySettings = () => import('./views/AdminActivitySettings.vue')
const AdminInventory = () => import('./views/AdminInventory.vue')
const AdminTasks = () => import('./views/AdminTasks.vue')
const AdminStoreCenter = () => import('./views/AdminStoreCenter.vue')
const AdminStoreUsers = () => import('./views/AdminStoreUsers.vue')
const AdminStoreLogs = () => import('./views/AdminStoreLogs.vue')
const AdminKefuActions = () => import('./views/AdminKefuActions.vue')
const AdminSystemSettings = () => import('./views/AdminSystemSettings.vue')
const AdminAuditLogs = () => import('./views/AdminAuditLogs.vue')
const AdminMonitoring = () => import('./views/AdminMonitoring.vue')

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/orders' },
    { path: '/orders', component: OrderWorkspace, meta: { title: '订单计算' } },
    { path: '/sku', redirect: '/inventory' },
    { path: '/inventory', component: InventoryManage, meta: { title: '库存管理' } },
    { path: '/half-headcost', redirect: '/inventory?tab=half-headcost' },
    { path: '/weight-headcost', redirect: '/inventory?tab=weight-headcost' },
    { path: '/activities', component: BulkActivity, meta: { title: '批量报名活动' } },
    // 店铺管理：模块准入由管理员按用户开通（store_access），页面内二次校验。
    // 子路由对应左侧「我的店铺」展开后的三个菜单。
    {
      path: '/store',
      component: StoreConsole,
      redirect: '/store/goods',
      meta: { title: '我的店铺', requiresStore: true },
      children: [
        { path: 'goods', component: () => import('./components/StoreGoodsPanel.vue'), meta: { title: '商品查询' } },
        { path: 'create', component: () => import('./components/StoreProductCreate.vue'), meta: { title: '创建商品' } },
        { path: 'kefu', component: () => import('./components/StoreKefuPanel.vue'), meta: { title: '客服自助' } },
        { path: 'actions', component: () => import('./components/StoreKefuActionsPanel.vue'), meta: { title: '操作记录' } },
      ],
    },
    { path: '/tasks', component: TaskHistory, meta: { title: '任务记录' } },
    { path: '/admin', redirect: '/admin/monitoring' },
    { path: '/admin/users', component: AdminUsers, meta: { title: '用户管理' } },
    { path: '/admin/regions', component: AdminRegions, meta: { title: '区域设置' } },
    { path: '/admin/categories', component: AdminCategories, meta: { title: '品类管理' } },
    { path: '/admin/settings', component: AdminSettings, meta: { title: '成本参数' } },
    { path: '/admin/activity-settings', component: AdminActivitySettings, meta: { title: '批量报活动设置' } },
    { path: '/admin/inventory', component: AdminInventory, meta: { title: '库存管理' } },
    { path: '/admin/tasks', component: AdminTasks, meta: { title: '任务记录' } },
    { path: '/admin/store-users', component: AdminStoreUsers, meta: { title: '用户管理' } },
    { path: '/admin/store-sessions', redirect: '/admin/store-users' },
    { path: '/admin/store-center', component: AdminStoreCenter, meta: { title: '店铺日志与审计' } },
    { path: '/admin/store-logs', redirect: '/admin/store-center?tab=logs' },
    { path: '/admin/kefu-actions', redirect: '/admin/store-center?tab=kefu' },
    { path: '/admin/system', component: AdminSystemSettings, meta: { title: '系统设置' } },
    { path: '/admin/audit-logs', redirect: '/admin/store-center?tab=audit' },
    { path: '/admin/monitoring', component: AdminMonitoring, meta: { title: '系统监控' } },
  ],
})
