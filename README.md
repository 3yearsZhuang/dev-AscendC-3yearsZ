# dev-AscendC-3yearsZ

面向 Ascend C / CANN 算子赛事的技能集。一个总控按任务委托五个专职技能，将方案、实现、审查、调优与 CANNJudge 评测串起来，并按真实评分和源码证据选择版本。

当前版本：**1.2.0**。技能调用标识使用小写，例如 `$dev-ascendc-3yearsz`。

## 技能分工

| 技能 | 职责 |
|---|---|
| [dev-ascendc-3yearsz](skills/dev-ascendc-3yearsz/SKILL.md) | 总控：任务路由、证据接收、评分选版、提交协调 |
| [dev-ascendc-plan-3yearsz](skills/dev-ascendc-plan-3yearsz/SKILL.md) | 核实题目契约、环境与计分，提出方案和实验假设 |
| [dev-ascendc-implement-3yearsz](skills/dev-ascendc-implement-3yearsz/SKILL.md) | 按真实工程入口实现候选，执行相称的本地检查 |
| [dev-ascendc-review-3yearsz](skills/dev-ascendc-review-3yearsz/SKILL.md) | 审查数值、访存、资源、尾部与同步，返回绑定源码的结论 |
| [dev-ascendc-tune-3yearsz](skills/dev-ascendc-tune-3yearsz/SKILL.md) | 根据性能和失败证据设计候选、消融与复测 |
| [dev-ascendc-judge-3yearsz](skills/dev-ascendc-judge-3yearsz/SKILL.md) | 下载工程、冻结版本、执行已授权提交、查询与恢复 |

新题通常走 `Plan → Implement → Review → Judge`；优化通常走 `Tune → Review → Judge`。简单任务只调用需要的角色。有子 agent 工具时可以委托并行的独立工作；没有时由同一 agent 分角色执行，并说明实际验证方式。

五个子技能共享总控目录中的参考和脚本。单独复制一个子技能会缺少资源，**六个目录必须保持同级并一起安装、更新**。

## 安装

在 Codex 中可直接提出：

```text
从 https://github.com/3yearsZhuang/dev-AscendC-3yearsZ 安装 skills/ 下的全部六个技能，保持同级目录。
```

也可以手动克隆：

```bash
git clone https://github.com/3yearsZhuang/dev-AscendC-3yearsZ.git
cd dev-AscendC-3yearsZ
```

将 `skills/` 下六个 `dev-ascendc…-3yearsz` 目录复制到 `~/.codex/skills/`；设置了 `CODEX_HOME` 时，目标为 `$CODEX_HOME/skills/`。已有同名技能时，先备份并移走六个旧目录，再整套替换。按客户端的技能刷新方式重新加载；未出现时重启客户端。

六个技能已包含工作流所需的共享资源，无须安装其他技能。

## 使用

完整开发：

```text
使用 $dev-ascendc-3yearsz，根据这道题的题面和工程实现算子。先核实接口与计分规则，再委托开发和审查。
```

专项审查：

```text
使用 $dev-ascendc-review-3yearsz，审查当前候选的数值误差、尾部访问和同步，并标明实际验证层次。
```

总控继承用户已经明确的目标和操作授权。Judge 是唯一提交协调者；下载、查询和接收候选本身不会产生正式提交授权。正式提交前冻结源码，受理状态未知时保留记录，已有提交 ID 时续查同一个请求。

技能区分静态检查、CPU 模型、目标编译、NPU 执行与官方评测。隐藏测试点保持未知，允许部分得分的比赛按官方评分选版，并披露失败覆盖。

## 脚本与运行环境

使用脚本时需要 Python；持久化 CANNJudge 包装层使用 `fcntl` 和目录 `fsync`，支持 Linux/macOS 等 POSIX 环境。目标编译和 NPU 验证另外需要与题目匹配的 CANN、工具链和设备。

在仓库根目录准备运行环境：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r skills/dev-ascendc-3yearsz/scripts/requirements.txt
.venv/bin/python skills/dev-ascendc-3yearsz/scripts/cannjudge.py --help
```

常规客户端需要 `requests`；仅 RSA 密文登录兼容路径额外需要 `pycryptodome`。登录与提交的操作说明见 [CANNJudge 工作流](skills/dev-ascendc-3yearsz/references/cannjudge-workflow.md)。

| 脚本 | 能验证或执行的范围 |
|---|---|
| `cannjudge.py` | 工程准备、持久化的一次 POST、同 ID 查询、未知请求关联与原始结果保存；attempt 锁只覆盖同一目录 |
| `verify_evidence.py` | 离线检查源码与评测记录的一致性；服务端源码比对限单文件 `kernel.asc` / `kernel_cpp` |
| `check_handoff.py` | 离线检查委托身份、文件 SHA、产物和声明范围；不监控进程权限，也不认证日志或官方结果真实性 |

本仓库只分发技能与离线工具。本地模拟答题 OJ（`oj/`：容器内真实编译 + CPU 孪生判题 + 网页答题界面）、三站赛事工程与评测证据统一放在私有仓库 **AscendC-workbench**，不再随本仓库发布。`verify_evidence.py` 与 `check_handoff.py` 不能证明算法正确或 NPU 通过；源码相同的历史结果也不能单独证明某次未知 POST 的身份。

## 验证

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider skills/dev-ascendc-3yearsz/tests
```

v1.2.0 包含 **187 项离线回归测试**，覆盖提交恢复、证据一致性和角色交接。CI 执行这些测试和三个脚本的帮助命令。离线测试不登录 CANNJudge，也不发起正式提交；本版本尚未完成真实服务登录/提交、CANN 编译或 NPU 验证。

相对 1.1.0，本版本按算子开发实测补充了会改变决策的窄结论：UB 向量操作数的 32 B 对齐要求与启动级 Runtime Error 签名、C++14 下 dtype 分支的实例化陷阱、元素级流式算子的核数与分块经验、非 Pass 提交可读耗时与探针边界、题目分随全站最优耗时漂移以及榜单改选判据，并把浏览器通道的探活与“账号历史 + 服务端源码 SHA”恢复步骤写入 Judge 工作流。未新增脚本与测试，测试计数不变。

## 许可

自有代码和文档采用 [Apache License 2.0](LICENSE)。

内置 SDK 保留其 [CANN Open Software License Agreement Version 2.0](skills/dev-ascendc-3yearsz/scripts/vendor/cannjudge_submit/LICENSE)，具体范围见 [第三方许可](THIRD_PARTY_NOTICES.md)。
