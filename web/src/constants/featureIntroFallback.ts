/**
 * 功能介绍本地兜底常量（V1.6 · 功能介绍知识库化 T6）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 单一职责：存放"API 不可用时"用的静态副本。
 *
 * 数据流：
 *   docs/gridmind-feature-introduction.md   ← 唯一事实来源（运营可改）
 *     └→ scripts/seed_feature_intro.py      ← 灌入 SQLite + Chroma
 *          └→ GET /api/knowledge/feature-intro
 *               └→ useFeatureIntro()        ← 正常路径
 *                    └→ 本文件               ← 降级路径（网络/后端故障）
 *
 * **放在独立模块而不是 .vue 内的原因**（对任务书"在 OnboardingTour.vue 内提取
 * TOUR_STEPS_FALLBACK"的结构性微调，已在交付说明中登记）：
 * `useFeatureIntro` 需要引用兜底常量做 fallback，而 `OnboardingTour.vue` 又要
 * 引用 `useFeatureIntro`——若常量留在 .vue 里会形成 **循环依赖**。抽到本模块后
 * 依赖方向单向：constants ← composable ← component。
 * `OnboardingTour.vue` 仍 re-export `TOUR_STEPS_FALLBACK`，旧引用零改动。
 *
 * 修改约定：**改文案请改 Markdown 文档**，本文件只在文档结构变化时同步，
 * 内容与文档第 4/5 章保持逐字一致。
 *
 * 作者：寇豆码（工程师）
 */
import type { DriveStep } from 'driver.js'

/** tour 名枚举（同步 router/index.ts 中允许的 ?tour= 取值） */
export type TourName = 'chat' | 'monitor' | 'grayscale' | 'audit' | 'system'

/** 合法 tour 名白名单（运行时校验用） */
export const TOUR_NAMES: readonly TourName[] = [
  'chat',
  'monitor',
  'grayscale',
  'audit',
  'system',
] as const

/** 引导 wizard 第 3 步的要点条目 */
export interface Step3Bullet {
  /** Element Plus 图标组件名 */
  icon: string
  title: string
  description: string
}

/** 引导 wizard 第 3 步的行动按钮 */
export interface Step3Cta {
  label: string
  /** 跳转路由 path */
  path: string
  /** 跳转后自动开启的单页 tour 名 */
  tour: TourName
  /** 按钮下方的补充说明 */
  hint: string
}

/** 引导 wizard 第 3 步的完整文案模型 */
export interface Step3Content {
  title: string
  description: string
  /** description 中需要高亮（<strong>）的词，前端做字符串切分渲染 */
  highlights: string[]
  bullets: Step3Bullet[]
  cta: Step3Cta
}

/**
 * 各 tour 的 DriveStep 兜底列表（对应文档第 4 章，共 19 个 anchor）。
 *
 * 与后端 `meta.steps` 字段结构完全一致，因此 API 返回值可直接顶替本常量。
 */
export const TOUR_STEPS_FALLBACK: Record<TourName, DriveStep[]> = {
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

/** 引导 wizard 第 3 步兜底文案（对应文档 §5.1，与原 Step3Monitor.vue 逐字一致） */
export const STEP3_FALLBACK: Step3Content = {
  title: '第三步 · 切换到实时监控视图',
  description:
    '第 2 步触发的"异常检测 / 设备查询 / 知识检索"会在监控页实时反馈：设备列表、健康评分、遥测曲线。点下方按钮跳转。',
  highlights: ['设备列表', '健康评分', '遥测曲线'],
  bullets: [
    {
      icon: 'Monitor',
      title: '设备实时列表',
      description: '按健康分排序 · 严重设备置顶 · 颜色 + 图标 + 文字码四重区分。',
    },
    {
      icon: 'DataAnalysis',
      title: '遥测趋势',
      description:
        '打开任意设备抽屉 → 切换 6h / 24h / 48h 时间窗 → 查看温度/负载/电流曲线。',
    },
    {
      icon: 'WarningFilled',
      title: '异常清单',
      description: 'z-score 异常检测 · 自动标注严重程度 · 一键跳到 HITL 审批页。',
    },
  ],
  cta: {
    label: '前往实时监控',
    path: '/monitor',
    tour: 'monitor',
    hint: '完成后点底部"完成，开始体验"统一结束引导。',
  },
}
