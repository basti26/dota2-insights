# 地图更新与版本资料接口

2026-11-05。实现`dota2_insights/map_updates.py`及`tools/map_update.py`，供以后地图升级、离线分析入口和原型启动器调用。只做本机版本检查、保存快照、生成差异、选择解析器和读取派生资料，不联网、不改游戏、不自动切换生产地图。

## 更新流程

1. `check`读取本机`steam.inf`构建号、完整`maps/dota.vpk`及逐资源SHA，与最近成功捕获版本比较。可同时检查一个原型包是否对应当前安装。
2. `capture`冻结这一版的地图包、版本原文、1030项资源指纹（当前实际数量）及格式探测结果。重复捕获同一版返回原记录；不同版本新建记录，不覆盖旧版。
3. 地图内容改变后，`prepare`从已保存快照调用版本适配器，复用现有根资源、地标、碰撞提取器。不会回头读取已被Steam更新的安装文件，也不会将旧截图标定填给新地图。
4. 后续消费端用`read_prepared`或`verify`复验整份派生资料，包括网格、模型、文本和各级收据。成功状态固定为`extracted_unverified`，还需要独立标定/验收，不能当作生产就绪。

当前在客户端6971、地图包SHA为`8543569c3c661f69694a341603041bd32fde3599072d35a9301e68406d152a0f`完成真实本地流程：无基线→捕获→检查未变化→从冻结资料解析→验证22份派生文件。原型v3与当前安装的构建/地图相同；它仍是未认证原型。

## 变化分类

| status | 含义 | 默认处理 |
|---|---|---|
| baseline_missing | 还没有本机基线 | 先捕获，记录当前状态 |
| unchanged | 构建、版本原文及地图包都相同 | 保留记录，认证状态保持原状 |
| client_changed_same_map | 构建变化，地图包字节相同 | 静态资料仅是复用候选，UI/镜头/外部依赖仍需核验 |
| archive_repacked_same_resources | 包字节不同，但全部资源名称、长度和SHA相同 | 不称地形变化；新身份单独保存，重新核验上下文 |
| map_content_changed | 资源增删或原字节变化 | 明确列出added/removed/changed，重新提取与核验 |
| installation_metadata_changed | 同构建同地图，但版本原文改变 | 新记录，不猜测变更原因 |
| map_variant_changed | 地图身份不同 | 拒绝混用，重新提取与核验 |

按SHA而非仅CRC或构建号判断资源变化。`requires_new_validation`只表示这次变化是否触发再验；false不代表旧地图已通过认证。所有结果均有`production_ready=false`、`calibration_inherited=false`。`static_payload_reuse_candidate`不是自动修改旧数据身份的许可，也不将游戏UI或外部依赖标成兼容。

旧版模型的严格`MapIdentity`门禁保持原样。客户端、地图包、地图变体不一致仍不能直接向旧模型塞入新坐标。历史原型可继续用于对照查看，`check_prototype`明确区分`historical_view_allowed`与`reuse_for_current_game_allowed`；当前所有候选均不能因此进入实战生产投影。

眼位已接入同一检查，check_prototype结果新增wards上下文，包含录像来源版本关系、目标地图是否对应当前安装、是否需要新绑定及只读历史预览状态。详见WARD_MAP_INTEGRATION.md；未知来源地图保持未知，不从目标地图补写。

## 命令

在本仓根目录执行，游戏需关闭：

```powershell
python tools/map_update.py check --game "<Dota安装目录>/game/dota" --prototype diagnostics/<原型目录>
python tools/map_update.py capture --game "<Dota安装目录>/game/dota"
python tools/map_update.py prepare --revision diagnostics/map-versions/revisions/<revision_id> --decoder "<本机固定解码器>"
python tools/map_update.py verify --extraction diagnostics/map-versions/derived/<revision_id>/<attempt> --revision diagnostics/map-versions/revisions/<revision_id>
python tools/map_update.py diff --before diagnostics/map-versions/revisions/<旧ID> --after diagnostics/map-versions/revisions/<新ID>
```

`check/capture`可传`--store diagnostics/<另一个资料库>`；`check --output diagnostics/<新目录>`可保存check.json。check只读，不修改基线；capture成功后才原子更新`latest-capture.json`。该文件表示最近捕获记录，**不是生产地图选择器**。回查旧版直接用其revision目录，不删除新旧数据、不按最高构建号猜当前版本。

