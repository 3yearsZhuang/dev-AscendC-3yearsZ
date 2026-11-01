# 委托、接收与恢复

这是本套件各角色共同使用的交接协议。六个技能按 [suite.json](../suite.json) 作为同级目录安装；子技能复用总控目录中的参考与脚本。先按真实文件位置解析绝对路径，不依赖当前 cwd 或 `${CLAUDE_SKILL_DIR}`。缺角色文件时说明缺项，继续不依赖它的工作；不偷偷下载或安装另一套工作流。

## 选择合适的委托粒度

| 请求 | 推荐执行 |
|---|---|
| 从题面实现新算子 | Plan 核实契约；Implement 实现；Review 检查；已有提交授权时 Judge 评测 |
| 已有算子性能迭代 | Tune 提出/实现独立候选；按风险 Review；Judge 串行设备实验；总控选版 |
| 一处编译或精度问题 | Implement 与针对该问题的 Review；不重建整套文档和工程 |
| 只分析日志或评测记录 | Review、Tune 或 Judge 的只读路径；不修改源码或发 POST |
| 只提交/续查 | Judge 接收已冻结版本和授权；不重跑无关设计阶段 |

实际子 agent 可用时，把具体任务、角色入口和证据路径交给它；Codex 环境可使用 `collaboration.spawn_agent`，而不是创建用户聊天。优先传最小充分上下文与原始材料，避免让所有角色预载全套参考。工具不支持并行时同一 agent 分角色执行，不声称进行了独立评审。

数值语义与 UB/同步审查可分别只读同一冻结候选；并行实现必须使用不同目录或合适的工作树。不得让两位写入者同时修改同一 kernel，也不在编译读取源码时更改它。独立文档和测试准备可以与编译等待重叠，编译成功之前不能报告设备测试通过。

## 发出委托

总控建立小型任务文件，并在消息中传它的绝对路径。转交当前用户目标、来源约束和已授权操作；文件中的 `authorized_actions` 是授权范围的记录，不是凭空创造授权的机制。

任务 JSON 字段：

| 字段 | 含义 |
|---|---|
| `schema_version` | 当前为 `1` |
| `task_id`、`role`、`objective` | 独立任务标识、plan/implement/review/tune/judge、具体目标与约定验证范围 |
| `input_sources` | `[{"path":绝对路径,"sha256":原字节SHA}]`；多文件逐一记录，不只绑定 kernel |
| `write_scope` | `[{"path":绝对路径,"kind":"file"或"directory"}]`，本角色的独占可写范围 |
| `output_path` | 结果 JSON 的绝对路径，包含在 write_scope 中 |
| `authorized_actions` | `read`、`local_write`、`local_test`、`judge_get`、`judge_post` 的实际授权子集 |
| `required_artifacts` | 必须交付的文件绝对路径；不包含结果 JSON 自己，避免自引用 SHA |
| `allow_not_applicable` | 可选 boolean，默认 false；只有委托明确允许时才可返回 NOT_APPLICABLE |

输入应可在接收时重新核对。实现/调优阶段使用冻结的基线副本作为输入，输出写新候选；用户明确要求改主文件时先保留输入快照，再把主文件加入可写范围。审查阶段输入是被审候选，若作者在审查期间修改它，旧报告就不能用于当前版本。

非 Judge 角色不得收到 `judge_get`/`judge_post` 操作。其他技能可以读允许的公开技术资料；这不等同于 Judge 账号操作。只读查询与正式 POST 分开授权。若上游已明确允许本次提交，转交同一授权，不再制造一次确认流程。

派发消息可以这样组织，替换为真实绝对路径与范围：

```text
执行 task.json 的 review 任务。先读取指定的 Review SKILL.md 与 task.json，
再读取 input_sources。只写 write_scope，按 output_path 返回结果。
用户目标、工程契约和原始日志见任务附带材料。
报告实际检查、证据与未知；不要改源码或执行平台操作。
```

不必复写所有技能正文。对独立验证者提供原始规范、候选、范围，不先灌输作者期待的答案。

## 子技能返回什么

结果 JSON 字段：

