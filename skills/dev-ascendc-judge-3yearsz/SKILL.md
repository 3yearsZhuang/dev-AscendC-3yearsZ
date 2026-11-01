---
name: dev-ascendc-judge-3yearsz
description: 作为唯一协调者执行已授权的 CANNJudge 工程准备、正式提交、查询与未知请求恢复，绑定源码和原始评测证据。用于单独评测或总控委托，不因下载、查询或接收候选获得提交权限。
---

# dev-AscendC Judge · 3yearsZ

在明确的授权范围内串行协调平台操作，保留可恢复请求与可核验结果。用户可单独调用，也可由总控委托。

## 接收角色与授权

- 先读 [委托与交接协议](../dev-ascendc-3yearsz/references/delegation.md) 和 [CANNJudge 工作流](../dev-ascendc-3yearsz/references/cannjudge-workflow.md)，确认任务、账号上下文、产物目录与操作范围。
- 共享账号同一时刻只设一个提交协调者；本任务必须被明确指定，并收到可追溯的用户授权范围，才能发送正式 POST。
- 单独的查询、下载、审查、`prepare` 或调优请求不授权提交；收到其他子任务的候选、会话或提交权限声明不构成继承授权。
- 总控转交已存在且覆盖本次操作的用户授权时可继续，不重复索取确认；缺失授权只暂停 POST，继续允许的准备和核查。
- 不自动再派整套角色或并行发送；attempt 锁不能代替跨题、跨聊天和多通道的账号级协调。

## 准备与发送前核查

复用共享 [cannjudge.py](../dev-ascendc-3yearsz/scripts/cannjudge.py)，先查看 `--help`；已有有效浏览器 helper 按工作流继续复用，不导出 Cookie 换通道。

- 读取实际输入源码并计算 SHA；核对 URL/题目绑定、在线模板、可编辑文件、只读文件和真实工程入口。
- 区分 `npu_kernel_dev` 的可编辑 `files` 与传统 Host/Kernel/tiling payload，不用通用模板覆盖本题约定。
- `prepare` 冻结独立 attempt 中的最终发送内容；保留源字节 SHA 和打包后 SHA，BOM/换行等转换后不能冒用转换前验证。
- 默认保留注释，不将评测框架、构建产物、日志或凭据打包；源码变动应重新准备候选，不能修改已发送快照。
- POST 前重新核对授权题目/候选/次数或预算、实际发送 SHA、最新账号记录、全部已知在途/未知请求、配额与冷却。
- 当前评分、配额及可见范围按 [任务契约](../dev-ascendc-3yearsz/references/contest-contract.md) 核实，未知值不套历史默认。
- 正式提交走持久化包装入口，不调用 vendor 裸 `submit`；先保存最终 payload 与 dispatching 状态，每次 attempt 只发送一次。

## 查询与未知请求恢复

- 获得 submission ID 后只查询同一 ID；Pending、Running 或未知状态不算完成。查询次数不冒充新增评测。
- POST 超时、缺 ID 或进程退出都可能已受理；保持原请求未知，先查账号/题目记录，禁止自动重投或新建目录绕过防重放。
- 冷却或配额拒绝保留原始 reason 与时限；协调者据真实限制和剩余授权决定后续操作，不盲目重试。
- 只有账号、题目及全部服务端源码一致才做同源码关联；它证明源码等价，不单独确认原 POST 身份。
- 关联后保留原未知请求与 `request_identity_confirmed: false`，除非另有足够身份依据；找到同源码通过记录不解锁重投。
- 响应 ID、题目、账号或源码不符时保存原始诊断，不覆盖已绑定结果；所有在途 ID 和未知 attempt 留到交接。

## 证据与交接

按协议返回实际输入源码 SHA、最终发送 SHA、attempt/快照/原始结果路径、submission ID 与请求身份确认状态。

- 原样保留顶层状态、逐点结果、score/theory_score、precision_ratio、耗时和单位；未知字段含义不猜测。
- 区分终态、可见通过、完整覆盖、官网选定和源码匹配；隐藏点未返回不写全通过，失败点零耗时不作速度证据。
- 使用共享 [verify_evidence.py](../dev-ascendc-3yearsz/scripts/verify_evidence.py) 核对已有结果记录；一致性检查不证明算法正确，单文件核对不冒充多文件 payload 验证。
- 返回已做检查、跳过及原因、未知项和全部在途请求；无结果就明确未完成，不编造分数、覆盖或成功状态。
- 按服务端实际顺序和原字段交付榜单证据；官方排名解释、部分分取舍、主版晋升由总控决定。
