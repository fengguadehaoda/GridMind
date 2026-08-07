<template>
  <!--
    空 wrapper：driver.js 自己渲染 overlay + popover 到 body 根节点
    （不放任何元素，避免 driver.js 误把 anchor 选到本组件根节点）
  -->
</template>

<script setup lang="ts">
/**
 * OnboardingTour · 单页 tour 触发器（v1.5.0 P0-4 架构 §1.3）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 工作原理：
 *   1. 监测路由 query.tour（chat / monitor / grayscale / audit / system）
 *   2. 一旦匹配，启动 driver.js（default import）
 *   3. 全屏一次性 popover，分别高亮 5 个 view 的关键 anchor
 *   4. 完成 / 关闭后清掉 ?tour=xxx query，不污染 history
 *   5. 组件本身无 DOM 输出（Teleport disable）
 *
 * 挂载点：在视图层（ChatView / MonitoringView / GrayscalePanel / AuditLogViewer / SystemOverview）
 * 也可以全局挂在 App.vue 一次（按 route.name 动态激活）
 *
 * 集成策略（架构 §7.4 性能影响）：
 *   - driver.js 在首次 mount 时 dynamic import，code-split
 *   - 任意时刻只有一个 driver 实例；先 destroy 再 drive
 *   - 中文按钮：nextBtnText='下一步' / prevBtnText='上一步' / doneBtnText='完成' /
 *     closeBtnText 由 driver 自己写'×'
 */
