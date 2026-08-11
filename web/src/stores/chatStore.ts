import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type {
  ChatMessage,
  SseEvent,
  HealthScoreResult,
  KnowledgeAnswer,
  DemoShortcut,
  SessionSummary,
} from '../types'
import * as api from '../api/chat'
// M-5：会话管理 API（fetchSessions/rename/archive/restore/delete）
import * as sessionApi from '../api/sessions'
// F1 修复：chatStore 与 reasoning store 打通（无循环依赖：reasoning 不反向 import chatStore）
import { useReasoningStore } from './reasoning'

let _msgCounter = 0
function nextId(): string {
  return `msg_${Date.now()}_${++_msgCounter}`
}

/** 异步 sleep */
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * 发送冷却（v1.5.0 P0-4 主理人决策 #4）：
 *   wizard 第 2 步会自动触发 4 次 send（4 个 DemoShortcuts 任选）
 *   为防止后端 QPS 限流（架构 §1.3 + §8 待明确事项 #4 决策结果），
 *   chatStore 在 sendMessage / sendMessageBlocking 各加 5s cooldown。
 *
 *   - 5 秒内重复 send → 静默忽略
 *   - cooldown 计时基于"上一次**成功入队**"的时间戳
 *   - 该限制仅作用于 send 类入口，不影响 HITL approve / reject（异步审批）
 */
const SEND_COOLDOWN_MS = 5_000

/** 流式显示模拟参数（让 LLM 回答看起来像真实生成，避免秒出） */
const THINKING_DELAY_MS = 700           // 普通场景：思考延迟（仅显示跳动点）
const HIGH_RISK_THINKING_DELAY_MS = 1500 // 高危场景：延长思考延迟（显示分析文字）
const HIGH_RISK_WARN_DELAY_MS = 600     // 高危场景：弹窗前警告文字缓冲
const CHUNK_SIZE = 4                     // 每块字符数
const CHUNK_DELAY_MS = 45                // 每块间隔（ms）

