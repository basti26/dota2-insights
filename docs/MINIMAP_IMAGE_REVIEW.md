# 小地图原图抽样复核

2026-10-26。原生run和旧闭合启动器包共用图像核验及复核页，不补写旧收据、不修改历史PNG或日志。目的是把原始记录与图像对应起来，检查采集现象；不是英雄身份、地图坐标或消失预警的自动验收。

## 使用

```powershell
python tools/prepare_minimap_review.py --latest 3 --max-runs 2 --max-images 12
```

入口先通过tools/review_recordings.py同步并复验各类资料，再读取独立小地图索引和旧启动器索引。原生包优先、旧格式补充，按根标识+原samples日志路径去重。默认最多3个run、每run12张；max-runs限定1–10、max-images限定2–24。没有合法闭合资料时明确输出0个run，不说没有采集。

结果只写Insights diagnostics/minimap-image-review/<UTC>/：INDEX.md、batch.json，以及各run的REVIEW.md、evidence.json、contact.png和samples/原PNG。原PNG按原字节保存，拼图才缩放；缺图/校验失败单列unavailable，不拿空白图替代。报告页面链接原图以供放大复核。

## 文件与几何验证

原生记录通过minimap_handoff.open_rows进入/退出复验，绑定独立索引包SHA及run/chain/关闭时钟；旧记录通过ClosedJournals，只允许已经准入的minimap samples日志。证据保留原行号、原行SHA、日志路径/包来源和采集UTC；抽样点由当前原始日志产生，不信任外部传来的标签或坐标。

图像路径只允许同run的frames/frame-<6位序号>.png，禁止越界、符号链接/目录联接；最多8MiB。先核验原PNG SHA，再要求PNG/RGB模式及宽高等于原ROI尺寸；ROI为客户端内64–1024像素矩形，客户端最大8192×4320。记录存在pixel_sha256时，RGB解码后恢复BGR原像素字节核验；缺少这个字段时单独标pixel_sha_verified=false，不伪造通过。

已准备的原图在发布证据前再次核对SHA，原日志在分析退出时再次校验。图像不可用可以生成有缺口的复核页；日志或已核验图像在处理中变化时，本次不发布evidence.json成功清单，已产生的部分文件不能当成功。输出禁止落入任一历史根。

## 抽样与时钟口径

先遍历已准入日志，统计实际sample行和原UTC相邻间隔；再按实际样本序列均匀分散抽取，并在预算至少4张时加入最大非负间隔的两端，保留首末样本。不按UTC排序隐藏倒退，不拿索引时间替代原捕获时间。

报告单列时钟倒退、同一采集UTC、缺时钟、相邻图像引用复用、client_size/roi/layout_segment记录变化和采集状态行。最大间隔仅指相邻已接受样本，不是丢失帧数、FPS、断网或敌人消失；静止画面复用不是卡死证据。此抽样不认证全量图像内容，也不将启动器比赛号或chain_id转成小地图比赛归属。

## 实际与生成资料验证

新增11项专项覆盖原PNG/BGR像素SHA、几何、最大间隔两端/分散抽样、时钟倒退、缺图、路径拒收、处理中源/图像变化、旧格式适配和一条命令。最终454项完整离线回归通过（diagnostics/offline-tests/run-928e0d471f9c/result.json），无失败/错误/跳过/后台异常且回归期间源码未变。

真实learning_worker此前用模拟源产生的两帧，现也经自动接收→抽样→原图核验，2张样本均可用、1次相邻图像复用，原文件SHA/mtime不变；证据diagnostics/minimap-image-interop-20261026/verification.json。这是生成像素的管线验证，不是实战图像。

正式旧资料先同步后限定两条流：5456+7061=12517条样本，抽中24张全部通过原PNG和BGR像素SHA/尺寸检查，实际查看了两张拼图。最大相邻采集UTC间隔分别0.537498秒、2.033072秒；时钟倒退和run内布局变化均0，第二条有1次相邻图像引用复用。24张抽样中可见真实小地图内容，英雄名字会遮挡部分图标；没有据此补英雄真值或开放预警。

实际报告：diagnostics/minimap-image-review/20261026T095554077691Z/INDEX.md；查看说明：同目录REVIEW_NOTES.md。不同run的ROI分别[8,1064,372,372]和[12,1064,366,366]，不能合成一个固定几何或据此通过地图世界坐标标定。源资料只读；未开游戏、采屏、联网、重跑录像、重建EXE或提交推送。