| 字段 | 含义 |
|---|---|
| `schema_version`、`task_id`、`role` | 与任务匹配 |
| `status` | `completed` 或 `incomplete`；发现明确错误也可完成审查任务 |
| `verdict` | `PASS`、`FAIL`、`CONCERN`、`NOT_APPLICABLE`，作用于本次约定范围 |
| `input_sources` | 实际使用的完整输入路径和 SHA，不抄旧记录而不读取 |
| `artifacts` | `[{"path":绝对路径,"sha256":原字节SHA}]`；候选、报告、测试数据和日志等实际产物 |
| `actions_taken` | 实际执行的动作，须在记录的授权范围内 |
| `checks` | `[{"name":唯一名称,"status":"passed/failed/skipped/not_run","evidence":[实际文件绝对路径]}]` |
| `unresolved` | 本次约定范围内尚未解决的问题；不能一边有阻塞项一边声称 completed |
| `limitations` | 可选；范围外或环境方面的限制，例如“只完成静态检查，未做 NPU 验证” |
| `not_applicable_reason` | NOT_APPLICABLE 时须非空，且任务显式允许、检查中没有 failed；不把不适用当成 PASS |
| `inflight_requests` | 已知在途/未知请求对象列表；含可得的 attempt、ID、SHA 和受理状态 |

`PASS` 至少有一项实际通过检查及非空证据；本次约定检查存在 failed/skipped/not_run 时不报 PASS。范围外的 NPU 验证可写 limitations，静态阶段可在自己的范围内完成，不能据此宣布整个算子已通过。

黑盒设计依据规范和比较器，白盒设计依据实际分支、布局、尾部和依赖。用例生成、静态审查、CPU 模型、目标编译、NPU 执行、官方评测逐层记录。关闭或跳过一项只表示没有做；空结果、重复用例 ID、低/高各一例无 golden 冒烟都不能称全量精度验证。详见 [分层验证](validation-layers.md)。

`inflight_requests` 推荐统一字段：`attempt_path`、`phase`、`submission_id`、`request_identity_confirmed`、`payload_sha256`、`source_files`。`source_files` 为原请求的 `[{path,sha256}]`，未知写 null；同源码历史结果放在独立 `association` 对象，不补填成原请求身份。接收旧字段时总控保留原文并注明映射，避免混淆单文件 SHA 与整个 payload SHA。检查器只保留这些对象，不认证字段或实时状态。

## 总控接收

```bash
python3 /absolute/skill-root/scripts/check_handoff.py --task /absolute/task.json --result /absolute/result.json
```

- 退出 `0`：声明与当前文件一致，结果达到它声称的阶段通过条件。
- 退出 `1`：声明/文件不一致，或者结果有效但为 FAIL/CONCERN/incomplete。读取 `structurally_valid`、`errors` 与 `issues`，不要混为一谈。
- 退出 `2`：JSON/schema 格式错误，先修交接格式，保留原产物。

校验器检查身份、SHA、当前文件、声明的动作/写入范围、必需产物及检查条目。它不监控进程写入，不验证授权真实来源，不认证日志真实性，也不查询平台；输出 `evidence_authenticity_verified=false`。总控仍要读取关键日志和回执，核对命令、退出码、构建/模型/比较器版本。不要把校验器退出 0 当 NPU 通过。

结构一致的 FAIL 交接可以完成“找到反例”任务，并供下一轮诊断或授权探索使用。`ready_for_next_step=false` 不代表比赛禁止尝试，也不替代总控的部分得分决策。源码哈希不一致则不能拿旧结论晋升当前文件，应定向重查。

## 状态与恢复

总控按需在项目保存状态索引：任务 ID、角色、状态、精确输入/输出 SHA、报告路径、当前候选、官网选定及在途请求。已有项目有同等记录时直接复用，不制造第二份竞争状态。

恢复时先查实际产物与 hash，再恢复尚未完成的阶段。源码/契约/比较器改变只使依赖它的结果失效；不能只看勾选框继承成功。Judge 的 attempt、dispatch marker、receipt/result 仍由原提交脚本管理，交接 JSON 不替代其防重放状态机。

总控负责官方得分、部分覆盖、探索预算、主版晋升和最终交付；Judge 独占 POST；其他角色交产物与建议。所有并行任务结束时都要回收结果或报告未完成状态，不留下未交代的提交与后台请求。
