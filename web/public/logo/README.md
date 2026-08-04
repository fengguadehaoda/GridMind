# GridMind 灵枢电网 · Logo 规范

> 状态：v1.0 · 维护：前端组 · 适用：所有 UI 触点（顶栏 / favicon / 文档 / 分享卡片）

## 1. 文件清单

| 文件 | 用途 | 主题 | 尺寸 |
|---|---|---|---|
| `logo-primary-horizontal.svg` | 顶栏/登录页主用 | 暗底 | 240×56 |
| `logo-primary-horizontal-light.svg` | 顶栏/登录页主用 | 亮底 | 240×56 |
| `logo-primary-vertical.svg` | 海报/PPT 封面 | 通用 | 240×320 |
| `logo-mark.svg` | 简版（仅图形） | 暗底 | 40×40 |
| `logo-mark-light.svg` | 简版（仅图形） | 亮底 | 40×40 |
| `logo-mono-light.svg` | 单色亮版（用于暗底） | 单色 | 40×40 |
| `logo-mono-dark.svg` | 单色暗版（用于亮底） | 单色 | 40×40 |
| `favicon.svg` | 浏览器 tab | 暗底 | 32×32 |
| `favicon-light.svg` | 浏览器 tab（亮主题模式） | 亮底 | 32×32 |
| `apple-touch-icon.svg` | iOS 主屏图标 | 暗底 | 180×180 |

## 2. 颜色规范

### 暗底配色
- 主色（青）：`#00E5FF` (品牌主色)
- 辅色（琥珀）：`#FFB300` (能量/告警/中心节点)
- 弱化主色：`rgba(0, 229, 255, 0.4 ~ 0.6)` (次级描边/节点)
- 背景：透明（深色页面背景透出）

### 亮底配色
- 主色（深青）：`#006978`
- 辅色（橙）：`#FF8F00`

## 3. 图形语义

```
六边形 = 电网/拓扑/控制中心
中心电枢指针 = AI 智能体（核心调度）
4 个外顶点节点 = monitor / diagnosis / rag / planner 四智能体
琥珀色顶点 = 当前活跃智能体（默认 diagnosis）
```

## 4. 字体引用

- 中文：`'PingFang SC', 'Microsoft YaHei', 'Inter', sans-serif`
- 英文：`'Orbitron', 'Inter', sans-serif`

## 5. 导出规范

- **格式**：SVG（矢量、可缩放、可改色）
- **viewBox**：固定，使用时通过 width/height 控制实际尺寸
- **颜色**：直接在 SVG 内固定，不依赖外部 CSS（避免跨域问题）
- **双主题**：提供 dark/light 双套，前端根据 `useThemeStore.theme` 切换 `:src`

## 6. 切勿

- ❌ 修改 `viewBox`
- ❌ 移除六边形结构（核心识别符）
- ❌ 替换为位图 PNG（除非 favicon ICO 兼容需要）
- ❌ 引入新颜色破坏品牌一致性
