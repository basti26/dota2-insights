# 小地图颜色候选与文字/方块歧义复核

2026-10-26。对已核验原图开展离线对照，不给实时助手添加算法、遮挡掩膜、候选删除或预警。该批24图已经用于探索，不能再宣称它是独立留出验证集。

## 基线与来源

dota2_insights/minimap_component_probe.py将Foresight的minimap_color_candidates_v1纯颜色/轮廓算法适配到离线侧：相同HSV范围、3×3闭运算、面积/长宽比/圆度、排序和48个上限。原源码及适配SHA见docs/minimap_component_probe_origin.json，未改历史migration/source-manifest.json，不跨仓库导入代码。

两个独立进程在同24张PNG上运行原Foresight函数与Insights适配函数：基础框、中心、颜色、面积、圆度、截断前数量完全一致，证据diagnostics/minimap-component-parity-20261026/verification.json。该对照证明适配相容，不证明原算法检测正确，也不是实时帧率/延迟测试。

## 增加的观察

疑似文字带：在原红/绿颜色掩膜上做横向闭运算，按372px参考尺度、宽≥18px、长宽比≥2.4、高3–16px及至少16个原色像素挑出横带。像素阈值按尺度换算，保留最多128条。它也可能是其他横向标记，短名字、字距很大、文字与英雄合并等情况可能漏检；不是OCR或已标注文字真值。

实色方块：复用分析项目已有square_candidates，仅提取8–15px参考尺寸、近方形且填充率较高的形状。当前方块中心落入同色候选的带padding框时标记重合；没有调用世界坐标标定，也不能说它一定是塔/建筑，英雄可以站在建筑附近。

疑似横带与同色候选框有交集时标记text_band_overlaps；方块重合标solid_square_overlaps。所有原候选仍保留，suppressed=false、hero_id=null、team_relation=unknown，hero_count=null。显示红/绿不是敌我身份。重合不是误识判决，未重合也不证明是英雄。

## 命令与输出

```powershell
python tools/probe_minimap_components.py --latest 3 --max-runs 2 --max-images 12
```

先调用prepare_minimap_review同步/复验并抽样原图，随后核对本批PNG复制件SHA与尺寸，再生成诊断。输出位于diagnostics/minimap-component-probe/<UTC>/REPORT.md、report.json、annotated-*.png；图中浅蓝框为原候选、橙框为疑似文字带、紫框为实色方块。报告保存算法源码SHA、参数SHA、OpenCV/NumPy版本、原行SHA和图像SHA。

源PNG/采样日志和线上配置不改。报告只陈述本次探索的候选/重合数量，不产生英雄数、敌我分类、可见/消失事件、准确率或地图认证。新输出是来源已核验图像的分析副本，不升级为新的游戏真值。

## 结果与边界

本机实际24张不同SHA的PNG，来自两条已有闭合采样流；本次重新同步、抽样和核验后得到：

| 项目 | 数量 |
| --- | ---: |
| 输入图像 | 24 |
| 截断前颜色候选 | 663 |
| 保留候选 | 663 |
| 到达48个上限的帧 | 0 |
| 疑似横向文字带 | 55 |
| 与文字带重合的候选 | 40 |
| 与实色方块重合的候选 | 197 |

候选是跨24帧的观测总数，不是独立英雄或误识个数；没有准确率分母。两类标记按设计可以重叠，本批实际同时重合为0，不能推广为互斥类别。

已查看annotated-001和annotated-016：部分候选框位于可见塔/基地、名字及其他图标附近。文字带规则能框出部分名字，但存在文字与英雄图标相邻或合并的情况；不应直接把整个文字框遮掉。仍需要逐目标证据或录像/可见性对照，才能决定怎样分离移动英雄与静态符号。本轮没有修改在线阈值或删框。

报告diagnostics/minimap-component-probe/20261026T100354372321Z/REPORT.md；实际查看说明同目录REVIEW_NOTES.md。8项生成形状测试覆盖圆点保留、横带、方块重合、文字相邻、探测失败、候选上限、原像素不改及输入边界。最终462项完整离线回归通过（diagnostics/offline-tests/run-7742f61d9b52/result.json），无失败/错误/跳过/后台异常，源码在回归中未变。助手运行代码未改，未开游戏、采屏、联网、重建EXE或提交推送。
