2026-11-12 向后兼容的可选己方扩展：原7字段可成对增加 `selected_ally_hero_ids` / `tip_ally_hero_ids` 为9字段格式，不能只提供其中一个。双方各最多5个唯一ID，互不重叠、tip分别属于对应选择；not_selected仅双方都无选择时有效。旧记录的己方字段未知，结果名单用null，不能解释为未选队友。接收端独立检查字段、来源和ready/选择关系；ready状态去重包含双方选择及各自覆盖，但不验证真人分路、像素显示或建议质量。

# 对线页元数据接收与统计

Foresight 在 `panel-visibility.jsonl` 的 v1 行中提供可选 `laning` 扩展。Insights 独立实现读取，不导入助手源码。扩展只描述文字提交与控件状态，不认证屏幕、实际对位、阅读或建议效果。

准入路径保持分开：

- BP 前缀经过 `draft_phase_handoff.open_rows` 的截止字节、包SHA及进出复验，只统计前缀中的扩展。strategy 截止后的对线选择不倒塞入前缀。
- 完整助手会话经过 `helper_handoff` 关闭状态与来源指纹检查，BP组件的可选白名单现包含 `panel-visibility.jsonl`；`ClosedJournals` 进出复验后才交给统计器。记录会话必须与来源目录会话完全相同，不按 UTC 推断所属比赛。

每行仅接受既定可见/展开/laning模式，白名单阶段、合法修订及有效时间。扩展严格接受七个字段：`selected_hero_ids`、`tip_hero_ids`、`advice_status`、`package_sha256`、`rendered_text_sha256`、`viewport_y`、`meaning`。英雄列表最多5位且唯一，提示英雄属于选择子集；仅 ready 可带包指纹与提示覆盖。滚动分数必须有限且在0–1之间，不能作为阅读比例。聊天等额外字段拒收，原始内容不回显。

JSON摘要附带来源行及哈希，分别统计 not_selected/loading/ready/unavailable 的控件状态变化。不同ready文字状态按会话、选择集合、提示覆盖、资料包与文字指纹去重，忽略滚动位置；该数量不是打开次数或阅读次数。资料包指纹仅为声明，本分析不重新认证助手安装资源。每个统计器最多处理4096条扩展，超过时标记预算不足。

无扩展时 `status=not_recorded_unknown`，`unique_ready_text_states=null`。旧记录缺失不能解释为零次使用。非法扩展单独计数，不生成ready证据，不让未知身份或无效状态成为成功。

验证：88项相关离线检查通过；独立生成的闭合BP组件覆盖可选流接收、来源会话不符拒收和来源SHA进出复验。助手生成的四状态模拟JSON通过版本化数据文件交换，输入SHA为 `4a9ba9654313151c6a800f1837ce0445039b214c511f0371e975878c194a5dc9`，四状态均被接收，证据在 `diagnostics/laning-interop-20261111/`。模拟互通不等于自然对局验收。
