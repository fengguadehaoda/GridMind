# 常见问题 FAQ

面向三类用户角色：**调度员**（每日盯盘）、**运维工程师**（部署排查）、**开发者**（集成调试）。

## 1. 调度员

**Q：如何快速切换视图？**
按 `⌘K` / `Ctrl+K` 打开命令面板，输入「监控」或「jk」即可直达实时监控；也可按 `⌘1-5` 直达 5 个核心路由。

**Q：AI 推理卡住 / 出错怎么办？**
查看 Header 的 Session 徽标：红色 = 运行异常。点击徽标打开详情抽屉，定位当前步骤并可从历史 checkpoint 回滚。

**Q：高危操作一定要审批吗？**
是。涉及停机 / 检修 / 紧急操作均触发 HITL 弹窗，必须人工确认；审批记录可在 `/audit` 追溯。

## 2. 运维工程师

**Q：灰度切流需要什么权限？**
需要管理员令牌（`X-Admin-Token`），在灰度面板「手动切流」卡片输入。

**Q：灰度异常如何恢复？**
系统会自动回滚（监控窗口错误率 / 延迟超阈值）；也可在面板手动回滚。

**Q：帮助文档如何更新？**
帮助文档为内置精选集，随前端发版更新；新增内容请走 `docs/help-src/` + `manifest.json` 白名单流程。

## 3. 开发者

**Q：SSE 事件流有哪些类型？**
`token / done / error / heartbeat / step_started / step_completed / step_failed / reasoning_paused / reasoning_resumed / reasoning_completed / reasoning_error / step_replaced / hitl_interrupt / hitl_resolved`。

**Q：Session 状态机？**
8 态：`idle / running / paused / editing / resuming / completed / error / aborted`；徽标 4 态由 8 态聚合派生。

**Q：命令面板如何新增命令？**
在 `composables/useCommands.ts` 注册 `CommandItem`（id 全局唯一、group 必填、keywords 含中文 / 拼音首字母 / 英文），禁止在组件内写死命令。

## 4. 通用

**Q：页面布局在小屏错乱？**
支持最低 1024×768；1024-1280 紧凑断点下导航折叠为汉堡菜单、图表自动换列。

**Q：快捷键冲突怎么办？**
全局快捷键统一走注册中心，ESC 按优先级仲裁：命令面板 > 速查浮层 > Session 抽屉 > 其他。
