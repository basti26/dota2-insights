# 本人经济与显示记录复核

2026-10-26。self_economy_audit.SelfEconomyAudit用于已闭合记录的离线核对，兼容旧launcher包与原生逐局GSI包。不是重新计价、改变线上经济或证明画面准确率。

## 检查范围

- 同包核对：记录的provider_timestamp与display_economy.valuation_provider_timestamp一致，且本人金额、显示原始金额和显示金额均为有效整数时，才比较。不同包/缺时间/缺金额分别记不可比，不按零处理，也不算显示正确。
- 普通本人显示：raw_amount和amount必须与本包self_economy.amount一致。沿用reported/estimated/partial原状态，GSI下降不平滑。
- 德鲁伊混合显示：本体GSI金额与熊灵独立装备下界合计；要求明确本人英雄、同比赛、owner_basis、会话/绑定、图像SHA、源/计算UTC、主格/背包金额与计数。本人统计会话和截图会话独立，不能误要求这两种game_session_id相等。来源不一致单列，不能把算术通过当作图像或归属验证通过。
- GSI已直接给reported净资产时不再加熊灵、不拿不完整分项强行核对净资产。只有partial/estimated且三项都齐全时核对现金/装备/升级加总。
- 按同比赛/会话/英雄/估值口径记录5秒内至少500的下降，仅作追查线索，不分类为识别错误。来源行及原时间保留。

数值bool/缺失不转零；示例最多20条，正常下降不能挤掉后来出现的显示异常证据。输出accuracy_verified=false、display_pixels_verified=false；不回写金额、标签或源文件。

熊灵记录契约依据Foresight self_bear_economy.py的既有来源/算术约束独立实现，核对时参考源码SHA256为8ad1c23122ba7f4451daa0fd6b387a9724d7456669e37c2f14fc2a4291d161c4。不是跨仓库运行时导入，不修改拆仓原始来源清单，也不声称新模块与原源码逐字一致。

## 入口与证据

先运行tools/sync_helper_records.py。旧资料继续由tools/review_closed_record_content.py通过ClosedJournals进出复验后生成REPORT.md，新增own_economy_audit字段和同包显示结论；原生逐局资料由tools/review_match_journals.py通过match_journals.open_rows进出复验后输出同一审计结构。分析完成且来源复验通过后才生成结果。

最近四场已闭合资料只读复核：11,513行中11,345行可进行普通显示比较，记录值一致且分项加总无不符；168行本人金额不可用。没有reported净资产行，没有熊灵混合显示行。因此不能把结果说成“真实净资产已全量对齐”或“德鲁伊实战已验证”。多数partial缺口来自TP来源、吃树剩余次数及合并眼等估值歧义，均保留原原因，不修改游戏界面。

实际报告在diagnostics/record-content/20261026T075151837213Z/；比赛号、行号、来源SHA和具体金额只保存在本机报告。未重新解析录像或读取图片/音频，没有打开游戏、联网或修改原历史。

最终358项完整离线回归通过（diagnostics/offline-tests/run-4884d2b82559/result.json），无失败/错误/跳过/后台异常且回归期间源码未变。覆盖同包/异包、reported与partial分项、独立会话的熊灵合计、错误来源/绑定/时间/计数、缺失与bool、下降不平滑以及异常证据保留；测试不替代游戏显示或装备图像核验。
