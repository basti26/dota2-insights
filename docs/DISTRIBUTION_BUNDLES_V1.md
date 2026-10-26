# 规则包与统计包交付契约 v1

用途：Insights向Foresight交付离线数据，不共享源码路径。规则与统计是两个独立包，不合并成一个“全部验证通过”开关。已提供生产端、离线检查器和独立纯JSON校验适配器；Foresight现已实现独立离线验收/显式原生导入入口，默认不会安装或激活数据。

## 文件与完整性

每个新目录只含manifest.json、payload.json、COMPLETE。目录已存在则拒绝覆盖；COMPLETE最后写入，缺少或不匹配即拒收。未完成目录作为失败产物保留，不算交付。

manifest固定字段：

- schema_version=1、kind=insights_distribution_bundle；artifact_kind为statistics或rules。
- package_id：去掉自身字段后，按UTF-8、排序键、无额外空格、非有限数字禁止的规范JSON计算SHA256。
- native_format：draft_statistics_v1，或draft_capability_rules_v1/v2，必须与正文原生版本一致。
- created_at_utc：UTC；源下载/复核时间不能晚于制包时间。
- payload：固定文件名payload.json、字节数、正文SHA256、原生内部sha256、原输入文档字节SHA256。不带本机原文件路径。
- applicability：从原生正文推导，不能由清单另填更有利的范围。含语义版本、统计筛选/规则标准、覆盖起止、版本证据状态和资源构建（没有则null）。
- validation：原生结构passed、原始来源字节绑定not_performed_by_transport、语义准确性not_certified、synthetic标志及automatic_activation=false。不得自行提高认证级别。
- generator：工具名称和源码SHA256。

检查器限制清单256KiB、正文16MiB、规则输入4MiB，拒绝重复JSON键、NaN/Infinity、未知字段、布尔值冒充版本或样本数、相对路径逃逸和指向包目录外的文件；封存路径在读取前拒收。SHA用于完整性和追溯，不是可信发行者签名，也不证明统计结论或规则内容正确。

## 原生正文与兼容

统计正文保留Foresight现有cache schema v1，不重命名字段、不重算delta。必须保留provider、固定官方source_url、覆盖/下载/更新UTC、scope/pair_scope、英雄样本及relations。双方筛选必须完全相同，样本/胜场合法，关系不重复，有限delta在-1..1，内部校验和正确。STRATZ角色标签仍只是正文中的来源标签，不能转换成已审核技能机制规则。

规则正文保留现有capability schema v1/v2，包括source_build、语义版本证据、标准、来源SHA、英雄逐项yes/no/unknown及内部校验和。未审核保留unknown。资源client_version/source_revision不能替代applies_to_game_version；非未知机制判断必须有符合原生契约的来源种类、技能ID和复核范围。

规则来源可另外执行tools/audit_rule_sources.py：按本机sources.local.json逐一读取声明来源，单文件64MiB、累计512MiB上限，检查前后稳定性及SHA。报告只写符号来源ID、SHA和字节数，不复制原始资源、本机路径或凭据。缺文件、缺绑定和SHA不符分别保留；全通过也不认证语义补丁或机制准确性。inspect的可选--rule-source-bindings会重新执行该核对，输出独立source_bytes_verified，不改包内发布时声明，不沿用旧报告作永久凭证。

纯校验器已提取到dota2_insights/contracts，不导入Foresight。来源指纹与改编范围见native-draft-contract-origins.json，首次拆仓迁移清单不回写。Foresight接收器独立校验清单后，仍运行它自己的原生校验与导入策略，不能因为传输层通过就跳过。统计新鲜度按接收端明确配置检查，不能把生产端检查时间或结果当永久凭证。

## 检查状态

inspect返回transport_verified、compatibility_status、activation_allowed=false、native_consumer_validation_required=true。

| 状态 | 含义 |
|---|---|
| synthetic_not_for_live_use | 测试包，任何情况下不进入实战 |
| package_clock_skew | 制包时间在检查时间之后 |
| semantic_version_unverified | 正文或调用方缺少语义版本，不能用构建号猜 |
| semantic_version_mismatch | 版本不匹配 |
| scope_mismatch | 模式/段位/地区筛选不匹配 |
| coverage_clock_skew | 统计覆盖期在当前时间之后 |
| stale_statistics | 覆盖期结束时间超龄，默认24小时，可显式设置阈值 |
| eligible_for_native_consumer_validation | 统计的传输、版本/筛选/新鲜度可继续送原生校验；不是统计质量或启用认证 |
| source_binding_required_before_native_consumer_validation | 规则仍需原始来源字节绑定，不由传输层补认证 |

结构、校验和、路径或正文不符直接报错，没有兼容成功结果。不会联网刷新过期缓存，不修改已有缓存，不给规则补补丁版本。

## 命令

```powershell
python tools/distribution_bundle.py publish --kind statistics --source <现有统计JSON> --output diagnostics/<新统计包>
python tools/distribution_bundle.py publish --kind rules --source <现有规则JSON> --output diagnostics/<新规则包>
python tools/distribution_bundle.py inspect --package diagnostics/<包> --game-version <已确认补丁> --game-mode <模式> --rank <段位> --region <地区>
python tools/audit_rule_sources.py --rules <现有规则JSON> --bindings <本机来源绑定清单>
python tools/distribution_bundle.py inspect --package diagnostics/<规则包> --game-version <已确认补丁> --rule-source-bindings <本机来源绑定清单>
```

规则检查可省略统计筛选参数。CLI不提供合成包发布开关，不下载、不向另一个仓库写入、不启动游戏。实际产物只留本机diagnostics。

当前实际统计正文覆盖结束2026-10-08，检查为stale_statistics；现有规则applies_to_game_version=null，检查为semantic_version_unverified。为了导出而放宽新鲜度、抄构建号补版本或宣称已接入实战均未执行。

本机现存130项规则来源全部与声明SHA相同，source_bytes_verified=true，但semantic_version_unverified保持。实际最终格式包在diagnostics/distribution-20261026/statistics-final-v1和rules-final-v1；更早的内部格式试制目录保留，不作为最终v1交付。来源核对报告在diagnostics/rule-source-audit/20261026T062521388347Z/report.json。后续Foresight接收器代码已补入；真实旧包仍未安装或更新旧统计缓存。

两仓库的独立命令交接正例使用生成算术夹具，语义版本fixture-only，输出仅Foresight诊断目录。既有DraftService._load_advisor能够读取导入后的原生文件，不启动观察/模型。它不是实际STRATZ数据或游戏验收。正式缓存/规则SHA仍与制包前相同；证据在Foresight diagnostics/insights-delivery-20261026/REPORT.md。接收端使用自己的校验器，没有导入本仓库源码。

验证：最终320项完整离线回归通过（run-3d077f7b0c74），包括原生v1/v2、合成包拒收、混合筛选、样本非法、换包/换正文、版本标签冲突、未来时间、过期/版本/筛选不匹配、来源缺失/变更和中断不发布COMPLETE；无失败/错误/跳过或后台异常。实际检查在diagnostics/distribution-20261026/verification-v1.json及REPORT.md，7.41f仅为旧缓存的声明版本，不冒充本轮确认当前游戏补丁。
