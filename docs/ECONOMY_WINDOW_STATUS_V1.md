# 经济HUD窗口请求元数据

Foresight 的可选扩展位于闭合运行索引 `recorders.json`。它记录程序走到的窗口放置分支，不证明像素显示、可读性、点击或阅读，也不替代本人GSI金额核对。

## 准入

先执行既有 Helper 同步；只有 `helper_handoff.verify_package` 准入的闭合包可以分析。`ClosedJournals.registry()` 只读包中固定的 `source_registry`，检查路径、512 KiB大小上限和原字节SHA；整个分析范围进入、退出均重验来源。不能因本人GSI通道已准入而直接打开其他来源，不能把仍在写的运行索引当闭合记录。

## 字段

- `economy_display_transitions`：1至128个按状态变化去重的条目。
- 每条仅包含 `reason`、布尔 `window_requested`、可空的明确 `match_id`、UTC `observed_at_utc`。
- 原因枚举：`waiting`、`user_hidden`、`gsi_waiting`、`gsi_stale`、`game_not_started`、`clock_missing`、`clock_before_30s`、`window_unavailable`、`game_not_foreground`、`game_minimized`、`placement_requested`、`preview`。
- 只有 `placement_requested` / `preview` 的 `window_requested` 为真。
- `economy_display` 可选，表示最新状态，另含固定 `scope=window_request_only_not_pixel_visibility`。
- `economy_display_history_limited` 可选布尔，缺省假；为真必须已有128条历史，此后最新状态可以不同于最后保留的历史。

相邻历史不能是相同状态及比赛编号，时间不得倒退，所有时间必须在已验证运行起止范围内。最新状态不得早于历史；未达到上限时应与历史末项状态相同。额外字段、未知原因、伪布尔、外来比赛号、范围外时间或矛盾状态拒绝解析，不能静默归为正常。

明确比赛号先检查封存锁，再要求它属于已准入包的明确比赛集合。编号空值保持未归属，不利用UTC或附近GSI行补编号。

## 结果边界

`record_content_review` 输出 `economy_window_requests`，包括原因变化计数、明确比赛分组、未归属计数、最新状态及上限标记。Markdown 同时保留本人GSI金额可用行数和窗口状态信息。二者不能当作同帧对照或因果证明。

没有扩展时输出 `not_recorded_unknown`，变化总数为null；不能当作零次显示或显示失败。变化数不是显示时长、帧数、识别次数或用户使用次数。时间是启动器观察状态的时刻，不是游戏帧或精确窗口呈现时刻。

实现为Insights独立解析器，不导入Foresight源码。合成互操作样本使用当前Foresight写入器生成，再以版本化JSON和SHA复制核验；它只证明协议兼容，不能替代自然对局的闭合记录验收。
