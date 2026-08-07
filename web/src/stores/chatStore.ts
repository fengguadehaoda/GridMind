import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { ChatMessage, SseEvent, HealthScoreResult, KnowledgeAnswer, DemoShortcut } from '../types'
import * as api from '../api/chat'
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

  // SSE controller
  let sseController: AbortController | null = null

  // ── Computed ──────────────────────────────
  const lastAssistantMessage = computed(() => {
    for (let i = messages.value.length - 1; i >= 0; i--) {
      if (messages.value[i].role === 'assistant') return messages.value[i]
    }
    return null
  })

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
  }
})