没有新增CMD、定时任务、下载器、启动器按钮或后台轮询。这些是可供后续界面直接调用的函数/命令接口。

## Python调用与消费契约

```python
from dota2_insights.map_updates import (
    inspect_installation, latest_revision, compare, capture,
    prepare_revision, read_revision, read_prepared, check_prototype,
)

installed = inspect_installation(game_directory)
prior = latest_revision(local_store)
change = compare(prior[1] if prior else None, installed)
prototype_status = check_prototype(prototype_directory, installed)

revision_directory, frozen = capture(game_directory, local_store)
attempt_directory, result = prepare_revision(revision_directory, decoder=decoder_path)
verified = read_prepared(attempt_directory, revision_directory)
```

`revision_id`绑定地图身份和原始steam.inf指纹，`map_content_sha256`绑定排序后的资源名/长度/SHA。玩法补丁仍未知，不用统计网站版本或日期补写。`external_dependencies_verified=false`：这次只完整追踪地图包，不宣称pak01内材质、模型、UI或所有外部依赖未变。

捕获目录包含`source/maps/dota.vpk`、`source/steam.inf`、snapshot.json和COMPLETE。相同地图包在本机内容寻址blobs内共用磁盘字节，通过本资料库内部硬链接或复制形成来源；不链接游戏安装或另一个仓库。游戏资产及派生资料都在Git忽略的diagnostics。

解析目录包含root/landmarks/collision三组原生输出，以及extraction.json和COMPLETE。metadata记录各组身份和全部文件SHA。消费端复验资料来源，不能只看目录名、最新指针或COMPLETE是否存在。

## 新格式适配入口

默认适配器`source2-observed-v1`只对当前观察到的编译头、GNV/TRM布局做尝试资格判断，不保证完整解码一定成功。未知内格式仍可冻结原包，但默认解析器不会强行继续或退回旧版输出。

未来添加解析器时，以代码注册明确的支持条件和提取函数：

```python
from dota2_insights.map_updates import MapUpdateAdapter

adapter = MapUpdateAdapter(
    profile="source2-future-v2",
    extract=extract_future_map,
    accepts=recognizes_future_snapshot,
)
prepare_revision(revision_directory, decoder=new_decoder, adapters=(adapter,))
```

`accepts(snapshot)`必须返回bool，并按明确版本/格式判断；`extract(source_directory, output_directory, decoder)`必须等待全部工作收尾，返回`{"root": "root-assets.json", "landmarks": "landmarks.json", "collision": "collision.json"}`。各子目录须有身份匹配的元数据和COMPLETE；不能把raw extraction标成已标定、生产就绪或可插眼。新增解码器还需更新其受控指纹及对应测试，不能任意换二进制后沿用旧验证结论。

## 失败与验证边界

- 扫描/捕获中安装文件改变、Dota启动、资源CRC/SHA不符：拒绝发布或更新指针。
- 捕获采用独立staging，失败留下FAILED.json；旧版和旧指针保留，下一次可以重新尝试，不会被半个revision占住正式路径。
- 每次解析使用新的attempt目录，部分输出保留诊断；没有整组COMPLETE就不是成功。重试不覆盖上一次成功或失败结果。
- 消费时几何/文本被改动、缺文件、版本错配、伪造ready状态：拒收，不用旧结果顶替。
- 当前支持有界、单包内嵌VPK，包≤64MiB、资源≤20000、单资源≤16MiB、资源总量≤128MiB；分卷VPK或超预算明确要求扩展适配器，不静默漏读。
- 解析子输出每组≤128文件/256MiB、≤64子目录，文件变化/链接拒绝；固定CLI解码仍保留原有超时/大小/Dota启动终止边界。

21项离线专项覆盖升级/回退、重打包、资源增删改、幂等保存、来源变化、未知格式、失败重试、篡改/伪认证、读取门禁和输出预算。真实本机端到端证据在`diagnostics/map-update-20261105/`。本接口不补齐仍未完成的P2/P3/P4、碰撞世界放置、GNV语义或动态树木认证。

最终942项完整离线回归通过（`diagnostics/offline-tests/run-bd94f8ef5e51/result.json`），0失败/错误/跳过/后台异常，源码稳定。当前真实版本已捕获，内置适配器从冻结地图成功重建三组资料，22份派生文件通过消费端复验；旧原型包和原提取目录保持不变。未提交推送。
