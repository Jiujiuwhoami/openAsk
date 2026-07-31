# admin-panel 多租户改造清单

> 项目：OpenAsk Admin Panel（管理后台）  
> 技术栈：Vue 3 + Vite + Element Plus + Vue Router + Pinia  
> 部署：Docker nginx → openAsk:8000  
> 代码路径：`~/admin-panel/src/`  
> 最后更新：2026-07-31

---

## 一、总体架构

### 1.1 改造原则

1. **不破坏现有功能** — 保留当前单租户的所有操作
2. **Key 驱动** — 前端请求带 `X-API-Key`，后端按 key 识别租户
3. **租户可切换** — 同一管理后台可切换管理不同租户
4. **租户管理** — 新增租户创建/编辑/删除/统计页面

### 1.2 现有架构

```
src/
├── api/
│   ├── request.ts          ← Axios 实例，已支持 X-API-Key（读 localStorage）
│   ├── types.ts            ← 类型定义
│   ├── health.ts           ← 健康检查
│   ├── knowledge.ts        ← 知识库 CRUD
│   └── chat.ts             ← 问答/搜索（含 SSE 流式）
├── stores/
│   ├── app.ts              ← 全局状态（已有 apiKey / backendUrl / health）
│   └── knowledge.ts        ← 知识库状态
├── views/
│   ├── dashboard/          ← 数据看板
│   ├── knowledge/          ← 知识库管理（list/create/edit/upload）
│   ├── logs/               ← 问答日志
│   ├── search/             ← 搜索测试
│   └── settings/           ← 系统配置
├── components/
├── layouts/
│   └── MainLayout.vue      ← 主布局（侧边栏 + 顶部）
├── router.ts               ← 路由配置
└── main.ts
```

**已有基础：**
- `api/request.ts` 已有 `X-API-Key` 拦截器（读 `localStorage.getItem('openask_api_key')`）
- `stores/app.ts` 已有 `apiKey` / `backendUrl` 管理
- `views/settings/SettingsView.vue` 已有配置页面

---

## 二、P0 — 租户上下文与鉴权（1-2 天）

### 2.1 改造 stores/app.ts — 加租户状态

**现有代码：**
```typescript
export const useAppStore = defineStore('app', () => {
  const apiKey = ref(localStorage.getItem('openask_api_key') || '')
  const backendUrl = ref(localStorage.getItem('openask_backend_url') || '')
  const healthInfo = ref<HealthResponse | null>(null)
  // ...
})
```

**改造后：**
```typescript
interface Tenant {
  id: string
  name: string
  status: 'active' | 'suspended' | 'trial'
  apiKey: string
  createdAt: number
  documentCount?: number
}

interface TenantState {
  current: Tenant | null
  tenants: Tenant[]
  selecting: boolean   // 是否正在显示租户选择
}

export const useAppStore = defineStore('app', () => {
  // 保留原有
  const apiKey = ref('')
  const backendUrl = ref('')
  const healthInfo = ref<HealthResponse | null>(null)

  // 新增：租户状态
  const tenant = ref<TenantState>({
    current: JSON.parse(localStorage.getItem('openask_tenant') || 'null'),
    tenants: JSON.parse(localStorage.getItem('openask_tenants') || '[]'),
    selecting: false,
  })

  // 动作
  function setApiKey(key: string) { ... }
  function setCurrentTenant(t: Tenant) {
    tenant.value.current = t
    apiKey.value = t.apiKey
    localStorage.setItem('openask_api_key', key)
    localStorage.setItem('openask_tenant', JSON.stringify(t))
    // 刷新健康检查（会返回当前租户文档数）
    checkConnection()
  }
  async function loadTenants(): Promise<void> { ... }
  function openTenantSelector() { tenant.value.selecting = true }
  function closeTenantSelector() { tenant.value.selecting = false }
  function switchTenant(id: string) { ... }
})
```

**涉及文件：**
- `src/stores/app.ts` — 改造，加租户状态
- `src/api/types.ts` — 新增类型定义

### 2.2 新增类型 — src/api/types.ts

