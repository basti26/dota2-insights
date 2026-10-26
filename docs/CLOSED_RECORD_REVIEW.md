# 闭合记录内容复核

先执行接收，再分析选中的闭合包；不打开游戏、不采屏/录音、不联网，不自动解析全部录像。

```powershell
python tools/sync_helper_records.py --latest 3
python tools/review_closed_record_content.py
python tools/prepare_record_item_review.py --report diagnostics/record-content/<本次目录>/report.json
```

内容报告写入diagnostics/record-content/<UTC目录>，装备裁片写入diagnostics/item-priority-review/<UTC目录>。原历史只读；新输出不进入Git。两个项目之间没有源码导入。

## 身份与时间

消费前、消费后重验交接包；JSONL逐行同时核对字节数/SHA、完整尾行和上限。禁止未闭合通道，拒收外来比赛、嵌套clock_context身份冲突、未知会话与封存比赛。不把旧索引当持续读许可。

共享资料分成显式比赛号、显式会话/比赛目录、UTC候选和未归属四类。UTC候选必须被同场有效本人GSI接收时间夹住，两个锚点间隔不超过5秒，提供者到接收延迟为0–5秒；只用PRE_GAME/GAME_IN_PROGRESS。跨局、相同时间有多个比赛、来源UTC异常、无锚点或过大间隔均不归属。JSON保留候选行段、原时间字段与范围，可定位原日志；不把这些候选升级为比赛事实、英雄身份或在线训练特征。

## 经济、BP与大件

本人金额保留原GSI派生值；金额下降不平滑、不自动当作识别错误。只有现金/物品/升级分项都可用时才核对加总，reported净资产不拿部分库存组件比较。partial/estimated/reported等原状态分别计数；缺项和未知不补零。视觉识别的accepted、已知格、未知格只是日志统计，不是准确率。

BP事件和UI应用回执分开统计；只统计selection阶段且候选非空的UI回执，不把赛后候选或算法计算次数称选人期已显示，也不据此认证推荐质量。

大件队列以带SHA的本地物品目录为排序参考，目录价格不代表当前补丁认证。优先挑目录价≥2000的未知候选和已知但匹配分差<0.12的记录，合并同场/英雄/格/候选/原因的重复观测，保留首尾日志行及行SHA；不更改在线阈值。最多保留60组展示，原始重复数量不是独立错误数。

裁片工具必须从原日志重新取得图片SHA、保存图像宽高及客户端origin，按原slot矩形减去origin裁剪；缺少几何不猜位置。路径只允许该观测目录内绑定的hud-NNNNNN.png，越界/换图/缺图保持不可用。不把已准备裁片等同视觉验收通过；contact.png用于集中查看，原生裁片单独保留。

## 原生逐局装备包的同一复查流程

新旧记录现共用ItemReviewPriorities，保持原先≥2000目录价格和<0.12分差的离线挑样口径；不改线上识别阈值。原生包在open_rows进入/退出复验内按原始JSONL字节生成行SHA，绑定该包声明的正文SHA、明确比赛/英雄/格/候选，保留首尾证据。统一内容报告的equipment_reports包含item_review_priorities和catalogue_source；没有传入目录/来源的单包计数接口将priority_inputs_available=false，不把缺输入当没有可疑大件。

tools/prepare_record_item_review.py继续接受统一report.json，现在同时处理旧launcher包与原生装备包。原生部分必须出现在当前独立equipment-handoff索引，固定其包SHA，重新从正文取得行、候选、图像绑定和几何；不能用GSI包或旧报告授权。它在输出目录下用equipment-<完整package_id>区分，成功清单kind=native_item_review_evidence。相同首尾行只裁一次，不同首尾行各保留原图，不用新截图替换老记录。

共享裁片代码也额外检查队列的候选名与原槽位的item_id/candidate一致，防止被修改的报告给原图贴错误候选标签。行SHA、源、英雄或候选矛盾则不生成该包成功清单；缺图片、图片SHA错误或缺几何等只标不可用，不猜坐标。不允许把原生裁片输出写回历史根。没有手工确认时visual_verdicts_entered=false，准备好图片不等于物品身份正确。

400项完整离线回归通过（run-ca170c535b07），无失败/错误/跳过/后台异常，源码未在回归中改变。新专项用颜色PNG核对客户端origin偏移与精确裁片像素、两个端点分别保留、改图/伪造行SHA/候选/源的拒收，以及缺几何不猜位置。真实Foresight写入器另生成一份合成观测，通过独立Insights进程自动索引→报告挑样→裁片，裁出20×10蓝色区域；证据diagnostics/native-item-crops-interop-20261026/verification.json。只是像素几何/数据交接验证，不是实际英雄或装备识别认证。

正式本机资料也已重跑统一内容报告及裁片流程，仍为9组、14张裁片全部准备成功，未修改原资料、金额、标签或规则。新输出见diagnostics/item-pipeline-verification-20261026/verification.json、diagnostics/record-content/20261026T084420321112Z/及diagnostics/item-priority-review/20261026T084420347542Z/。本次查看了一张BKB复查页确认构图仍对应原物品格，但没有重新给全部样本做真值标签，也没有宣称99%准确率。

## 本轮实际资料

最近三份启动记录覆盖四场，已形成闭合日志内容报告及9组大件的14张首尾裁片。本人英雄名下视觉装备观测均为0，金额分项可比记录中没有发现加总不一致；这只证明日志链的部分一致性，不证明屏幕呈现或完整净资产。

实图审阅已看到：低分差的BKB、分身斧、祭品图标可辨；冷却遮罩下散失候选被保留unknown；死灵法师三张被拒收图标的莲花/达贡候选没有得到画面支持，不能把这些候选称已入账错误。影魔背包两张仍留无法判断，不替它编物品身份。具体比赛号、逐图观察和SHA只写本机证据目录。

地图独立认证、音频收尾矛盾、实际听审及录像同刻真值仍未因此通过。

最终296项完整离线回归通过（run-a5a035b2faa3），失败/错误/跳过/后台异常均0，回归期间源码未变；git diff --check通过。另补了实际解析路径的封存门禁：文件名正常但最终解析路径落入封存比赛时，必须在哈希、正文读取或图片解码前拒收；合成哨兵测试验证没有读取其正文。已退出复验上下文的流不能继续读取。
