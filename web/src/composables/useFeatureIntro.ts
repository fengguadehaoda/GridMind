/**
 * useFeatureIntro · 功能介绍知识库读取 composable（V1.6 T5）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 职责：
 *   1. 拉取 `GET /api/knowledge/feature-intro` 全量分片（一次请求拿全部）
 *   2. 按 tag 前缀二次分类为 `scenarios` / `tours` / `step3`
 *   3. 暴露 `loading` / `error` / `isFallback` 状态
 *   4. **任何失败都不抛给调用方**——静默降级到本地兜底常量，
 *      保证引导流程 100% 可用（需求硬约束："API 失败回退本地"）
 *
 * 关键设计：
 *   - **模块级单例状态**：Step1Scenario / OnboardingTour / Step3Monitor 三个组件
 *     共享同一份数据与同一次在途请求（`inflight` 去重），避免重复打后端。
 *   - **5s 超时**：用 `AbortController` + `setTimeout` 实现（不依赖 axios 配置，
 *     也不引入新依赖）。超时即视为失败 → 走兜底。
 *   - **部分降级**：API 只返回了 scenarios 没返回 tours 时，tours 仍用兜底，
 *     两者互不影响（逐类 fallback 而非整体 fallback）。
 *
 * 使用：
 *   const { scenarios, tours, step3, loading, error, isFallback, load } = useFeatureIntro()
 *   onMounted(() => void load())
 *
 * 作者：寇豆码（工程师）
 */
import { computed, ref, type ComputedRef, type Ref } from 'vue'
import type { DriveStep } from 'driver.js'
import { getAuthHeaders } from '@/composables/useJwtAuth'
import {
  STEP3_FALLBACK,
  TOUR_NAMES,
  TOUR_STEPS_FALLBACK,
  type Step3Bullet,
  type Step3Content,
  type Step3Cta,
  type TourName,
} from '@/constants/featureIntroFallback'
import {
  ONBOARDING_SCENARIOS_FALLBACK,
  type OnboardingScenario,
  type OnboardingScenarioId,
} from '@/types/theme'

// ═══════════════════════════════════════════════════════
// 常量
// ═══════════════════════════════════════════════════════

/** 后端端点（Vite dev 通过 /api 代理到 localhost:9900） */
const API_PATH = '/api/knowledge/feature-intro'

/** 请求超时（毫秒）。引导页是首屏体验，超过 5s 宁可用兜底也不能卡住 */
const REQUEST_TIMEOUT_MS = 5000

/** 场景 tag 前缀，如 `scenario:fault-diagnosis` */
const TAG_PREFIX_SCENARIO = 'scenario:'

/** tour tag 前缀，如 `tour:chat` */
const TAG_PREFIX_TOUR = 'tour:'

/** 引导第 3 步 tag */
const TAG_WIZARD_STEP3 = 'wizard:step3'

/** 合法场景 id 白名单（防后端脏数据污染前端枚举） */
const SCENARIO_IDS: readonly OnboardingScenarioId[] = [
  'monitor-overview',
  'fault-diagnosis',
  'knowledge-rag',
  'grayscale-rollout',
] as const

/** driver.js popover 允许的 side 取值（与 driver.js 内部 `Side` 字面量联合保持一致） */
type PopoverSide = 'top' | 'right' | 'bottom' | 'left'
const POPOVER_SIDES: readonly PopoverSide[] = ['top', 'right', 'bottom', 'left'] as const

/** driver.js popover 允许的 align 取值 */
type PopoverAlign = 'start' | 'center' | 'end'
const POPOVER_ALIGNS: readonly PopoverAlign[] = ['start', 'center', 'end'] as const

// ═══════════════════════════════════════════════════════
// 类型
// ═══════════════════════════════════════════════════════

/** 后端返回的单条分片（与 `api/routers/feature_intro.py::FeatureIntroItem` 对齐） */
export interface FeatureIntroItem {
  id: string
  doc_id: string
  title: string
  content: string
  tags: string[]
  icon: string | null
  starterMessage: string | null
  source: string
  meta: Record<string, unknown>
}

/** `GET /api/knowledge/feature-intro` 响应体 */
export interface FeatureIntroResponse {
  items: FeatureIntroItem[]
  total: number
  tag: string
}

/** composable 返回值 */
export interface UseFeatureIntroReturn {
  /** 原始分片列表（未分类，供未来"帮助中心"等场景直接检索） */
  items: Ref<FeatureIntroItem[]>
  /** 引导场景卡（wizard 第 1/2 步） */
  scenarios: ComputedRef<OnboardingScenario[]>
  /** 各页面 driver.js tour 步骤 */
  tours: ComputedRef<Record<TourName, DriveStep[]>>
  /** 引导 wizard 第 3 步文案 */
  step3: ComputedRef<Step3Content>
  /** 请求进行中 */
  loading: Ref<boolean>
  /** 失败原因（成功为 null）。非空不代表 UI 不可用——此时已自动走兜底 */
  error: Ref<string | null>
  /** 当前展示的是否为本地兜底数据 */
  isFallback: ComputedRef<boolean>
  /** 拉取（默认带进程内缓存；已成功过则直接返回） */
  load: (force?: boolean) => Promise<void>
  /** 强制重新拉取（= load(true)） */
  refresh: () => Promise<void>
}