import { onBeforeUnmount, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { Driver, DriveStep } from 'driver.js'
import { useFeatureIntro } from '@/composables/useFeatureIntro'
import {
  TOUR_NAMES,
  TOUR_STEPS_FALLBACK,
  type TourName,
} from '@/constants/featureIntroFallback'

/**
 * tour 名枚举（同步 router/index.ts 中允许的 ?tour= 取值）
 *
 * V1.6：定义已迁到 `@/constants/featureIntroFallback`（避免 composable ←→
 * 组件循环依赖），此处 re-export 保持旧引用兼容。
 */
export type { TourName }

// 说明：原内联的 `TOUR_STEPS` 已抽离为
// `@/constants/featureIntroFallback::TOUR_STEPS_FALLBACK`（`<script setup>`
// 不允许值导出，故不在此 re-export；需要兜底常量的地方直接从 constants 引入）。
// 正常路径改由知识库下发：`useFeatureIntro().tours`。

const TOUR_QUERY_KEY = 'tour'

const route = useRoute()
const router = useRouter()

/** tour 步骤数据源：知识库优先，逐 tour 自动回落兜底 */
const { tours, load } = useFeatureIntro()

let driverInstance: Driver | null = null

/** 各 tour 的 DriveStep 列表（每个 3-5 个 anchor ≈ 总计 20 anchor） */
const TOUR_STEPS: Record<TourName, DriveStep[]> = {
  chat: [
    {
      element: '[data-tour="chat-history"]',
      popover: {
        title: '对话流',
        description:
          '这里是完整对话历史：用户提问 → LLM 推理 → 工具返回 → 最终回答。每个气泡支持快捷审批（HITL）。',
        side: 'top',
        align: 'center',
      },
    },
    {
      element: '[data-tour="chat-demo-shortcuts"]',
      popover: {
        title: '演示快捷指令',
        description:
          '4 个种子快捷方式：设备查询 / 异常检测 / 知识检索 / 高危操作。点任意卡片即真实发送消息。',
        side: 'top',
        align: 'center',
      },
    },
    {
      element: '[data-tour="chat-model-switcher"]',
      popover: {
        title: '模型切换',
        description: '支持在线切换 LLM 后端（v2.0 / 多模型热切换）。',
        side: 'top',
        align: 'center',
      },
    },
    {
      element: '[data-tour="chat-input"]',
      popover: {
        title: '输入区',
        description:
          '回车发送 · Shift+Enter 换行 · 发送后会启动 700ms 思考延迟 + 真实 SSE 流式回复。',
        side: 'top',
        align: 'center',
      },
    },
  ],
  monitor: [
    {
      element: '[data-tour="monitor-stats"]',
      popover: {
        title: '顶部统计',
        description:
          '4 个 StatHexagon：设备总数 / 正常运行 / 预警 / 严重。颜色 + 形状 + 图标 + 文字码四重区分。',
        side: 'bottom',
        align: 'center',
      },
    },
    {
      element: '[data-tour="monitor-toolbar"]',
      popover: {
        title: '刷新控制',
        description: '手动刷新 / 自动刷新开关（每 15 秒轮询）。',
        side: 'bottom',
        align: 'start',
      },
    },
    {
      element: '[data-tour="monitor-table"]',
      popover: {
        title: '设备总览表',
        description:
          '按健康分升序排列（最差在前）。点击"详情"打开抽屉查看遥测趋势 + 巡检记录。',
        side: 'top',
        align: 'center',
      },
    },
    {
      element: '[data-tour="monitor-health-card"]',
      popover: {
        title: '健康评分卡',
        description:
          '打开任一设备详情后看到。综合 LLM + 机理校验 + 规则护栏三层输出健康分与异常清单。',
        side: 'top',
        align: 'center',
      },
    },
    {
      element: '[data-tour="monitor-telemetry"]',
      popover: {
        title: '遥测趋势图',
        description:
          '点击 6h / 24h / 48h 切换时间窗。异常数据点用三角形标记（标准状态四重区分的一部分）。',
        side: 'top',
        align: 'center',
      },
    },
  ],
  grayscale: [
    {
      element: '[data-tour="grayscale-stats"]',
      popover: {
        title: '灰度统计',
        description:
          '当前切流比例 / 状态机 / 错误率 / 累计回滚次数。语义不依赖颜色（图标 + 文字码）。',
        side: 'bottom',
        align: 'center',
      },
    },
    {
      element: '[data-tour="grayscale-toggle"]',
      popover: {
        title: '手动切流',
        description:
          '需要管理员 token（环境变量 ADMIN_TOKEN）。比例只支持 0 / 10 / 50 / 100 四档。',
        side: 'bottom',
        align: 'center',
      },
    },
    {
      element: '[data-tour="grayscale-metrics"]',
      popover: {
        title: '监控窗口',
        description:
          '样本数 / 错误率 / P95 / Neo4j 连续失败。任意指标超阈值自动触发回滚。',
        side: 'top',
        align: 'center',
      },
    },
    {
      element: '[data-tour="grayscale-history"]',
      popover: {
        title: '切换历史',
        description: '最近 10 条切换记录。auto_ 前缀代表系统自动回滚。',
        side: 'top',
        align: 'center',
      },
    },
  ],
  audit: [
    {
      element: '[data-tour="audit-stats"]',
      popover: {
        title: '审计统计',
        description: '总记录 + 按决策类型（批准 / 拒绝 / 编辑）的分布。',
        side: 'bottom',
        align: 'center',
      },
    },
    {
      element: '[data-tour="audit-filter"]',
      popover: {
        title: '筛选栏',
        description: '按 thread_id 子串过滤 / 按决策类型过滤，回车 / change 即应用。',
        side: 'bottom',
        align: 'center',
      },
    },
    {
      element: '[data-tour="audit-list"]',
      popover: {
        title: '审计条目',
        description:
          '每条记录包含 thread_id / actor / tool / risk / reason / 编辑内容。所有 HITL 操作 3 年留存。',
        side: 'top',
        align: 'center',
      },
    },
  ],
  system: [
    {
      element: '[data-tour="system-grayscale"]',
      popover: {
        title: '灰度总览',
        description: '聚合显示灰度状态机、Neo4j 路由占比、累计回滚、最近切换。',
        side: 'bottom',
        align: 'start',
      },
    },
    {
      element: '[data-tour="system-model"]',
      popover: {
        title: 'LLM 模型',
        description: '当前模型 + 可选模型数 + 默认模型。',
        side: 'bottom',
        align: 'end',
      },
    },
    {
      element: '[data-tour="system-metrics"]',
      popover: {
        title: 'Prometheus 指标',
        description:
          'Counter / Gauge / Histogram 三类视图。5 秒自动刷新，最新值带脉冲微动效。',
        side: 'top',
        align: 'center',
      },
    },
  ],
}

/** driver.js 文案中文化（架构 §5 T04 + §10 主理人决策 #5） */
const DRIVER_OPTS = {
  animate: true,
  smoothScroll: true,
  allowClose: true,
  allowKeyboardControl: true,
  overlayClickBehavior: 'close' as const,
  showButtons: ['next', 'previous', 'close'] as ('next' | 'previous' | 'close')[],
  showProgress: true,
  progressText: '第 {{current}} / {{total}} 步',
  nextBtnText: '下一步',
  prevBtnText: '上一步',
  doneBtnText: '完成',
  // driver.js v1.8 用 closeBtnText，原版用 closeBtnText 也可
  closeBtnText: '关闭',
  popoverClass: 'gm-driver-popover',
  stagePadding: 8,
  stageRadius: 6,
  // 防止 hover anchor 时被 driver 拦截
  disableActiveInteraction: false,
} as const

/** 动态 import driver.js（code-split） */
async function loadDriver(): Promise<typeof import('driver.js')> {
  // Vite dynamic import → 单独 chunk
  return await import('driver.js')
  // driver.css 自动 import（sideEffects 标记），无需手动引入
}

/** 启动 tour */
async function startTour(name: TourName): Promise<void> {
  // 先清理旧实例
  destroyDriver()

  const { driver } = await loadDriver()

  driverInstance = driver({
    ...DRIVER_OPTS,
    onDestroyed: () => {
      // 清理 query tour（无 history 入栈）
      if (route.query[TOUR_QUERY_KEY]) {
        const { [TOUR_QUERY_KEY]: _drop, ...rest } = route.query
        void _drop
        router.replace({ path: route.path, query: rest }).catch(() => undefined)
      }
    },
  })

  driverInstance.setSteps(TOUR_STEPS[name])
  driverInstance.drive()
}

/** 立即清理当前 driver 实例 */
function destroyDriver(): void {
  if (driverInstance) {
    try {
      driverInstance.destroy()
    } catch {
      /* noop */
    }
    driverInstance = null
  }
}

/** 重写 query 校验：白名单枚举 */
function pickTour(value: unknown): TourName | null {
  const allowed: TourName[] = ['chat', 'monitor', 'grayscale', 'audit', 'system']
  if (typeof value === 'string' && (allowed as string[]).includes(value)) {
    return value as TourName
  }
  return null
}

/** 路由 query.tour 变化 → 启动 tour；为空 → 销毁 */
watch(
  () => route.query[TOUR_QUERY_KEY],
  async (val) => {
    const name = pickTour(val)
    if (name) {
      // 等待下一个 tick，确保目标 view 的 anchors 已渲染
      await new Promise((r) => requestAnimationFrame(() => r(null)))
      // 再等一帧让数据驱动渲染（fetchDevices 等）
      setTimeout(() => {
        void startTour(name)
      }, 250)
    } else {
      destroyDriver()
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  destroyDriver()
})
</script>
