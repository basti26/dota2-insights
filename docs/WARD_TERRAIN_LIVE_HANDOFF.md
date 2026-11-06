# 眼位底图试用数据与实战记录接收

2026-11-06，用户授权扩展到Foresight接入试看，Insights继续离线调整。

## 数据交接

`tools/export_ward_terrain.py`读取冻结registration.json、地图revision和资产地标清单，核验来源SHA与目标身份，在本仓新目录导出ward_terrain_trial_reference v1（bundle.json、terrain.png、receipt.json、COMPLETE）。只输出数据，不将源码或动态运行时路径放进另一项目的import链。

bundle保留目标MapIdentity、纹理原构建上下文、纹理SHA/尺寸、声明世界窗口、显式raw_offset_xy=-16384、证据SHA与production_ready=false。导出不把旧纹理/未知历史眼位身份认证为当前地图真值。文件仅在本机诊断目录，游戏素材不发布到Git。

本机已将两份数据复制到Foresight/project/diagnostics/ward-terrain-reference-20261106，config/ward-terrain.local.json指向该副本并核对安装地图SHA。正常源码启动即可使用；旧EXE未重建。

## 记录接收

ward_camera_review兼容旧v1/v2闭合包，在location事件可选terrain_registration扩展中验证固定方法、试用标志、独立3×3小地图矩阵、区域与客户区、缓存年龄、点数量/ID/颜色/坐标。过期、越框、声称精度已认证或未知版本拒收。

主画面原world_to_screen和projected_markers保持独立；仅小地图有点时，surface关联允许匹配到对应的小地图投影，不报“没有主画面定位却画点”的伪矛盾。报告增加terrain_registration_counts，不把主画面unlocalized改成主画面成功。配准拟合残差仍不是地面落位误差，OS可见性与真实地图精度保持false。

采集仍由Foresight正常对局触发，不增加用户操作。后续从闭合包复查：失败原因/ROI、缓存来源与年龄、Alt显隐、旧后备采用情况、匹配耗时与主画面/小地图独立表现；已有精确比赛/双会话/GSI来源ID不按UTC猜测补齐。

## 当前离线结果

Foresight73项眼位专项及1732项全量回归通过；Insights989项全量回归通过，均0失败/错误/跳过/后台异常。旧小图嵌入生成2K画面、注入ROI后，真实Foresight配准与WardEvidence写出73点/1图闭合包；独立Insights进程接收通过，精确来源surface关联1条、矛盾0条。

这是生产代码在生成场景中的数据互通，不是游戏/自动ROI/贴地/FPS认证。当前证据diagnostics/ward-terrain-live-20261106/interop.json；Foresight本机diagnostics/ward-terrain-integration-20261106/production-receipt.json保留源段。正式P2/P3继续未完成，未开游戏/联网/提交推送。

## 连续性与重叠诊断

ward_camera_review现在接收可选projection_stability，拒绝错误版本/认证声明、非有限修正量、缺失原始/运动矩阵的成功声明，以及主矩阵与显隐声明不一致。报告按原因计数；同帧本人GSI与画面候选锚点的偏差按terrain和依赖锚点拟合分别列出。原始/应用投影分别记录分布，缺少来源/陈旧/不一致保持不可比较。此项不认证图像身份、脚底、高度或全图精度，旧包没有扩展时兼容并明确计数。新fit_p90_native_px为原图尺寸；fit_p90_target_px为匹配尺寸。双线与收工边界见WARD_OVERLAP_TRACK.md。

## 2026-11-06 连续诊断窗口

此前后台每5秒处理一次，超过连续确认允许的1.2秒间隔，未按Alt时通常无法形成成功连续性证据。现改为每15秒最多三次连续尝试（约0、0.5、1秒），平均12次/分钟与旧背景节奏相同；这些尝试复用原线程，不另开采集。诊断窗口内三帧分别留事件，绕过普通2秒事件限频；Alt显示期间的普通记录仍沿用原限频。图片仍最多每60秒一张，队列2、4096事件/80图/128MiB/2小时上限不变。

sampling_window明确窗口ID、0..2序号、计划3帧、15秒周期和1.2秒最大间隔。捕获失败或超时不补帧、不把离散帧凑成连续；新上下文/参考包/记录器重开窗口。projection_stability增加previous_captured_monotonic，明确运动矩阵对应的前帧。只在底图已获得主画面投影且附近没有历史点时，额外提取同帧本人候选锚点供偏差核查，不据此改变投影。

Insights只比较同一窗口相邻序号、严格递增且间隔<=1.2秒、同比赛/双会话/参考摘要/尺寸/方法，并且前帧单调时间精确对应运动矩阵的记录。报告完整/不完整窗口、未测原因、共有眼位屏幕位移、扣除背景运动后的原始模型与应用模型偏差。相同窗口重复序号拒收；旧包没有窗口继续兼容并明确缺失。切屏、无共享点、缺前帧或无背景运动保持未测，不补零。

应用模型本身受运动补偿约束，接近零的偏差不能证明落点正确；因此同时报告原始模型偏差。本人候选锚点和高度仍无独立真值，complete_overlap_verified保持false。三帧窗口也不能代表全局连续覆盖或跳动发生率。

本批117项Foresight眼位/桌面检查通过run-e70e10ee32d6；266项Insights地图/眼位检查通过run-742a796c68d1，均0失败/错误/跳过/后台异常、源码稳定。真实工作线程的假设备测试确认不按Alt也可记录连续三帧并得到确认投影。

生成画面的两份真实WardEvidence闭合包由独立Insights进程接收：一份纹理不足，三帧均未确认且误差为空；另一份生成地形三帧中先隐藏、后两帧确认，可测一组相邻眼位。平面矩阵人为注入，未采屏/游戏，不能当作实战或世界贴地验证。证据Foresight diagnostics/ward-window-integration-20261106与Insights diagnostics/ward-window-review-20261106/interop.json。