// ═══════════════════════════════════════════════════════
// 模块级单例状态（跨组件共享）
// ═══════════════════════════════════════════════════════

const items = ref<FeatureIntroItem[]>([])
const loading = ref<boolean>(false)
const error = ref<string | null>(null)

/** 是否已成功加载过（成功后不再重复请求，除非 force） */
const loaded = ref<boolean>(false)

/** 在途请求，用于并发去重 */
let inflight: Promise<void> | null = null

// ═══════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════

/**
 * 安全读取对象字段（后端 meta 是 `Record<string, unknown>`，需逐字段收窄）。
 */
function pickString(source: Record<string, unknown>, key: string): string {
  const value = source[key]
  return typeof value === 'string' ? value : ''
}

/** 安全读取字符串数组 */
function pickStringArray(source: Record<string, unknown>, key: string): string[] {
  const value = source[key]
  if (!Array.isArray(value)) return []
  return value.filter((v): v is string => typeof v === 'string')
}

/** 从 tags 中取出指定前缀后的值，如 `scenario:fault-diagnosis` → `fault-diagnosis` */
function tagValue(tags: string[], prefix: string): string {
  const hit = tags.find((t) => t.startsWith(prefix))
  return hit ? hit.slice(prefix.length) : ''
}

/**
 * 校验并转换单个 DriveStep（后端 meta.steps 元素）。
 *
 * @returns 合法的 DriveStep；结构不合法返回 null（该步被丢弃）
 */
function toDriveStep(raw: unknown): DriveStep | null {
  if (typeof raw !== 'object' || raw === null) return null
  const obj = raw as Record<string, unknown>
  const element = obj.element
  if (typeof element !== 'string' || element.length === 0) return null

  const popoverRaw = obj.popover
  const popoverObj =
    typeof popoverRaw === 'object' && popoverRaw !== null
      ? (popoverRaw as Record<string, unknown>)
      : {}

  // driver.js 的 side/align 是字面量联合类型；后端来的是任意字符串，
  // 这里做白名单收窄，非法值回落到与原硬编码一致的默认值。
  const sideRaw = pickString(popoverObj, 'side')
  const alignRaw = pickString(popoverObj, 'align')
  const side = (POPOVER_SIDES as readonly string[]).includes(sideRaw)
    ? (sideRaw as PopoverSide)
    : 'top'
  const align = (POPOVER_ALIGNS as readonly string[]).includes(alignRaw)
    ? (alignRaw as PopoverAlign)
    : 'center'

  return {
    element,
    popover: {
      title: pickString(popoverObj, 'title'),
      description: pickString(popoverObj, 'description'),
      side,
      align,
    },
  }
}

/** 校验并转换 Step3 的 bullet */
function toStep3Bullet(raw: unknown): Step3Bullet | null {
  if (typeof raw !== 'object' || raw === null) return null
  const obj = raw as Record<string, unknown>
  const title = pickString(obj, 'title')
  if (!title) return null
  return {
    icon: pickString(obj, 'icon') || 'Monitor',
    title,
    description: pickString(obj, 'description'),
  }
}

/** 校验并转换 Step3 的 CTA；任一必填缺失则回落兜底 CTA */
function toStep3Cta(raw: unknown): Step3Cta {
  if (typeof raw !== 'object' || raw === null) return STEP3_FALLBACK.cta
  const obj = raw as Record<string, unknown>
  const label = pickString(obj, 'label')
  const path = pickString(obj, 'path')
  if (!label || !path) return STEP3_FALLBACK.cta
  const tourRaw = pickString(obj, 'tour')
  const tour = (TOUR_NAMES as readonly string[]).includes(tourRaw)
    ? (tourRaw as TourName)
    : STEP3_FALLBACK.cta.tour
  return {
    label,
    path,
    tour,
    hint: pickString(obj, 'hint') || STEP3_FALLBACK.cta.hint,
  }
}

// ═══════════════════════════════════════════════════════
// 数据拉取
// ═══════════════════════════════════════════════════════

/**
 * 执行一次带超时的 GET 请求。
 *
 * @throws Error 网络错误 / HTTP 非 2xx / 超时（调用方统一捕获后降级）
 */
