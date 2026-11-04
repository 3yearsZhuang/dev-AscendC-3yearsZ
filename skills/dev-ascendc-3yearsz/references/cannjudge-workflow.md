# CANNJudge 工程与可恢复提交

首次使用客户端、下载工程、正式提交、恢复或读取结果时读取。计分与配额见 [任务契约](contest-contract.md)。下文的 `ASCEND_SKILL_DIR` 指本技能目录，`python3` 指已安装依赖的解释器。

## 选择传输方式

已有项目 helper 能复用已登录浏览器，且用户选择继续该通道时，沿用原 helper。遵守已有浏览器非侵入约定，不导出 Cookie 来切换到 requests。原项目 `tools/judge.py` 与本技能 SDK 的状态目录不是同一协议，不能直接把其中一个的 `--resume` 用到另一个目录。

新环境可用本技能的 SDK 包装层：

```bash
python3 -m venv .venv-cannjudge
.venv-cannjudge/bin/python -m pip install -r "$ASCEND_SKILL_DIR/scripts/requirements.txt"
.venv-cannjudge/bin/python "$ASCEND_SKILL_DIR/scripts/cannjudge.py" --help
```

常规操作只需要 requests；RSA 密码解密额外需要 pycryptodome。持久化包装入口使用 `fcntl` 与目录 `fsync`，目前限 Linux/macOS 等 POSIX 系统；上游会话模块的 Windows 支持不代表新包装层支持 Windows。SDK 来自固定上游版本，正式提交增加了本地状态记录，详见 [来源](provenance.md)。未经本次真实服务测试，不宣称当前线上登录/提交端到端已通过。

## 登录与会话

有效会话直接复用。需要新登录时沿用用户已选方式；没有选择时只询问缺失的登录方式，优先提供本地终端隐藏输入。用户已授权提交且信息齐全时无需再次询问是否提交。

```bash
python3 "$ASCEND_SKILL_DIR/scripts/cannjudge.py" login
```

终端输入的密码不回显。用户若明确选对话输入，只以运行时内存/stdin 传给 SDK，不拼接到 shell 参数或临时文件；缺少安全运行时通道时改用终端。只向 CANNJudge 官方 HTTPS 登录接口发送凭据。RSA 只作为用户选择的兼容方式，不因可用而强迫使用。

上游默认会话缓存为 `~/.cannjudge/session.json`；Linux/macOS 用 0600 权限，Windows 用当前用户 DPAPI。Cookie 属于凭据，不写入实验目录或交付包。401 清除失效会话；403 先检查题目/小组权限；不自动重试登录或切换验证码。验证码仅在用户选择或服务端明确要求时启用，由用户读取。

## 下载和工程检查

```bash
python3 "$ASCEND_SKILL_DIR/scripts/cannjudge.py" info --problem-url "$PROBLEM_URL"
python3 "$ASCEND_SKILL_DIR/scripts/cannjudge.py" download --problem-url "$PROBLEM_URL" --output ./problem-work
```

客户端按小组→比赛→题目解析完整链接，并携带 contestId 防止同名题误选。公开资源无需强制新登录。下载真实 package ZIP 和 template，保留 `.cannjudge-project.json` 的题目绑定；ZIP 拒绝路径越界、符号链接和覆盖已有工程。

| 工程 | 入口与 payload | 必须核实 |
|---|---|---|
| `npu_kernel_dev` | 根目录 `.asc/.h` 可编辑文件；`files: [{path, content}]` | 当前模板的 editable/只读文件、真实 `run_kernel`、类型/stream；不是 PyTorch 注册工程 |
| 传统工程 | `op_host`、`op_kernel` 与 tiling 字段 | 唯一匹配源文件、模板/题目绑定与允许修改范围；不能声称拥有与核函数包完全相同的自动只读检查 |

核函数打包器收集当次模板可编辑文件及允许的根目录 `.asc/.h` 辅助文件。不可提交 `main.asc`、`judge.asc`、`data_utils.h` 评测框架，不把构建产物、日志和会话打入 payload。只读文件发生修改时检查会失败；应重新核对修改范围，不通过移除元数据掩盖错题提交。

默认不去注释。上游文本读取可能规范化 CRLF/BOM，因此正式绑定的是打包后 UTF-8 内容；需要核对源字节与 payload 的差异。注释处理若真的必要，采用词法可靠的转换，不能用会破坏字符串与 token 间隔的正则。

## 冻结与正式提交

用一个独立 attempt 目录保存一次候选。准备阶段查询题面/模板和冻结 payload，不提交：

```bash
python3 "$ASCEND_SKILL_DIR/scripts/cannjudge.py" prepare --problem-url "$PROBLEM_URL" --project-dir ./problem-work/project --attempt-dir ./evaluations/candidate-a --expected-testcases 15
python3 "$ASCEND_SKILL_DIR/scripts/cannjudge.py" submit --attempt-dir ./evaluations/candidate-a
python3 "$ASCEND_SKILL_DIR/scripts/cannjudge.py" query --attempt-dir ./evaluations/candidate-a
```

