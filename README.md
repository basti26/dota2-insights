# Dota 2 Insights

独立的录像与数据分析项目。从 dota2-foresight（原 dota2-helper）拆出，负责录像事实、赛后经济复核、地图/眼位验证、小地图与声音离线评估。实战GSI、采屏、覆盖层、BP操作和记录器仍由 dota2-foresight 负责。

## 日常赛后分析

由Codex执行后台命令和整理分析结果，不要求用户操作EXE。先运行 `python tools/sync_helper_records.py --latest 3`，只读接收Helper已关闭的记录清单，再按比赛编号复核。原始资料继续留在Helper，insights只写索引和新报告。收尾条件、数据缺口和当前逐局交接边界见 [交接契约](docs/HELPER_RECORD_HANDOFF_V1.md)。

接收后可运行 `python tools/review_closed_record_content.py`，汇总本人GSI、装备观测、BP界面回执和共享资料的场次候选；再按报告准备大件原图裁片。流程与限制见[闭合记录内容复核](docs/CLOSED_RECORD_REVIEW.md)。时间候选不当作身份真值，裁片准备不等于识别验收通过。

## 备用启动入口

已有独立免安装版：完整解压 `dist/insights-0.1.0-20261025-125559-566019/Dota2Insights-0.1.0-win64.zip`，双击其中 `Dota2Insights.exe`，无需安装Python。交付路径、检查范围和限制见 [免安装版交接](docs/PORTABLE_RELEASE_20261025.md)。本机构建产物被Git忽略，仓库不会携带个人数据或二进制ZIP。

Windows / Python 3.12：双击 `Start-Dota2-Insights.cmd`，选择录像，点击开始队列。不会在启动时解析录像或访问远端；Dota运行时等待，中途打开Dota会请求停止解析。原录像不移动。

本机迁移已准备独立 `.replay-deps`。新电脑先安装桌面分析依赖，再单独准备解析环境：

```powershell
python -m pip install -r requirements.txt
python -m pip install --target .replay-deps -r requirements-replay.txt
python tools/win_insights.py --self-check
```

安装命令会联网，不由启动器自动执行。解析器固定Gem 0.13.0及已审阅提交，依赖与实时助手隔离。上述命令仅用于源码入口；免安装版已有独立界面和解析工作进程。

## 历史资料

原录像、截图、录音、报告、旧程序全部留在原位置。`config/history.local.json` 记录本机路径，不进入Git；可按 `config/history.example.json` 配置。界面“历史资料”菜单可打开原目录。新分析默认写入本仓库 `diagnostics/`，原分析事实与人工标签不回写。

声音工具已支持从明确配置的历史目录读取、向新目录输出。需人工听审的26段仍未完成，不因拆仓补标签。封存比赛锁定名单随源码迁移，未解封。

## 开发

- 当前状态：[STATUS.md](STATUS.md)
- 后续任务：[TASKS.md](TASKS.md)
- 分工与迁移：[docs/PROJECT_SPLIT.md](docs/PROJECT_SPLIT.md)
- 开发前必读：[AGENTS.md](AGENTS.md)、[Git流程](docs/GIT_WORKFLOW.md)

离线测试：`python tools/run_offline_tests.py --pattern 'test_*.py'`。不启动游戏、采屏、录音或外网；回归记录保存到 `diagnostics/offline-tests/`。

分析结果通过版本化记录包/数据包与助手交换，不从助手目录导入源码。规则/统计交付格式及检查工具见[数据包契约v1](docs/DISTRIBUTION_BUNDLES_V1.md)；Foresight读取适配尚未接入，不自动把眼位先验、能力候选或全知录像位置投放到实战。当前未新增许可证声明，拆仓不改变第三方许可。