async function fetchFeatureIntro(): Promise<FeatureIntroItem[]> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const resp = await fetch(API_PATH, {
      method: 'GET',
      headers: { Accept: 'application/json', ...getAuthHeaders() },
      signal: controller.signal,
    })
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`)
    }
    const data = (await resp.json()) as Partial<FeatureIntroResponse>
    if (!Array.isArray(data.items)) {
      throw new Error('响应缺少 items 数组')
    }
    return data.items
  } finally {
    clearTimeout(timer)
  }
}

/**
 * 加载功能介绍数据（幂等 + 并发去重 + 静默降级）。
 *
 * @param force 为 true 时忽略缓存强制重新拉取
 */
async function load(force = false): Promise<void> {
  if (loaded.value && !force) return
  if (inflight) return inflight

  loading.value = true
  error.value = null

  inflight = (async () => {
    try {
      const fetched = await fetchFeatureIntro()
      items.value = fetched
      loaded.value = true
      if (fetched.length === 0) {
        // 库为空（未执行 seed 脚本）不算致命错误，但要让 isFallback 生效
        error.value = '知识库暂无功能介绍数据，已使用本地文案'
      }
    } catch (e) {
      const reason = e instanceof Error ? e.message : String(e)
      const isAbort = e instanceof DOMException && e.name === 'AbortError'
      error.value = isAbort
        ? `请求超时（>${REQUEST_TIMEOUT_MS}ms），已使用本地文案`
        : `功能介绍知识库读取失败（${reason}），已使用本地文案`
      items.value = []
      // 注意：不置 loaded=true，下次进入引导页会自动重试
      console.warn('[useFeatureIntro]', error.value)
    } finally {
      loading.value = false
      inflight = null
    }
  })()

  return inflight
}

// ═══════════════════════════════════════════════════════
// 分类计算属性
// ═══════════════════════════════════════════════════════

/**
 * 场景卡：`scenario:<id>` tag → :class:`OnboardingScenario`。
 *
 * 排序严格按 `SCENARIO_IDS` 白名单顺序（保证 UI 卡片顺序稳定，
 * 不受后端返回顺序影响）；任一场景缺失则该条用兜底补齐。
 */
const scenarios = computed<OnboardingScenario[]>(() => {
  const byId = new Map<string, FeatureIntroItem>()
  for (const item of items.value) {
    const id = tagValue(item.tags, TAG_PREFIX_SCENARIO)
    if (id) byId.set(id, item)
  }

  return SCENARIO_IDS.map((id) => {
    const fallback =
      ONBOARDING_SCENARIOS_FALLBACK.find((s) => s.id === id) ??
      ONBOARDING_SCENARIOS_FALLBACK[0]
    const hit = byId.get(id)
    if (!hit) return fallback
    return {
      id,
      title: hit.title || fallback.title,
      description: pickString(hit.meta, 'description') || fallback.description,
      icon: hit.icon || fallback.icon,
      starterMessage: hit.starterMessage || fallback.starterMessage,
    }
  })
})

/**
 * tour 步骤：`tour:<page>` tag + `meta.steps` → driver.js DriveStep[]。
 *
 * 逐 tour 降级：某个页面的 tour 缺失或步骤全非法时，只有该页面用兜底。
 */
const tours = computed<Record<TourName, DriveStep[]>>(() => {
  const byName = new Map<string, FeatureIntroItem>()
  for (const item of items.value) {
    const name = tagValue(item.tags, TAG_PREFIX_TOUR)
    if (name) byName.set(name, item)
  }

  const result = {} as Record<TourName, DriveStep[]>
  for (const name of TOUR_NAMES) {
    const hit = byName.get(name)
    const rawSteps = hit ? hit.meta.steps : undefined
    const steps = Array.isArray(rawSteps)
      ? rawSteps.map(toDriveStep).filter((s): s is DriveStep => s !== null)
      : []
    result[name] = steps.length > 0 ? steps : TOUR_STEPS_FALLBACK[name]
  }
  return result
})

/**
 * 引导第 3 步文案：`wizard:step3` tag + `meta.bullets` / `meta.cta`。
 */
const step3 = computed<Step3Content>(() => {
  const hit = items.value.find((item) => item.tags.includes(TAG_WIZARD_STEP3))
  if (!hit) return STEP3_FALLBACK

  const bullets = Array.isArray(hit.meta.bullets)
    ? hit.meta.bullets.map(toStep3Bullet).filter((b): b is Step3Bullet => b !== null)
    : []
  const highlights = pickStringArray(hit.meta, 'highlights')

  return {
    title: hit.title || STEP3_FALLBACK.title,
    description: pickString(hit.meta, 'description') || STEP3_FALLBACK.description,
    highlights: highlights.length > 0 ? highlights : STEP3_FALLBACK.highlights,
    bullets: bullets.length > 0 ? bullets : STEP3_FALLBACK.bullets,
    cta: toStep3Cta(hit.meta.cta),
  }
})

/** 是否正在使用本地兜底（库里一条都没拿到） */
const isFallback = computed<boolean>(() => items.value.length === 0)

// ═══════════════════════════════════════════════════════
// 对外入口
// ═══════════════════════════════════════════════════════

/**
 * 功能介绍知识库 composable。
 *
 * 所有调用方共享同一份模块级状态，多组件同时调用只会触发一次网络请求。
 */
export function useFeatureIntro(): UseFeatureIntroReturn {
  return {
    items,
    scenarios,
    tours,
    step3,
    loading,
    error,
    isFallback,
    load,
    refresh: () => load(true),
  }
}

/** 仅供单测使用：清空模块级缓存 */
export function __resetFeatureIntroCache(): void {
  items.value = []
  loading.value = false
  error.value = null
  loaded.value = false
  inflight = null
}
