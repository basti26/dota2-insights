# 单局性能采集交接 v1

Foresight负责PresentMon采集器生命周期，Insights只独立读取数据；不跨仓库导入源码。开关仍使用现有帧率记录配置，未开启不创建监督线程或采集器。本协议不更换PresentMon版本、采样频率或帧率算法。

Windows观察首次仍记录菜单/选人。发现新鲜、明确双会话后绑定范围；同范围POST_GAME到来，监督线程先关闭自有采集器，核实最终退出码、资源关闭、清单发布及ETW诊断，再封存。经济、GSI等其他通道继续运行。下一范围开始新采集器，旧目录不再写。空公开比赛号保留null，不能从旧BP编号回填。

缺赛后阶段、身份跳转或采集器提前结束不能伪造单局完成。停止失败、清单发布失败或限额停止均保留失败状态。监督线程处理停止和SHA，GUI及GSI回调不等待这些磁盘操作。

## 目录与清单

`game-frames/run_<32hex>/closed-match/record-handoff.json` 和最后发布的 `COMPLETE`。标记为清单原字节SHA256加单个LF。原文件仍在capture根目录，不复制CSV或日志。

清单schema_version=1、kind=foresight_performance_match_handoff，包含capture_run_id、scope（match_id可空、game_session_id、record_session_id）、end_state、原end_provider_at_utc、封存UTC、覆盖边界、明确collector_closed_before_publication，以及固定四源的字节数和SHA：capture.json、frames.csv、context.jsonl、collector.log。不包含配置、凭据或截图。封存时间不是游戏帧时间。

## 独立读取

```powershell
C:\Python312\python.exe tools/review_performance_match.py <closed-match目录> --expected-package-id <清单SHA>
```

输出位于本仓库diagnostics/performance-match-review的新目录。先从小清单检查公开封存锁，再读正文；拒绝链接、身份冲突、非精确COMPLETE、预算越界和来源变动。进入/退出均复验，读取期间变动不发布成功报告。即使capture.json否认丢帧，collector.log出现ETW丢失或权限失败也拒绝可用统计。

复用原ContextDecoder、game_windows、read_presentmon_csv，只接受同比赛/双会话且新鲜、游戏前台的相邻QPC窗口。提交帧与显示帧分开，不按文件接收时间或UTC邻近匹配。不足上下文保持未知；空编号只用于明确本地双会话。不声明整局覆盖、实际游戏性能验收或助手造成掉帧。

显式CLI仍可用。自动发现、独立持久索引及日常联合报告也已接入，按下节流程执行；旧启动器闭合性能报告保留原入口。

## 自动接收与日常报告

在Insights运行 `C:\Python312\python.exe tools/review_recordings.py --latest 5`。先同步所有通道，再消费已复验来源并生成联合报告；不启动游戏、采集器或外网查询。分别执行时仍可先tools/sync_helper_records.py，再tools/review_closed_record_content.py。

performance_index只扫描已配置源码历史的game-frames/run_*/closed-match，以及既有helper-/economy-便携版的固定diagnostics布局。最多100个发布目录、10000候选、128个所选包；mtime仅用于选择最近目录，不充当游戏时间。清单SHA固定在独立diagnostics/performance-match-handoff/index.json，不复制CSV、日志、配置或凭据。同采集ID的相同清单副本合并；同采集ID出现不同有效清单时，整组阻止。

每次索引的来源预留预算为2×1040MiB，包含四源，失败尝试也消耗其预留；进入正文前核对完整清单及声明大小，读取以声明大小为界。报告阶段重新核对索引成本和包SHA，缩小伪造成本不能绕过原文件预算。预算/缺标记/来源冲突均列不可用，不作为零FPS。

联合JSON分别保存旧performance_reports与新performance_match_reports，分别报告旧窗口数和新包数，不相加。使用明确采集ID、公开比赛号/双会话以及已核验CSV和context的SHA判断重合，即便便携副本路径不同也标记不可新增计数；同路径重合另列。只在非空公开比赛号及两个运行会话完全相同时关联原生GSI包，不以UTC猜测。

实际自动链生成验证：diagnostics/performance-match-auto-interop/20261026T215825010546Z/verification.json。生产进程确认仍存活时，独立解释器调用真实日常run入口，自动发现并报告首包；第二局后自动索引/报告两包，第一包来源SHA不变，10份生产/消费源码前后一致。未借用另一仓库运行时导入，没有真实ETW/游戏、网络或正式历史配置变更。

最终658项完整离线回归通过：diagnostics/offline-tests/run-30049df45361/result.json，失败/错误/跳过/后台异常均0，289份源码指纹复查一致。新增13项覆盖源码/便携副本、冲突、索引重标/来源变化、原子失败、预算、空编号、旧范围跨路径重合和精确GSI范围关联。真实PresentMon轮换时延/开销、切局时序及整局覆盖仍待验收。

生产监督仍受整次观察CSV容量、4小时和128范围上限约束，不因轮换重新计预算。双进程生成资料证据：diagnostics/performance-match-interop/20261026T214922968785Z/verification.json。生产Python进程存活时，两次独立CLI均读取到已知2秒QPC窗口和显示FPS=2；第二局后第一包全部来源SHA不变，8份生产/消费源码前后不变。使用生成采集器及真实最终清单发布路径，不是ETW、实际游戏或整局覆盖验证。助手最终1609项完整离线回归通过run-7679bcff4318。

Insights完整645项离线回归通过：diagnostics/offline-tests/run-245fb3ab3737/result.json，无失败/错误/跳过/后台异常，源码在回归中未变。新增10项覆盖已知数值、独立检查ETW诊断、空编号、封存身份先于正文、源变更、上下文尾部/身份/时钟门禁和精确标记。首次专项的换行/缺标记断言问题保留在run-114bdd81315e、run-361d5b4d4cab；修正生成夹具为生产端的LF字节标记及允许文件缺失错误，没有放宽生产校验。
