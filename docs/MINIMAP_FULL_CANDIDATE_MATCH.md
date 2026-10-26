# 完整颜色候选的资源族对照

2026-10-26。将资源匹配从“有孔形状”扩展到原颜色基线保留的所有候选，补入实心/方向型标记、文字、建筑和兵点。输出仍是未认证形状建议，不是英雄身份、人数或可见性事件。

## 方法与入口

```powershell
python tools/match_minimap_frame_candidates.py --catalog diagnostics/<图标目录> --batch diagnostics/<原图复核目录>/batch.json --max-frames 8
```

输入是此前prepare_minimap_review产生的已准备PNG快照。严格核对批次schema、PNG SHA、原尺寸、先前像素核验标志和路径边界；本入口不重新核验原始比赛日志，报告明确prepared_png_snapshot_original_source_logs_not_reverified_here。从可用帧序中均匀取最多1–24张原图。

dota2_insights/minimap_frame_match.evaluate_frame调用固定颜色/轮廓基线，每个保留框按原坐标裁片，使用原9族25资源矩阵、603个变体及0.82/0.08分数与差距门禁；没有修改参数、只选有孔目标或只留下英雄模板。原生裁片不在4–128px范围时单列unavailable，不缩放源图凑输入。保留原截断前数量和48候选上限状态，不能用未输出候选当不存在。

所有结果保存在report.json中，包括未知与不可用；可视化最多64张，先保留最多32个形状建议，再按族/文字重合/方块重合分层及跨序列分散补充，无重复行。报告明确有多少形状建议未进入视觉页，不能把只看了部分当全检。原PNG和资源在发布前再次复验，输入改变不发布成功清单。

输出diagnostics/minimap-frame-template-match/<UTC>/REPORT.md、report.json、sheet-*.png及crops/。每条结果关联原候选框、原图/原行证据、跨族最佳/次佳、方向/尺寸，保存模块源码及输入/参照SHA。未知保留，所有semantic_identity_verified和accuracy_verified仍false，线上代码/配置不改。

## 本机结果

从已有48条连续样本的准备图像中取8张不同PNG，全部颜色候选共226条，无48个上限触顶。218条unknown，8条shape_candidate_only，8条全部对应ward_obs资源族；hero_marker形状建议为0。不能把8条称为8个独立眼，也不能把0英雄建议称为画面没有英雄或英雄召回率为0。

8条形状建议已全部进入视觉页并查看：外观与眼形资源相容，最佳分数0.832215–0.955882，跨族差0.120609–0.201596；没有把这些眼形样本强行归成英雄。它们跨多个帧、部分位置重复，未与录像实体或游戏客户端核对，故仍未确认实际语义/数量/位置。其余文字、建筑和小点等混淆保留未知。

本批还没有确认的英雄方向标记正例。部分原图可见英雄名字，不能凭名字附近没有形状建议就判断算法漏掉或英雄不可见；需要明确图标显示方式、位置和独立对象证据。后续应补全真实方向/填充外观及相邻文字遮挡，不能继续在同批资料上降阈值到出现英雄标签。

实际报告diagnostics/minimap-frame-template-match/20261026T104840063465Z/REPORT.md；查看备注同目录REVIEW_NOTES.md。前一遍输出保持原位不覆盖。6项新测试覆盖实心图形参与比较、原像素不改、上限/不可用裁片、优先视觉抽样不重复，以及完整工具的SHA/路径/schema/限制门禁。最终499项完整离线回归通过（diagnostics/offline-tests/run-58cb5d35f2aa/result.json），无失败/错误/跳过/后台异常且源码在回归中未变；git diff --check通过。未开游戏、采屏、联网、重建EXE或提交推送。