```typescript
/** 租户 */
export interface Tenant {
  id: string
  name: string
  status: 'active' | 'suspended' | 'trial'
  apiKey: string
  createdAt: number
  updatedAt: number
  documentCount?: number
}

/** 创建租户请求 */
export interface CreateTenantRequest {
  name: string
  llmApiKey?: string
  llmApiBase?: string
  llmModel?: string
  llmTimeout?: number
  rateLimitPerUser?: string
  rateLimitGlobal?: string
  systemPrompt?: string
}

/** 更新租户请求 */
export interface UpdateTenantRequest extends Partial<CreateTenantRequest> {}

/** 租户 Key 响应 */
export interface TenantKeyResponse {
  apiKey: string
}

/** 租户统计 */
export interface TenantStats {
  tenantId: string
  documentCount: number
  totalCalls: number
  promptTokens: number
  completionTokens: number
  cacheHitRate: number
  createdAt: number
  lastRequest: number
}
```

### 2.3 新建 src/api/tenant.ts — 租户 API

```typescript
import request from './request'
import type { Tenant, CreateTenantRequest, UpdateTenantRequest, TenantKeyResponse, TenantStats } from './types'

export const tenantApi = {
  // 租户列表
  list: () => request.get<{ data: { items: Tenant[], total: number } }>('/api/admin/tenants').then(r => r.data),
  // 创建租户
  create: (data: CreateTenantRequest) => request.post<{ data: Tenant }>('/api/admin/tenants', data).then(r => r.data),
  // 获取租户详情
  get: (id: string) => request.get<{ data: Tenant }>(`/api/admin/tenants/${id}`).then(r => r.data),
  // 更新租户
  update: (id: string, data: UpdateTenantRequest) => request.put<{ data: Tenant }>(`/api/admin/tenants/${id}`, data).then(r => r.data),
  // 删除租户
  delete: (id: string) => request.delete<{ data: { success: boolean; message: string } }>(`/api/admin/tenants/${id}`).then(r => r.data),
  // 轮换 Key
  rotateKey: (id: string) => request.post<{ data: TenantKeyResponse }>(`/api/admin/tenants/${id}/rotate-key`).then(r => r.data),
  // 统计
  stats: (id: string) => request.get<{ data: TenantStats }>(`/api/admin/tenants/${id}/stats`).then(r => r.data),
}
```

### 2.4 路由守卫 — router.ts

```typescript
// 现有 router 改造：
router.beforeEach(async (to, _from, next) => {
  NProgress.start()
  const appStore = useAppStore()

  // 无需鉴权的页面
  if (to.name === 'TenantSelect' || to.name === 'TenantList') {
    next()
    return
  }

  // 无当前租户 → 跳转选择页
  if (!appStore.tenant.current) {
    // 尝试加载已保存的租户列表
    if (appStore.tenant.tenants.length === 0) {
      try { await appStore.loadTenants() } catch { /* 加载失败 */ }
    }
    next({ name: 'TenantSelect' })
    return
  }

  // 检查 API Key 是否有效（调用 health 端点）
  try {
    await appStore.checkConnection()
    next()
  } catch {
    // 401 → Key 无效，清除状态
    appStore.setApiKey('')
    next({ name: 'TenantSelect' })
  }
})

router.afterEach(() => {
  NProgress.done()
})
```

### 2.5 新建 views/auth/TenantSelect.vue — 租户选择页

```vue
<template>
  <div class="tenant-select-page">
    <div class="header">
      <h1>选择管理租户</h1>
      <p>选择要管理的电商站点</p>
    </div>

    <el-table :data="tenants" highlight-current-row class="tenant-table">
      <el-table-column prop="name" label="站点名称" width="300" />
      <el-table-column prop="status" label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === 'active' ? 'success' : 'danger'" size="small">
            {{ row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="documentCount" label="文档数" width="100" />
      <el-table-column prop="createdAt" label="创建时间" width="180" />
      <el-table-column label="操作">
        <template #default="{ row }">
          <el-button type="primary" @click="select(row)">管理</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="tenants.length === 0" description="暂无租户，请先创建" />

    <div class="actions">
      <el-button @click="refresh">刷新列表</el-button>
      <router-link to="/admin/tenants">
        <el-button type="primary">进入租户管理</el-button>
      </router-link>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useAppStore } from '@/stores/app'
import { tenantApi } from '@/api/tenant'

const appStore = useAppStore()

const tenants = computed(() => appStore.tenant.tenants)

async function refresh() {
  await appStore.loadTenants()
}

function select(tenant) {
  appStore.setCurrentTenant(tenant)
  router.push('/')
}

onMounted(() => refresh())
</script>
```

### 2.6 路由表加租户选择路由

