<script setup lang="ts">
/**
 * KnowledgeUpload.vue · 用户上传知识库管理组件（V1.7 · KB Upload）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构 kb-upload-architecture-2026-08-06 §3.2（KnowledgeUploadVue）：
 *   - 拖拽 / 选择区（el-upload-dragger，多选）+ 校验即时提示
 *   - 每文件进度条（传输阶段 0-100；解析入库阶段「正在解析入库…」）
 *   - 成功 / 失败结果反馈（可读文案，失败可重试）
 *   - 文档列表表格（文件名 / 大小 / 上传时间 / 知识片段数 / 删除二次确认）
 *
 * 类图方法契约：beforeUpload / onUploadSuccess / onUploadError / confirmDelete。
 * （类图中的 viewMode 由 HelpCenter 的 Tab 切换承担，本组件仅负责 KB 管理面。）
 *
 * 作者：寇豆码（工程师）
 */
import { computed, onMounted, onUnmounted } from 'vue'
import { Delete, FolderOpened, UploadFilled, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile } from 'element-plus'
import { useKnowledgeUploadStore } from '@/stores/knowledgeUpload'
import type { KbUploadItem, UploadResponse } from '@/types/knowledgeUpload'
// M-5 T05 · AC3-3：上传/删除按钮仅 kb_admin/admin 显示（读列表全员保留）
import { getJwtRole } from '@/composables/useJwtAuth'

const store = useKnowledgeUploadStore()

/**
 * M-5 T05 · canManageKb = role ∈ {kb_admin, admin}。
 * 控制上传区 + 删除列显隐；列表/检索/刷新全员保留（AC3-3）。
 * 注：前端仅展示层 UX，安全由后端 require_role(KB_ADMIN, ADMIN) 兜底（403）。
 */
const canManageKb = computed<boolean>(() => {
  const role = getJwtRole()
  return role === 'kb_admin' || role === 'admin'
})

/** 允许的扩展名白名单（与后端 ALLOWED_EXT 对齐） */
const ALLOWED_EXT = ['.txt', '.md', '.pdf']
/** 单文件大小上限（与后端 MAX_FILE_BYTES 对齐） */
const MAX_FILE_BYTES = 5 * 1024 * 1024

/** 浏览器侧校验：格式 / 大小不符即时提示，符合才放行（PRD §4.2 第 2 条） */
function beforeUpload(file: File): boolean {
  const name = file.name || ''
  const ext = name.includes('.') ? `.${name.split('.').pop()!.toLowerCase()}` : ''
  if (!ALLOWED_EXT.includes(ext)) {
    ElMessage.error('仅支持 txt / md / pdf 文件')
    return false
  }
  if (file.size > MAX_FILE_BYTES) {
    ElMessage.error('文件大小不能超过 5MB')
    return false
  }
  return true
}

/** el-upload on-change：校验通过后逐个上传 */
function handleFileChange(fileItem: UploadFile): void {
  if (fileItem.status !== 'ready') return
  const raw = fileItem.raw
  if (!raw) return
  if (!beforeUpload(raw)) {
    // 校验失败的文件从 el-upload 内部列表移除（:show-file-list=false 无痕）
    return
  }
  void doUpload(raw)
}

/** 单文件上传：进度回调由 store 维护（uploading[文件名]） */
async function doUpload(file: File): Promise<void> {
  try {
    const resp = await store.upload(file, undefined, () => {
      /* 进度实时写入 store.uploading，模板直接渲染 */
    })
    onUploadSuccess(resp)
  } catch (err) {
    onUploadError(err instanceof Error ? err.message : String(err))
  }
}

/** 成功反馈：PRD §4.2 成功文案 */
function onUploadSuccess(resp: UploadResponse): void {
  ElMessage.success(
    `《${resp.filename}》已入库，共 ${resp.chunk_count} 个知识片段，` +
      '现在可以在对话中提问相关规程。',
  )
}

/** 失败反馈：展示可读错误文案（禁止静默失败，PRD 验收 2） */
function onUploadError(err: string): void {
  ElMessage.error(err || '上传失败，请重试')
}

/** 删除二次确认（PRD §4.3 / 架构 §4.3）：确认后调 store.remove */
function confirmDelete(item: KbUploadItem): void {
  void ElMessageBox.confirm(
    '删除后该文档的知识将无法在对话中检索，确定删除？',
    '确认删除',
    {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    },
  )
    .then(async () => {
      await store.remove(item.doc_id)
      ElMessage.success(`《${item.filename}》已删除`)
    })
    .catch(() => {
      /* 用户取消，静默 */
    })
}

onMounted(() => {
  void store.fetchUploads()
})

onUnmounted(() => {
  store.clearAllUploading()
})
</script>