这里的 15 只是命令示例，必须换为当前题目确认的完整点数；未知时不填。不要从某个赛区继承。修改源码后重新 prepare 新目录；已有 attempt 是被冻结的版本，不随工作文件变化。

提交前协调者检查账号配额、最新提交、冷却和所有未知请求；本地 attempt 锁只防同目录并发。需要多主机/多通道时仍用一个协调者，不宣称锁住了整个平台账号。

正式发送前写入最终 payload（含当前账号 ID，**不含 Cookie/密码**）和 dispatching 状态。即使随后进程退出，也不能据没有回执认定没发送。包装层对一次 attempt 不自动重试 POST；明确冷却拒绝应保留原始拒绝，遵从服务端时限，由协调者决定下一次独立尝试。状态不明时不能新建目录来绕过防重放。

## 标签页健康检查与未知受理恢复

- 后台标签可能被浏览器冻结：`execute javascript` 返回空串、异步 `fetch` 的结果读不到，轮询只会超时，但 POST 可能已经发出。构造请求前先做一次无副作用探活（返回非空即健康）；不健康就 reload 该标签、等它恢复可响应再继续。探活必须在写入 attempt 状态之前完成，否则一次未发出的请求会被记成未知受理。
- 轮询超时后不重投。用 `/api/submissions/user/<uid>` 找该题、该时间窗内的新记录，取回 `/api/submissions/<id>` 中服务端 `kernel.asc` 字节并与本地冻结源码比对 SHA；一致才认领该 ID，并写明这是“账号历史 + 源码一致”的关联，身份确认状态按实际填写。
- 传输入口优先遍历所有窗口和标签页而不是只读活动标签：用户切换标签不应让已授权请求失败，执行也不应抢占焦点。

## 恢复与同源码关联

取得 submission ID 后，`query`/`resume` 只查询该 ID，保存原始结果和摘要。Running、Pending 或未知状态不算完成；按需稍后继续查询，不把一次 GET 当作最终验收。交付时保留仍在运行的 ID。

POST 超时或缺 ID 时先查该账号、该题的提交记录。候选 ID 的服务端源码可读且与全部提交文件一致、题目与账号一致，才可以关联结果：

```bash
python3 "$ASCEND_SKILL_DIR/scripts/cannjudge.py" reconcile --attempt-dir ./evaluations/candidate-a --submission-id "$SUBMISSION_ID"
```

这个关联只证明源码等价；此前相同源码可能提交过多次。记录 `request_identity_confirmed: false`，保留原请求未知状态。时间窗、账号与唯一提交记录等额外证据才能进一步确认原请求身份。不要把“找到通过的同源码”写成“已经恢复原请求”，也不要据关联记录允许重投。

## 结果、排名与晋升

摘要保留顶层原状态、实际返回点数、可见/隐藏点数、通过/失败/未知点数、预期总点数和完整覆盖判断。`precision_ratio` 原样保留；单位不明的耗时保留原值。失败点的零耗时不纳入速度对照。查询还需核对返回 ID、题目、账号及可获得的源码；响应身份不符时保留诊断原文，不覆盖已绑定结果。完整响应仍是核查依据，摘要不替代原始记录。

`rank` 应保留服务端返回顺序和分数字段。题目的 latest 端点返回列表不等于已证明它就是全站总榜；需要官方名次时读取相应页面/榜单快照，不能自己按 Pass 优先重排后冠名官方排名。

晋升前核对主源码、发送快照和完整结果；可用 `scripts/verify_evidence.py --help` 检查项目已有的 `verified-result.json`：

```bash
python3 "$ASCEND_SKILL_DIR/scripts/verify_evidence.py" --source ./problem/kernel.asc --record ./problem/verified-result.json --repo-root .
```

它兼容当前项目的 `source_sha256`、`submission_id`、`result`、可选 `source_snapshot`/`passed_testcases` 等字段，离线检查 SHA、ID、状态和声称的覆盖。服务端源码核对限单文件 `kernel.asc`／`kernel_cpp`，不代表检查了任意多文件 payload；新客户端冻结目录中的多文件绑定由 `cannjudge.py` 核对。显式 `source_transform` 只记录输入/输出 SHA 和转换方法，不证明转换语义。退出码 0 表示所提供证据一致，1 表示不一致或未证实，2 表示格式错误。它不访问平台、不证明算法正确，也不自动晋升。

本技能保持最小闭环：准备→一次 POST→同 ID 查询→证据检查。自动全账号排队、实时配额发现、长驻监控或自动晋升需要项目层明确实现，不能仅凭文档当作现成功能。