```typescript
const routes: RouteRecordRaw[] = [
  {
    path: '/select-tenant',
    name: 'TenantSelect',
    component: () => import('@/views/auth/TenantSelect.vue'),
  },
  // ... 现有路由
]
```

---

## 三、P1 — 租户管理页面（2-3 天）

### 3.1 新建 views/admin/TenantList.vue — 租户列表

```vue
<template>
  <div>
    <el-page-header title="租户管理" @back="router.push('/')" />

    <div class="toolbar">
      <el-button type="primary" @click="showCreateDialog">新建租户</el-button>
      <el-input v-model="search" placeholder="搜索站点名称" />
    </div>

    <el-table :data="filteredTenants" stripe>
      <el-table-column prop="id" label="租户 ID" width="220" />
      <el-table-column prop="name" label="站点名称" />
      <el-table-column prop="status" label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === 'active' ? 'success' : 'danger'">
            {{ row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="documentCount" label="文档数" width="100" />
      <el-table-column prop="rateLimitPerUser" label="用户限流" width="140" />
      <el-table-column prop="llmModel" label="LLM 模型" width="200" />
      <el-table-column prop="createdAt" label="创建时间" width="180" />
      <el-table-column label="操作" width="320">
        <template #default="{ row }">
          <el-button size="small" @click="showEditDialog(row)">编辑</el-button>
          <el-button size="small" type="warning" @click="rotateKey(row)">轮换Key</el-button>
          <el-button size="small" @click="showStats(row)">统计</el-button>
          <el-button size="small" type="danger" @click="handleDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 创建/编辑对话框 -->
    <el-dialog v-model="dialogVisible" :title="editMode ? '编辑租户' : '新建租户'">
      <el-form :model="form" label-width="120px">
        <el-form-item label="站点名称">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="状态">
          <el-radio-group v-model="form.status">
            <el-radio value="active">启用</el-radio>
            <el-radio value="suspended">暂停</el-radio>
            <el-radio value="trial">试用</el-radio>
          </el-radio-group>
        </el-form-item>

        <el-divider content-position="left">LLM 配置</el-divider>
        <el-form-item label="API Base">
          <el-input v-model="form.llmApiBase" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="form.llmApiKey" type="password" show-password />
        </el-form-item>
        <el-form-item label="Model">
          <el-input v-model="form.llmModel" placeholder="agnes-2.0-flash" />
        </el-form-item>
        <el-form-item label="Timeout">
          <el-input-number v-model="form.llmTimeout" :min="1" :max="120" />
        </el-form-item>

        <el-divider content-position="left">限流配置</el-divider>
        <el-form-item label="每用户限流">
          <el-input v-model="form.rateLimitPerUser" placeholder="60/minute" />
        </el-form-item>
        <el-form-item label="全局限流">
          <el-input v-model="form.rateLimitGlobal" placeholder="1000/minute" />
        </el-form-item>

        <el-divider content-position="left">Prompt 模板</el-divider>
        <el-form-item label="系统 Prompt">
          <el-input v-model="form.systemPrompt" type="textarea" :rows="6"
            placeholder="留空使用默认模板" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- API Key 轮换确认对话框 -->
    <el-dialog v-model="keyDialogVisible" title="轮换 API Key">
      <p>确认要轮换 API Key 吗？新 Key 为：</p>
      <el-input :model-value="newKey" readonly />
      <template #footer>
        <el-button @click="keyDialogVisible = false">关闭</el-button>
        <el-button type="primary" @click="copyKey">复制</el-button>
      </template>
    </el-dialog>

    <!-- 租户统计面板 -->
    <el-dialog v-model="statsDialogVisible" :title="`统计 - ${currentTenant?.name}`" width="80%">
      <el-row :gutter="20">
        <el-col :span="6">
          <el-statistic title="文档数" :value="stats?.documentCount ?? 0" />
        </el-col>
        <el-col :span="6">
          <el-statistic title="请求次数" :value="stats?.totalCalls ?? 0" />
        </el-col>
        <el-col :span="6">
          <el-statistic title="输入 Token" :value="stats?.promptTokens ?? 0" />
        </el-col>
        <el-col :span="6">
          <el-statistic title="输出 Token" :value="stats?.completionTokens ?? 0" />
        </el-col>
      </el-row>
      <el-card title="Token 用量趋势" style="margin-top: 20px">
        <v-chart :option="chartOption" style="height: 300px" />
      </el-card>
    </el-dialog>
  </div>
</template>
```