<template>
  <div class="kb-upload" data-test="kb-upload">
    <!-- ── 上传区（M-5 T05：仅 kb_admin/admin 可见；读列表全员保留）── -->
    <el-upload
      v-if="canManageKb"
      class="kb-upload__picker"
      drag
      multiple
      :auto-upload="false"
      :show-file-list="false"
      accept=".txt,.md,.pdf"
      :on-change="handleFileChange"
      data-test="kb-upload-dropzone"
    >
      <el-icon class="kb-upload__drop-icon"><UploadFilled /></el-icon>
      <div class="kb-upload__drop-text">
        点击或拖拽文件到此处上传
      </div>
      <div class="kb-upload__drop-hint">
        支持 .txt / .md / .pdf，单文件不超过 5MB，可多选
      </div>
    </el-upload>

    <!-- ── 上传中进度（含解析入库阶段）── -->
    <div
      v-if="Object.keys(store.uploading).length > 0"
      class="kb-upload__progress-list"
      data-test="kb-upload-progress"
    >
      <div
        v-for="entry in Object.values(store.uploading)"
        :key="entry.filename"
        class="kb-upload__progress-item"
      >
        <span class="kb-upload__progress-name">{{ entry.filename }}</span>
        <el-progress
          :percentage="entry.percent"
          :status="entry.status === 'error' ? 'exception' : undefined"
          :stroke-width="10"
        />
        <span
          v-if="entry.status === 'uploading' && entry.percent >= 100"
          class="kb-upload__progress-phase"
        >
          正在解析入库…
        </span>
        <span
          v-else-if="entry.status === 'error'"
          class="kb-upload__progress-phase is-error"
        >
          {{ entry.error || '上传失败' }}
        </span>
      </div>
    </div>

    <!-- ── 文档列表 ── -->
    <div class="kb-upload__list">
      <div class="kb-upload__list-head">
        <span class="kb-upload__list-title">
          <el-icon><FolderOpened /></el-icon>
          已上传文档（{{ store.items.length }}）
        </span>
        <el-button
          size="small"
          text
          :icon="Refresh"
          :loading="store.loading"
          @click="store.fetchUploads()"
        >
          刷新
        </el-button>
      </div>

      <el-table
        v-loading="store.loading"
        :data="store.items"
        empty-text="暂无上传文档"
        data-test="kb-upload-table"
      >
        <el-table-column prop="filename" label="文件名" min-width="220" show-overflow-tooltip />
        <el-table-column label="大小" width="110" align="right">
          <template #default="{ row }">
            {{ store.formatSize(row.size_bytes) }}
          </template>
        </el-table-column>
        <el-table-column prop="uploaded_at" label="上传时间" width="170" show-overflow-tooltip />
        <el-table-column label="知识片段数" width="110" align="center">
          <template #default="{ row }">
            <el-tag size="small" type="success">{{ row.chunk_count }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default>
            <el-tag size="small">已入库</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" align="center">
          <template #default="{ row }">
            <el-button
              v-if="canManageKb"
              size="small"
              type="danger"
              text
              :icon="Delete"
              data-test="kb-upload-delete"
              @click="confirmDelete(row as KbUploadItem)"
            >
              删除
            </el-button>
            <span v-else class="kb-upload__readonly-hint" data-test="kb-upload-readonly">只读</span>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<style scoped lang="scss">
.kb-upload {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

/* ── 拖拽上传区 ── */
.kb-upload__picker {
  width: 100%;

  :deep(.el-upload-dragger) {
    padding: var(--space-8) var(--space-4);
    background: var(--bg-card);
    border: 1px dashed var(--border-strong);
    border-radius: var(--radius-md);
    transition: all var(--dur-fast) var(--ease-out-quint);

    &:hover {
      border-color: var(--brand-primary);
      background: var(--brand-primary-soft);
    }
  }
}

.kb-upload__drop-icon {
  font-size: 40px;
  color: var(--brand-primary);
  margin-bottom: var(--space-2);
}

.kb-upload__drop-text {
  font-family: var(--font-cn);
  font-size: var(--fs-md);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}

.kb-upload__drop-hint {
  margin-top: 4px;
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

/* ── 上传中进度 ── */
.kb-upload__progress-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  border: 1px solid var(--border-muted);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
}

.kb-upload__progress-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.kb-upload__progress-name {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-upload__progress-phase {
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--brand-primary);

  &.is-error {
    color: var(--status-danger);
  }
}

/* ── 文档列表 ── */
.kb-upload__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.kb-upload__list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.kb-upload__list-title {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);

  .el-icon {
    color: var(--brand-primary);
  }
}

/* M-5 T05：非管理角色删除列只读提示 */
.kb-upload__readonly-hint {
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}
</style>
