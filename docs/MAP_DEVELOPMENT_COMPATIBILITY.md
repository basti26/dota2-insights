# 同包跨构建开发比较与冻结图标关联

2026-11-05，接续MAP_EVALUATION_PLAN。只用既有图片、官方图标与冻结地图资料；不改Foresight/生产投影，不操作游戏或联网，不自动提交推送。

## 显式兼容，限定用途

`map_compatibility`增加定向、非传递的声明。只允许相同地图变体及完整地图包SHA的两个不同上下文，scope固定为development_landmark_comparison。声明固定来源、目标、证据指纹和内容摘要，版本/真假标志严格校验。UI等价、标定和生产就绪均固定false。

生成入口：

```powershell
python tools/declare_map_compatibility.py --prototype <已有原型包> --revision <已捕获revision目录> --output diagnostics/<新目录>
```

入口先用原型小清单查封存锁，再核验显示文件；旧plan、atlas、source-before收据相互绑定，旧地图SHA/字节数与目标revision一致，目标冻结原包及steam.inf也重验。证明范围是“这些冻结来源记录了同一个地图包”；不证明旧客户端UI、外部依赖或游戏运行环境相同。

`evaluate_map.py windows`可逐次显式传入`--compatibility <declaration.json>`。消费时校验方向/用途/声明摘要，并对全部证据文件进入、退出复验；缺声明仍拒绝跨构建像素比较，不存在全局开关或传递放行。VirtualMap.require_compatible和眼位版本门禁未改，声明不能用于正式留出、P2、生产或实战浮层。

记录文件仍限定在本机diagnostics或配置的历史根；为核验旧算法来源，只允许固定名单内的本仓源码作为指纹证据，不将整个仓库或其他项目源码开放为记录输入。

## 先冻结图像检测，再显式关联身份

`associate_map_glyphs.py`消费已有开发清单、冻结图标观察、显式视觉关联及原型。核验freeze/观察/原包、图像SHA和观察源码/图标素材；未冻结、预测引导、溢出、重复候选/身份、图片不符、覆盖已有标签均拒收。只写新目录，不改原观察或原始坐标。像素原值直接取冻结检测结果，不按投影距离挑候选、不拟合或做亚像素调整。

关联文件为development，包含reviewer、prediction_guided=false、dataset_sha256、observations_sha256；每条包含landmark_id、image_sha256、case、frame_index、icon、candidate_index、visually_confirmed与basis。它是审阅者声明，程序校验不等于身份真实性认证。当前真实关联由助手看原图和官方精灵图作开发视觉复核，**没有人工认证，也不是最终独立留出集**。

当前取点定义固定为frozen_template_canvas_center_not_glyph_centroid，测量不确定度保持null。官方莲花池图标含黑色叶尖和白色底座；白底座中心不能代替整个模板画布中心。初次目视估计原样留存于blind-pixels/，明确不用于数值评估。

## 本次实际进展与数值

旧图标检测的源码指纹已改变，首次导入按门禁拒收。本轮仅复算15张已有小图、4类图标，共60行；所有候选（坐标、分数、尺寸、矩形）逐项精确一致。完整检测记录增加minimum_center_distance_px和per_scale_candidate_limit两项参数元数据，不能说完整文件相同。旧记录未改，当前复算另写reproduced-glyphs/。

两个可辨认的莲花池关联到实体4358/4359，冻结中心分别为(31.5,86.5)、(345.5,265.5)。仍是以前已观察的两个空间位置，**没有增加独立空间样本数**；其余46个候选继续缺标注。

客户端6966图片与6971资产经显式同包声明进入开发比较。三个固定窗口统一使用两点与1024参考尺度：

| 直接铺满保存裁图的候选窗口 | 两点误差中位数 | 最大误差 |
|---|---:|---:|
| 声明窗±9472 | 53.188 | 65.335 |
| 参考窗-10464..10400 | 91.814 | 104.864 |
| 旧Gem平移后窗口 | 65.513 | 72.298 |

这与此前声明窗莲花无序集合的14.428–22.969原360像素残差一致；本轮增加可追溯身份与开发门禁，并非取得新的独立真值。只能说明这些“完整窗口直接铺满该保存裁框”的假设没有得到这两点支持，不能判断误差由裁框/缩放/图标原点语义哪一项引起，更不能选定整图胜者。

九区仍证据不足，formal_p2_pass=false，UI等价未知；Gem直接坐标平移、真实眼位P3继续待证。原型和Foresight的实际显示未改。

## 证据与后续

本机证据目录：diagnostics/map-development-20261105/；当前声明compatibility-v3/，当前窗口报告window-report-final/，候选复算差异reproduction-details.json，完整链复验verification.json。

下一步优先处理已有图片中的其他非塔地标：先确认是否有可识别图标及明确点定义，不把神符/中立点/瞭望塔的相似图形直接认作同一对象。达到开发集跨区域覆盖后再决定是否拟合；正式留出继续封存，收尾显示变换与zoom统一暂不混改。
