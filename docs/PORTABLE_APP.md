# Insights 免安装应用

将ZIP完整解压到可写目录，双击 `Dota2Insights.exe`。EXE与相邻目录须一起保留，不要只复制EXE。不需要安装Python、登录Steam或启动实时助手。

此入口负责：选择已下载DEM、逐场解析、查看事实报告。启动时不自动解析、下载、采屏或录音；Dota运行时等待，中途开游戏会停止当前解析。未完成结果保留原因，不自动重试。普通源代码CLI仍在仓库，未把所有研究工具变成EXE按钮。

新任务与报告写入本应用 `diagnostics/replay-workbench/`；原录像不移动。历史资料菜单通过本地 `config/history.local.json` 定位旧目录，发布包不携带本机配置、个人记录、历史资料或凭据。可按仓库 `config/history.example.json` 配置；未配置时不猜测旧目录。

使用固定Gem 0.13.0独立工作进程，界面不加载解析器环境。封存比赛保持拒绝，不因换入口解锁；自检和合成测试不证明地图/眼位/声音准确性通过。

开发构建：准备 `.replay-deps`、同固定提交的 `.parser-source`，安装PyInstaller后运行 `python tools/build_insights_app.py`。构建只读取已有本机环境，不自动下载依赖；Dota运行时拒绝构建。完整依赖版本由构建脚本核验。

构建先验证公开锁与固定解析环境，再生成独立界面和解析进程，运行冻结程序离线自检（编解码、公开锁、隐藏空闲窗口和退出）。通过后才生成ZIP。失败保留日志，未通过的目录不当作交付包。

外部自检：`Dota2Insights.exe --offline-self-check 新文件.json --check-ui`。不会读DEM或联网；检查范围不包括真实解析、游戏避让实效、活动任务退出和实际DPI布局。