### 3.2 路由加租户管理

```typescript
const routes: RouteRecordRaw[] = [
  // ... 现有路由
  {
    path: '/admin',
    name: 'Admin',
    component: MainLayout,
    children: [
      {
        path: 'tenants',
        name: 'TenantList',
        component: () => import('@/views/admin/TenantList.vue'),
        meta: { title: '租户管理', icon: 'User' },
      },
    ],
  },
]
```

### 3.3 新建 views/admin/TenantStats.vue — 租户统计详情

ECharts 折线图展示 Token 用量趋势（输入/输出堆叠面积图）。

---

## 四、P2 — 全局 UI 适配（0.5 天）

### 4.1 顶部导航栏 — 租户切换

```vue
<!-- components/layout/AppHeader.vue 或 MainLayout.vue 顶部 -->
<template>
  <div class="header-extra">
    <el-dropdown @command="switchTenant" trigger="click">
      <el-button text>
        <span class="tenant-name">{{ currentTenant?.name || '选择租户' }}</span>
        <el-icon><ArrowDown /></el-icon>
      </el-button>
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item v-for="t in tenants" :key="t.id"
            :command="t.id" :disabled="t.id === currentTenant?.id">
            <el-tag :type="t.status === 'active' ? 'success' : 'danger'" size="small">
              {{ t.name }}
            </el-tag>
          </el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>
  </div>
</template>
```

### 4.2 租户隔离提示条

在知识库、日志等页面顶部显示当前租户，防止混淆：

```vue
<el-alert
  v-if="appStore.tenant.current"
  :title="`当前操作租户：${appStore.tenant.current.name}`"
  type="info"
  show-icon
  :closable="false"
  style="margin-bottom: 16px"
/>
```

---

## 五、文件改动汇总

| 文件 | 改动类型 | 改动内容 | 工作量 |
|---|---|---|---|
| `src/stores/app.ts` | **改造** | 加 tenant 状态（current/tenants），改 setApiKey 联动 | 2h |
| `src/api/types.ts` | **改造** | 加 Tenant/CreateTenantRequest/UpdateTenantRequest/TenantKeyResponse/TenantStats | 1h |
| `src/api/tenant.ts` | **新建** | 租户 CRUD / rotateKey / stats API | 1h |
| `src/router.ts` | **改造** | 加路由守卫（无 tenant → 跳选择页）、加 admin 路由 | 1.5h |
| `src/views/auth/TenantSelect.vue` | **新建** | 租户选择页（列表 + 刷新） | 2h |
| `src/views/admin/TenantList.vue` | **新建** | 租户管理列表（增删改查 + 编辑对话框 + 统计） | 5h |
| `src/views/admin/TenantStats.vue` | **新建** | 租户统计详情（ECharts 图表） | 3h |
| `src/components/layout/AppHeader.vue` | **改造** | 加租户切换下拉 | 1.5h |
| `src/components/layout/MainLayout.vue` | **小改** | 顶部加租户提示条 | 1h |
| `src/views/knowledge/KnowledgeList.vue` | **小改** | 顶部加当前租户名称提示 | 0.5h |
| `src/views/logs/ChatLogs.vue` | **小改** | 顶部加当前租户名称提示 | 0.5h |

**总计：约 20 人时 = 2.5 个工作日**

---

## 六、前后端改造时序

```
Phase 1: 后端先行（P0 阶段）
    ├── domain/models.py         → Tenant 实体
    ├── services/tenant_service  → 租户 CRUD
    ├── api/routes.py            → resolve_tenant 中间件
    ├── zvec_store.py            → 所有方法加 tenant filter
    └── knowledge_service.py     → 方法加 tenant_id 参数

Phase 2: 前端跟上（P0 + P1 阶段）
    ├── stores/app.ts            → 加 tenant 状态
    ├── api/tenant.ts            → 租户 API
    ├── api/types.ts             → 类型定义
    ├── router.ts                → 路由守卫
    ├── views/auth/TenantSelect  → 租户选择页
    └── views/admin/TenantList   → 租户管理页

Phase 3: 联调
    └── 端到端测试：创建租户 → 切换 → 知识库 CRUD → 问答 → 统计

Phase 4: 增强（P2）
    ├── 租户统计图表（ECharts）
    ├── Token 用量监控
    ├── 前端聊天 SDK（可选）
```
