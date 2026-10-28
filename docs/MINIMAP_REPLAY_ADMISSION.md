# 小地图旧录像入口的身份门禁与兼容性

2026-10-27。此轮修正旧离线读取入口，不新增预测模型或眼位维度。

## 问题与修改

旧 `replay_alignment.read_replay_samples` 先计算采样正文SHA，再读取manifest身份；旧粗区域基线先读取含未来重现位置的候选，再检查归档身份。已知封存路径和匿名目录内声明的封存比赛都应在关联正文打开前拒绝，不能靠后续行校验补救。

`minimap/offline_io.py` 统一检查已知比赛字段、来源路径和比赛列表，递归深度与访问数量有上限。`replay_alignment.py` 的JSON及SHA读取先检查路径，采样读取先检查manifest身份。`replay_baselines.py` 先检查归档身份再读取目标候选，并继续保留原SHA、采样身份、时钟、可见性和隐藏坐标过滤规则。依赖这些读取器的可见性窗口工具继承此门禁。

不读取真实封存DEM、正文或答案来验证拒收。`tests/test_minimap_replay_admission.py` 使用临时生成数据及公开锁名单中的比赛号，拦截正文打开/计算SHA，验证拒绝顺序；合法生成归档与非比赛统计缓存仍可读取。共8项专项检查。完整584项回归通过，失败、错误、跳过、后台异常均0，回归期间源码未变：`diagnostics/offline-tests/run-87b7ba0a6c4b/result.json`。

## 已有开发资料的实际兼容检查

读取Helper资料前已再次运行 `tools/sync_helper_records.py --latest 3`，结果为已有记录 `ready_with_gaps`，不将此状态当作所有通道完整。这里只读取已明确公开用于开发的9044537452与9044645010旧归档；无新DEM解码、采集、外网或游戏操作。

通过现有 `tools/evaluate_replay_baselines.py` 实际入口及Dota进程门禁，显式传入两份 `full-1hz` 归档、各自v3候选和原本地heroStats缓存；参数、特征与评分定义均未改变。

- 新报告：`diagnostics/minimap-replay-admission/20261026T193711797396Z/baseline.json`。
- 前后来源SHA与比较结果：同目录 `verification.json`；命令及标准输出/错误保留。
- 两局分别418、222个完整候选，共640。整份报告去掉运行耗时后，与原 `coarse-region-baselines-v1.json` 完全相等，不只比较汇总数字。
- 两份manifest、两份采样正文、两份候选、原heroStats和旧报告八个输入文件，前后字节数及SHA均一致。原文件未修改。

这是身份门禁修改后的兼容性证据，不是新增独立验证集，也不是模型提升。原报告的 `training_ready=false`、`visibility_semantics_verified=false`、`runtime_integration=false`、`online_warning_enabled=false` 保持不变。

## 后续边界

原B0/B1结果及局限仍见 `legacy/MINIMAP_REPLAY_BASELINES_20261015.md`：B0保持最后粗格，B1按过去可见速度外推；原始队伍可见位尚非认证的玩家视野，3×3格不是已标定线路，重现时长条件版使用赛后时长。当前不能把640个事件当作线上报警覆盖或准确率，不能用隐藏坐标补特征。

下一阶段需要新实战来源ID与像素/GSI对应证据、独立可见性审核或合格的新资料。门禁与旧基线一致，不足以启用消失预警、认证地图坐标或完成眼位客户端核验。