export const useChatStore = defineStore('chat', () => {
  // ── State ──────────────────────────────────
  const messages = ref<ChatMessage[]>([])
  const threadId = ref<string>(`thread_${Date.now()}`)
  const loading = ref(false)
  const streaming = ref(false)

  // HITL state
  const interruptRequired = ref(false)
  const interruptNode = ref<string | null>(null)
  const interruptMsg = ref<string | null>(null)
  // 原始参数（Agent 生成，前端 EditDialog 用作默认值）
  const interruptArgs = ref<Record<string, unknown>>({})
  // 原始参数别名（后端 Interrupt payload 中 original_args）
  const interruptOriginalArgs = ref<Record<string, unknown>>({})
  const pendingThreadId = ref<string | null>(null)
  const hitlBusy = ref(false)
  const hitlSafetyReject = ref<string | null>(null)

  // v1.5.0 P0-4：send cooldown 时间戳（ref 化为 reactive state；不持久化）
  const lastSendAt = ref(0)

  // ── M-5 会话管理 state（T02）────────────────────────
  /** 活跃会话列表（archived=0，updated_at 倒序） */
  const sessions = ref<SessionSummary[]>([])
  /** 已归档会话列表（archived=1） */
  const archivedSessions = ref<SessionSummary[]>([])
  const sessionsLoading = ref(false)
  const sessionError = ref<string | null>(null)
  /**
   * 多会话内存缓存（AC4-2：切换不丢状态）。
   * 键 = thread_id；值 = 该会话的 messages 快照。
   * 约定（架构 §7 共享知识 #5）：**仅非空会话缓存**——空会话不产生缓存项。
   */
  const sessionMessagesCache = ref<Record<string, ChatMessage[]>>({})

  // SSE controller
  let sseController: AbortController | null = null

  // ── Computed ──────────────────────────────
  const lastAssistantMessage = computed(() => {
    for (let i = messages.value.length - 1; i >= 0; i--) {
      if (messages.value[i].role === 'assistant') return messages.value[i]
    }
    return null
  })

  /** M-5：导出数据源 getter = 当前激活会话内存消息（含已 attach 的 knowledgeAnswer） */
  const exportableMessages = computed<ChatMessage[]>(() => messages.value)

  const healthResults = computed<HealthScoreResult[]>(() => {
    const results: HealthScoreResult[] = []
    for (const msg of messages.value) {
      if (msg.healthScores?.length) results.push(...msg.healthScores)
    }
    return results
  })

  const knowledgeAnswers = computed<KnowledgeAnswer[]>(() => {
    const results: KnowledgeAnswer[] = []
    for (const msg of messages.value) {
      if (msg.knowledgeAnswer) results.push(msg.knowledgeAnswer)
    }
    return results
  })

  // v1.5.0 P0-4：cooldown reactive getters（供 UI 如 wizard Step2 显示）
  const isInCooldown = computed<boolean>(() => {
    return Date.now() - lastSendAt.value < SEND_COOLDOWN_MS
  })
  const cooldownRemainingSec = computed<number>(() => {
    const elapsed = Date.now() - lastSendAt.value
    const left = SEND_COOLDOWN_MS - elapsed
    if (left <= 0) return 0
    return Math.ceil(left / 1000)
  })

  // ── Actions ───────────────────────────────

  /** 发送消息（SSE 流式） */
  async function sendMessage(text: string) {
    if (!text.trim() || loading.value) return
    // v1.5.0 P0-4：5 秒 cooldown 防 wizard 自动 4 次 send 触发限流
    if (Date.now() - lastSendAt.value < SEND_COOLDOWN_MS) return
    lastSendAt.value = Date.now()

    const userMsg: ChatMessage = {
      id: nextId(),
      role: 'user',
      content: text,
      timestamp: new Date().toISOString(),
    }
    messages.value.push(userMsg)
    loading.value = true
    streaming.value = true

    // 创建占位 assistant 消息并获取 reactive proxy
    messages.value.push({
      id: nextId(),
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
      loading: true,
    })
    // ⚡ 从 reactive 数组中重新读取，确保拿到的是 reactive proxy
    const reactiveMsg = messages.value[messages.value.length - 1]

    // 本地缓冲：先收完 SSE，再模拟流式展示（让 UX 更接近真实 LLM）
    let fullResponse = ''
    // Bug3 修复：标记本次流是否已失败（onError 已写入 ❌ 错误内容）。
    // onDone 兜底回调不得再跑模拟流式输出覆盖错误内容，防止"先报错又被覆盖"。
    let streamFailed = false
    let interruptInfo: {
      required: boolean
      threadId?: string | null
      node?: string | null
      msg?: string | null
    } | null = null
    // M-3：done 事件携带的 knowledge_answer（流式收尾后 attachContext 修复缺口）
    let pendingKnowledgeAnswer: KnowledgeAnswer | null = null

    // SSE 流式请求
    sseController = api.streamChat(
      threadId.value,
      text,
      (event: SseEvent) => {
        if (event.type === 'token' && event.content) {
          // 收 token 到本地缓冲，不直接更新 reactiveMsg.content
          fullResponse += event.content
        } else if (event.type === 'done') {
          // F1 修复（QA F1 P1）：打通 chatStore.threadId 与 reasoning.sessionId
          // 后端 /chat/stream 的 done 事件携带真实 thread_id（main.py chat_stream）
          // → ① 同步 threadId（此前从未被更新，恒为初始 thread_xxx）
          // → ② 启动 reasoning 会话级状态机，触发 ChatView watch 挂载
          //      /sessions/{id}/events SSE（step_* / reasoning_* / hitl_* 事件）
          // 同 thread 重复 done（多轮对话）不重复 start，保留已累积 steps。
          if (event.thread_id) {
            threadId.value = event.thread_id
            const reasoningStore = useReasoningStore()
            if (reasoningStore.sessionId !== event.thread_id) {
              reasoningStore.start(event.thread_id)
            }
            // 遗留 A4（P2 增强）：M-5 懒登记新会话实时上侧栏。
            // 新会话首轮发送后后端才在 threads 表登记真实 thread_id，
            // 本地将其 upsert 进 sessions 列表，无需等下次 fetchSessions；
            // 已存在（多轮对话/已归档）时 _upsertSession 幂等无副作用。
            if (!sessions.value.some((s) => s.thread_id === event.thread_id)) {
              _upsertSession({
                thread_id: event.thread_id,
                title: text.trim().slice(0, 30) || '新会话',
                model_id: null,
                created_at: new Date().toISOString(),
                updated_at: new Date().toISOString(),
                archived: 0,
              })
            }
          }
          // Bug2 修复：演示模式剧本外响应 → 清掉残留审批态（防上一轮
          // pending_tool_plan / interrupt 状态卡死），并标记消息供样式展示
          if (event.is_demo_out_of_scope) {
            interruptRequired.value = false
            pendingThreadId.value = null
            interruptNode.value = null
            interruptMsg.value = null
            interruptArgs.value = {}
            interruptOriginalArgs.value = {}
            reactiveMsg.metadata = {
              ...(reactiveMsg.metadata || {}),
              is_demo_out_of_scope: true,
            }
          }
          // 暂存中断信息，等流式展示结束后再触发
          interruptInfo = {
            required: !!event.interrupt_required,
            threadId: event.thread_id || null,
            node: event.interrupt_node || null,
            msg: event.interrupt_msg || null,
          }
          // M-3：捕获 done 事件携带的 knowledge_answer（RagPanel 引用卡片数据源）
          pendingKnowledgeAnswer = event.knowledge_answer ?? null
        }
      },
      (err: string) => {
        streamFailed = true
        reactiveMsg.content = `❌ ${err}`
        reactiveMsg.loading = false
        streaming.value = false
        loading.value = false
      },
      async () => {
        // Bug3 修复：流已失败（onError 已触发）时直接收尾，
        // 不再跑模拟流式输出覆盖 ❌ 错误内容（防重复/覆盖）
        if (streamFailed) {
          streaming.value = false
          loading.value = false
          return
        }
        // M-3：SSE done 携带的 knowledge_answer → attach 到最近 assistant 消息
        // （修复 attachContext 从未被调用的缺口，RagPanel 据此渲染引用卡片）
        if (pendingKnowledgeAnswer) {
          attachContext({ knowledgeAnswer: pendingKnowledgeAnswer })
        }
        // ── SSE 完成 → 模拟流式展示 ─────────────────────
        const isHighRisk = !!interruptInfo?.required

        if (isHighRisk) {
          // ── 高危场景：拉长节奏，分段让用户看清分析过程 ──
          // 阶段 0：先让 ThinkingIndicator 跳动 1.2s（"分析中"语义）
          await sleep(HIGH_RISK_THINKING_DELAY_MS - 300) // 留 300ms 给阶段 1 起步
          reactiveMsg.loading = false

          // 阶段 1：先完整呈现本轮回答正文。
          //   融合层 HITL（诊断结论需人工复核）场景下，fullResponse 是诊断结论
          //   + 可解释性推理链——此前这里被警告文案整段覆盖，用户会丢失诊断内容。
          //   工具级 HITL 时图在 interrupt() 处挂起、本轮没有回答正文
          //   （fullResponse 为空），自动跳过本阶段，保持原有观感。
          let prefix = ''
          if (fullResponse) {
            for (let pos = 0; pos < fullResponse.length; pos += CHUNK_SIZE) {
              reactiveMsg.content = fullResponse.slice(0, Math.min(pos + CHUNK_SIZE, fullResponse.length))
              await sleep(CHUNK_DELAY_MS)
            }
            reactiveMsg.content = fullResponse
            prefix = `${fullResponse}\n\n`
            await sleep(300)
          }

          // 阶段 2：风险分析文字流式出现
          const warnText = '⚠️ 检测到高危操作请求，正在评估风险并匹配安全规范…'
          for (let pos = 0; pos < warnText.length; pos += CHUNK_SIZE) {
            reactiveMsg.content = prefix + warnText.slice(0, Math.min(pos + CHUNK_SIZE, warnText.length))
            await sleep(CHUNK_DELAY_MS)
          }
          await sleep(300)

          // 阶段 3：补充完整警告说明，缓冲后弹出 HITL 弹窗
          const detail = `\n\n经安全 Agent 评估，本次操作涉及：\n• 设备：${interruptInfo?.node || '目标设备'}\n• 风险等级：高\n• 建议：需值班负责人 / 调度员人工确认后方可执行`
          for (let pos = 0; pos < detail.length; pos += CHUNK_SIZE) {
            reactiveMsg.content = prefix + warnText + detail.slice(0, Math.min(pos + CHUNK_SIZE, detail.length))
            await sleep(CHUNK_DELAY_MS)
          }
          await sleep(HIGH_RISK_WARN_DELAY_MS)

          // 触发 HITL 弹窗
          interruptRequired.value = true
          pendingThreadId.value = interruptInfo?.threadId ?? null
          interruptNode.value = interruptInfo?.node ?? null
          interruptMsg.value = interruptInfo?.msg ?? null
          // 注意：SSE 流式目前不发原始 args，保留空对象（前端 EditDialog 默认按 schema 渲染）
          interruptArgs.value = {}
          interruptOriginalArgs.value = {}

          streaming.value = false
          loading.value = false
          return
        }

        // ── 普通场景：保持原有节奏 ─────────────────────
        // 思考延迟（期间 UI 展示 ThinkingIndicator 三个跳动点）
        await sleep(THINKING_DELAY_MS)

        // 开始流式输出：先关闭 loading indicator
        reactiveMsg.loading = false

        // 流式分块输出
        const total = fullResponse.length
        for (let pos = 0; pos < total; pos += CHUNK_SIZE) {
          reactiveMsg.content = fullResponse.slice(0, Math.min(pos + CHUNK_SIZE, total))
          await sleep(CHUNK_DELAY_MS)
        }
        // 兜底：确保最后一次写完整内容（避免边界误差）
        reactiveMsg.content = fullResponse

        streaming.value = false
        loading.value = false
      },
    )
  }

  /** 普通模式发送（非流式，用于 HITL 后续） */
  async function sendMessageBlocking(text: string) {
    if (!text.trim() || loading.value) return
    // v1.5.0 P0-4：5 秒 cooldown 防 wizard 自动 4 次 send 触发限流
    if (Date.now() - lastSendAt.value < SEND_COOLDOWN_MS) return
    lastSendAt.value = Date.now()

    const userMsg: ChatMessage = {
      id: nextId(),
      role: 'user',
      content: text,
      timestamp: new Date().toISOString(),
    }
    messages.value.push(userMsg)
    loading.value = true

    try {
      const resp = await api.sendMessage(text, threadId.value)
      threadId.value = resp.thread_id

      // Bug2 修复：演示模式剧本外响应 → 清掉残留审批态（防状态卡死）
      if (resp.is_demo_out_of_scope) {
        interruptRequired.value = false
        pendingThreadId.value = null
        interruptNode.value = null
        interruptMsg.value = null
        interruptArgs.value = {}
        interruptOriginalArgs.value = {}
      }

      const assistantMsg: ChatMessage = {
        id: nextId(),
        role: 'assistant',
        content: resp.response,
        timestamp: new Date().toISOString(),
        metadata: resp.is_demo_out_of_scope
          ? {
              agent_name: resp.agent_name || null,
              thread_id: resp.thread_id,
              is_demo_out_of_scope: true,
            }
          : undefined,
      }

      if (resp.interrupt_required) {
        interruptRequired.value = true
        interruptNode.value = resp.interrupt_node || null
        interruptMsg.value = resp.interrupt_msg || null
        pendingThreadId.value = resp.thread_id
        interruptArgs.value = {}
        interruptOriginalArgs.value = {}
      }

      messages.value.push(assistantMsg)
      // M-3（C-4 前端补齐）：阻塞路径同样 attach knowledge_answer（若后端下发）
      if (resp.knowledge_answer) {
        attachContext({ knowledgeAnswer: resp.knowledge_answer })
      }
    } catch (e: any) {
      messages.value.push({
        id: nextId(),
        role: 'assistant',
        content: `❌ 请求失败: ${e?.message || String(e)}`,
        timestamp: new Date().toISOString(),
      })
    } finally {
      loading.value = false
    }
  }

  /** 批准 HITL */
  async function approveHitl(reason = '') {
    if (!pendingThreadId.value) return
    interruptRequired.value = false
    try {
      const resp = await api.approveInterrupt(pendingThreadId.value, reason)
      messages.value.push({
        id: nextId(),
        role: 'system',
        content: `✅ 已批准高危操作${reason ? `（${reason}）` : ''}`,
        timestamp: new Date().toISOString(),
      })
      if (resp.response) {
        messages.value.push({
          id: nextId(),
          role: 'assistant',
          content: resp.response,
          timestamp: new Date().toISOString(),
        })
      }
    } catch (e: any) {
      messages.value.push({
        id: nextId(),
        role: 'system',
        content: `❌ 审批请求失败: ${e?.message || String(e)}`,
        timestamp: new Date().toISOString(),
      })
    } finally {
      pendingThreadId.value = null
      interruptNode.value = null
      interruptMsg.value = null
      interruptArgs.value = {}
      interruptOriginalArgs.value = {}
    }
  }

  /** 拒绝 HITL */
  async function rejectHitl(reason = '') {
    if (!pendingThreadId.value) return
    interruptRequired.value = false
    try {
      const resp = await api.rejectInterrupt(pendingThreadId.value, reason)
      messages.value.push({
        id: nextId(),
        role: 'system',
        content: `❌ 已拒绝高危操作${reason ? `（原因: ${reason}）` : ''}`,
        timestamp: new Date().toISOString(),
      })
      if (resp.response) {
        messages.value.push({
          id: nextId(),
          role: 'assistant',
          content: resp.response,
          timestamp: new Date().toISOString(),
        })
      }
    } catch (e: any) {
      messages.value.push({
        id: nextId(),
        role: 'system',
        content: `❌ 审批请求失败: ${e?.message || String(e)}`,
        timestamp: new Date().toISOString(),
      })
    } finally {
      pendingThreadId.value = null
      interruptNode.value = null
      interruptMsg.value = null
      interruptArgs.value = {}
      interruptOriginalArgs.value = {}
    }
  }

  /** Edit & Continue 批准（P0：修改后批准）
   *
   * 三步原子：
   * 1. safety 重检（仅 edit_approve，由后端执行）
   * 2. 审计写入 hitl_audit_log
   * 3. resume 图执行，edited_args 替换 pending_tool_plan 中的原 args
   *
   * Args:
   *   editedArgs: 编辑后的工具参数（前端表单收集后传入）
   *   editReason: 修改原因（必填，前端已经过 el-form 校验）
   *   rejectReason: 拒绝原因（仅 reject 模式使用）
   */
  async function decideHitl(
    decision: 'approve' | 'reject' | 'edit_approve',
    options: {
      editedArgs?: Record<string, unknown>
      editReason?: string
      rejectReason?: string
    } = {},
  ): Promise<{ rejected_by_safety?: boolean; safety_summary?: string } | null> {
    if (!pendingThreadId.value) return null
    hitlSafetyReject.value = null
    hitlBusy.value = true
    interruptRequired.value = false
    try {
      const payload: import('../types').InterruptDecisionRequest = {
        decision,
        reason: options.rejectReason ?? '',
        edited_args: decision === 'edit_approve' ? options.editedArgs ?? {} : undefined,
        edit_reason: decision === 'edit_approve' ? options.editReason ?? '' : undefined,
      }
      const resp = await api.decideInterrupt(pendingThreadId.value, payload)

      if (resp.rejected_by_safety) {
        // safety 重检失败：写系统消息 + 保留弹窗可重试
        hitlSafetyReject.value = resp.safety_summary || '安全重检未通过'
        messages.value.push({
          id: nextId(),
          role: 'system',
          content: `⚠️ 安全重检未通过：${resp.response || '禁止继续执行'}`,
          timestamp: new Date().toISOString(),
        })
        // 重开弹窗以让用户查看失败原因
        interruptRequired.value = true
        return { rejected_by_safety: true, safety_summary: resp.safety_summary }
      }

      const label =
        decision === 'approve'
          ? '✅ 已批准高危操作'
          : decision === 'reject'
            ? `❌ 已拒绝高危操作${options.rejectReason ? `（${options.rejectReason}）` : ''}`
            : `✏️ 已按编辑后内容批准（修改原因：${options.editReason ?? '未填写'}）`
      messages.value.push({
        id: nextId(),
        role: 'system',
        content: label,
        timestamp: new Date().toISOString(),
      })
      if (resp.response) {
        messages.value.push({
          id: nextId(),
          role: 'assistant',
          content: resp.response,
          timestamp: new Date().toISOString(),
        })
      }
      return null
    } catch (e: any) {
      messages.value.push({
        id: nextId(),
        role: 'system',
        content: `❌ 决策请求失败: ${e?.message || String(e)}`,
        timestamp: new Date().toISOString(),
      })
      // 失败时重开弹窗
      interruptRequired.value = true
      return null
    } finally {
      hitlBusy.value = false
      pendingThreadId.value = null
      interruptNode.value = null
      interruptMsg.value = null
      interruptArgs.value = {}
      interruptOriginalArgs.value = {}
    }
  }

  /** Edit & Continue：便捷 API，向后兼容 approveWithEdit 命名 */
  async function approveWithEdit(
    editedArgs: Record<string, unknown>,
    editReason: string,
  ) {
    return decideHitl('edit_approve', { editedArgs, editReason })
  }

  /** 重置对话 */
  function resetChat() {
    if (sseController) {
      sseController.abort()
      sseController = null
    }
    messages.value = []
    threadId.value = `thread_${Date.now()}`
    loading.value = false
    streaming.value = false
    interruptRequired.value = false
    interruptNode.value = null
    interruptMsg.value = null
    pendingThreadId.value = null
    interruptArgs.value = {}
    interruptOriginalArgs.value = {}
    hitlBusy.value = false
    hitlSafetyReject.value = null
    // v1.5.0 P0-4：reset 时一并清掉 send cooldown
    lastSendAt.value = 0
  }

  /* ────────────────────────────────────────────────────────────
   * M-5 会话管理（T02 · 架构 session-mgmt §1.4 + §7 共享知识 #5/#6）
   * ──────────────────────────────────────────────────────────── */

  /** 清理流式中断后残留的占位 assistant 消息（loading 且空内容） */
  function _dropStalePlaceholder(): void {
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant' && last.loading && !last.content) {
      messages.value = messages.value.slice(0, -1)
    }
  }

  /** 重置 HITL 态（切换会话时调用，架构 §7 #5 步骤③） */
  function _resetHitlState(): void {
    interruptRequired.value = false
    interruptNode.value = null
    interruptMsg.value = null
    interruptArgs.value = {}
    interruptOriginalArgs.value = {}
    pendingThreadId.value = null
    hitlBusy.value = false
    hitlSafetyReject.value = null
  }

  /**
   * Q7 切换守卫：流式输出中先轻量确认，确认后中断当前流（不静默丢输入）。
   *
   * @returns true=放行切换；false=用户取消。
   */
  async function guardSwitchSession(): Promise<boolean> {
    if (!streaming.value) return true
    try {
      await ElMessageBox.confirm(
        '当前会话正在生成，切换将中断，确定？',
        '切换会话',
        {
          confirmButtonText: '切换',
          cancelButtonText: '取消',
          type: 'warning',
          distinguishCancelAndClose: true,
        },
      )
    } catch {
      return false
    }
    // 中断复用现有 AbortController
    if (sseController) {
      sseController.abort()
      sseController = null
    }
    streaming.value = false
    loading.value = false
    _dropStalePlaceholder()
    return true
  }

  /** 把后端 /thread/{id} 历史消息映射为 ChatMessage[]（T03 复用） */
  function _mapHistoryMessage(
    m: Record<string, unknown>,
    tid: string,
    idx: number,
  ): ChatMessage {
    return {
      id: `hist_${tid}_${idx}_${Date.now()}`,
      role: (m.role as ChatMessage['role']) ?? 'assistant',
      content:
        typeof m.content === 'string'
          ? m.content
          : m.content == null
            ? ''
            : JSON.stringify(m.content),
      name: typeof m.name === 'string' ? m.name : undefined,
      timestamp: typeof m.timestamp === 'string' ? m.timestamp : new Date().toISOString(),
      metadata: {
        thread_id: tid,
        agent_name: typeof m.agent_name === 'string' ? m.agent_name : null,
      },
    }
  }

  /** 目标会话无缓存 → GET /thread/{id} 拉历史（失败返回空数组，不阻断切换） */
  async function _loadThreadMessages(targetId: string): Promise<ChatMessage[]> {
    try {
      const data = await api.getThread(targetId)
      const raw: unknown = data?.messages
      if (!Array.isArray(raw)) return []
      return raw
        .filter((m): m is Record<string, unknown> => !!m && typeof m === 'object')
        .map((m, i) => _mapHistoryMessage(m, targetId, i))
    } catch (e) {
      // R-X5 口径：用户侧通用文案，服务侧 console
      console.warn('[chatStore] load thread history failed:', e)
      ElMessage.warning('会话历史加载失败，已切换为空会话')
      return []
    }
  }

  /** 从活跃/归档两个列表移除某会话（本地同步辅助） */
  function _removeFromLists(tid: string): void {
    sessions.value = sessions.value.filter((s) => s.thread_id !== tid)
    archivedSessions.value = archivedSessions.value.filter((s) => s.thread_id !== tid)
  }

  /** 把会话插入正确列表（按 updated_at 倒序；archived=2 仅移除） */
  function _upsertSession(summary: SessionSummary): void {
    _removeFromLists(summary.thread_id)
    if (summary.archived === 1) {
      archivedSessions.value = [...archivedSessions.value, summary].sort((a, b) =>
        String(b.updated_at ?? '').localeCompare(String(a.updated_at ?? '')),
      )
    } else if (summary.archived === 0) {
      sessions.value = [...sessions.value, summary].sort((a, b) =>
        String(b.updated_at ?? '').localeCompare(String(a.updated_at ?? '')),
      )
    }
    // archived=2（软删）→ 已从两列表移除
  }

  /** M-5：拉取会话列表（活跃 + 归档两组并行） */
  async function fetchSessions(): Promise<void> {
    sessionsLoading.value = true
    sessionError.value = null
    try {
      const [activeRes, archivedRes] = await Promise.all([
        sessionApi.fetchSessions(0),
        sessionApi.fetchSessions(1),
      ])
      sessions.value = activeRes.sessions
      archivedSessions.value = archivedRes.sessions
    } catch (e) {
      sessionError.value = (e as Error)?.message || String(e)
      console.warn('[chatStore] fetchSessions failed:', e)
    } finally {
      sessionsLoading.value = false
    }
  }

  /**
   * M-5：新建会话 = 本地 resetChat（懒登记语义，Q6）。
   *
   * 不产生后端空会话垃圾行：thread_id 沿用 resetChat 生成的
   * ``thread_{Date.now()}``，首次发送消息后 /chat 才在 threads 表登记。
   */
  async function newSession(): Promise<void> {
    const proceed = await guardSwitchSession()
    if (!proceed) return
    resetChat()
    // 推理链状态机一并重置（避免旧会话 steps/SSE 串台）
    const reasoningStore = useReasoningStore()
    reasoningStore.reset()
    ElMessage.success('已新建会话')
  }

  /**
   * M-5：切换会话（AC4-2 多会话并行切换不丢状态）。
   *
   * 流程（架构 §7 #5）：
   * ① guardSwitchSession（流式确认 + abort）；
   * ② 缓存当前会话 messages（仅非空）；
   * ③ 目标有缓存 → 恢复；无 → GET /thread/{id} 映射为 ChatMessage[]；
   * ④ 重置 HITL 态；
   * ⑤ reasoning.reset()+start(tid) 重建会话状态机（ChatView watch
   *    reasoning.sessionId 自动切 SSE）；
   * ⑥ threadId 更新 → ChatView watch(threadId) 自动同步 modelStore。
   */
  async function activateThread(targetId: string): Promise<void> {
    if (!targetId || targetId === threadId.value) return
    const proceed = await guardSwitchSession()
    if (!proceed) return

    // ① 缓存当前会话（仅非空，共享知识 #5）
    if (messages.value.length > 0) {
      sessionMessagesCache.value[threadId.value] = [...messages.value]
    }

    // ③ 恢复 / 拉取目标会话
    const cached = sessionMessagesCache.value[targetId]
    if (cached && cached.length > 0) {
      messages.value = [...cached]
    } else {
      messages.value = await _loadThreadMessages(targetId)
    }

    // ④ 重置 HITL 态
    _resetHitlState()

    // ⑤ 重建推理链状态机（SSE 订阅跟随）
    const reasoningStore = useReasoningStore()
    reasoningStore.reset()
    reasoningStore.start(targetId)

    // ⑥ 更新激活会话（ChatView watch 自动同步 modelStore + SSE）
    threadId.value = targetId
  }

  /** M-5：重命名会话（成功即本地同步列表） */
  async function renameSession(tid: string, title: string): Promise<SessionSummary | null> {
    try {
      const updated = await sessionApi.renameSession(tid, title)
      _upsertSession(updated)
      return updated
    } catch (e) {
      console.warn('[chatStore] renameSession failed:', e)
      ElMessage.error('重命名失败，请稍后重试')
      return null
    }
  }

  /** M-5：归档会话（archived=1；激活态 → 自动回退新会话） */
  async function archiveSession(tid: string): Promise<void> {
    try {
      const resp = await sessionApi.archiveSession(tid)
      if (!resp.ok) return
      const wasActive = tid === threadId.value
      // 先取摘要再移除（_findSummary 依赖列表内容）
      const summary = _findSummary(tid)
      _removeFromLists(tid)
      if (summary) _upsertSession({ ...summary, archived: 1 })
      else await fetchSessions() // 兜底：缓存缺失时全量刷新
      if (wasActive) {
        await newSession()
      }
    } catch (e) {
      console.warn('[chatStore] archiveSession failed:', e)
      ElMessage.error('归档失败，请稍后重试')
    }
  }

  /** M-5：恢复归档会话（archived=0） */
  async function restoreSession(tid: string): Promise<void> {
    try {
      const resp = await sessionApi.restoreSession(tid)
      if (!resp.ok) return
      const summary = _findSummary(tid)
      _removeFromLists(tid)
      if (summary) _upsertSession({ ...summary, archived: 0 })
      else await fetchSessions() // 兜底：缓存缺失时全量刷新
    } catch (e) {
      console.warn('[chatStore] restoreSession failed:', e)
      ElMessage.error('恢复失败，请稍后重试')
    }
  }

  /** M-5：删除会话（软删 archived=2；激活态 → 自动回退新会话） */
  async function deleteSession(tid: string): Promise<void> {
    try {
      const resp = await sessionApi.deleteSession(tid)
      if (!resp.ok) return
      const wasActive = tid === threadId.value
      _removeFromLists(tid)
      delete sessionMessagesCache.value[tid]
      if (wasActive) {
        await newSession()
      }
    } catch (e) {
      console.warn('[chatStore] deleteSession failed:', e)
      ElMessage.error('删除失败，请稍后重试')
    }
  }

  /** 在活跃/归档两列表中查找某会话摘要（本地同步辅助） */
  function _findSummary(tid: string): SessionSummary | null {
    return (
      sessions.value.find((s) => s.thread_id === tid) ??
      archivedSessions.value.find((s) => s.thread_id === tid) ??
      null
    )
  }

  /** 添加上下文数据（健康评分/知识答案等）到最近一条 assistant 消息 */
  function attachContext(data: { healthScores?: HealthScoreResult[]; knowledgeAnswer?: KnowledgeAnswer | null }) {
    const last = lastAssistantMessage.value
    if (last) {
      if (data.healthScores) last.healthScores = data.healthScores
      if (data.knowledgeAnswer !== undefined) last.knowledgeAnswer = data.knowledgeAnswer
    }
  }

  // ── Demo Shortcuts ────────────────────────
  const demoShortcuts: DemoShortcut[] = [
    {
      label: '🔍 设备查询',
      icon: 'Monitor',
      message: '查询主变压器当前运行状态和遥测数据',
      description: '查询设备遥测数据 → 展示真实 SQLite 数据',
    },
    {
      label: '⚠️ 异常检测',
      icon: 'Warning',
      message: '对所有设备进行异常检测分析',
      description: '运行 z-score 异常检测 → 展示健康评分与异常清单',
    },
    {
      label: '📖 知识查询',
      icon: 'Notebook',
      message: '变压器过载如何处置',
      description: 'RAG 混合检索 → 展示向量+图谱引用路径',
    },
    {
      label: '🛑 高危操作',
      icon: 'Stopwatch',
      message: '建议对#1主变压器进行停机检修',
      description: '触发 HITL → 展示人工确认对话框',
    },
  ]

  return {
    messages,
    threadId,
    // V1.7.0 M-2：激活会话别名（架构 §7.6 契约——ModelSwitcher/modelStore 依赖此名）
    activeThreadId: threadId,
    loading,
    streaming,
    interruptRequired,
    interruptNode,
    interruptMsg,
    interruptArgs,
    interruptOriginalArgs,
    pendingThreadId,
    hitlBusy,
    hitlSafetyReject,
    healthResults,
    knowledgeAnswers,
    demoShortcuts,
    sendMessage,
    sendMessageBlocking,
    approveHitl,
    rejectHitl,
    approveWithEdit,
    decideHitl,
    resetChat,
    attachContext,
    // v1.5.0 P0-4：暴露 cooldown reactive 状态（Step2 UI 可读）
    lastSendAt,
    isInCooldown,
    cooldownRemainingSec,
    SEND_COOLDOWN_MS,
    // ── M-5 会话管理（T02）──
    sessions,
    archivedSessions,
    sessionsLoading,
    sessionError,
    sessionMessagesCache,
    exportableMessages,
    fetchSessions,
    newSession,
    activateThread,
    guardSwitchSession,
    renameSession,
    archiveSession,
    restoreSession,
    deleteSession,
  }
})
