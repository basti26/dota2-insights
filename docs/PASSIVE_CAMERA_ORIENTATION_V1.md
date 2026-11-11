# 被动镜头方向候选

本扩展仅消费已闭合 `ward_camera_evidence` 的版本2记录，不执行游戏命令，也不启用实时投影。原有 `native_camera_position` 与快照格式保持不变。

## 输入

Foresight 被动读到相邻三行时，保留位置事件，再追加一个 `native_camera_orientation` 事件：

1. `Camera position: X Y Z`
2. `Camera position Look-At Point is: X Y`
3. `Camera look-at position: X Y Z`

允许三行跨文件读取片段，但不跨日志代次、Alt按住范围或前台/会话范围。插入其他行、缺失第三行、非有限数字或两份目标XY相差超过0.1世界单位时，不生成方向候选。旧的单命令绑定仍只产生位置事件；本扩展不修改绑定、启动项或查询触发方式。启动时镜头查询仍为已撤回方案。

## 事件

外层 `projection_applied=false`、`frame_id=null`、`capture_frame_alignment_verified=false`。

`telemetry.kind=alt_camera_orientation_candidate_v1` 包含：

- `position_candidate`：原有完整 `alt_camera_position_v1` 接收记录，含相应日志代次、Alt epoch、单调接收时刻、字节读取范围与摘要。
- `look_at_xyz`：第三行报告的目标三维坐标。
- `orientation_candidate`：由 camera→target 向量算出的距离、单位方向、俯仰角和水平朝向；roll、FOV和viewport仍为空，projection_calibrated仍为false。
- 本次第三行的接收凭据，与位置接收记录保持同一代次/epoch，单调时刻和读取范围不倒退。
- `atomic_pair_verified`、`request_association_verified`、`projection_calibrated`、`live_usable`、`exact_log_flush_latency_verified`、`game_control_sent`、`capture_frame_alignment_verified`全部为false，`data_time_utc`为空。

两个查询不是原子采样。即使XY相符，也不能排除其间镜头高度等变化；日志出现时刻不是引擎采样时刻。旧位置快照的pitch/Z仍为空，方向候选不替换其内容、不刷新其年龄、不额外触发截图。位置和方向事件共享原每次运行128条镜头事件预算。

## 独立分析

`ward_camera_review.review` 先按原协议准入闭合包、检查封存身份及全部文件大小/SHA，读取结束再复验。`native_camera_review.validate_orientation` 独立检查来源字段、时刻差、代次、epoch、读取范围、目标XY一致性，并重新计算全部几何量；缺字段、额外字段、几何矛盾或未经验证的几何/同步声明拒收。

报告单列 `orientation_candidate_events`，不把它加到位置数，也不把这类候选记为已认证完整镜头。`complete_camera_pairs=0`、投影与时钟/画面对齐认证仍为false。候选可以为后续独立标定提供待核对信息，不能替代地标像素真值、FOV/roll或相机—截图对应证据。

合成互操作材料位于 `diagnostics/native-orientation-interop-20261112`：助手生产解析器及写入器生成的闭合两事件包，经版本化清单与全部SHA复制后由Insights独立接收。它证明协议链路，不证明真实镜头或地图精度。
