# 来源与复用范围

展示名称为 `dev-AscendC-3yearsZ`，目录与调用标识为 `dev-ascendc-3yearsz`，版本见 `../VERSION`。

## cann-learning-hub / cannjudge-submit

- 仓库：[cann/cann-learning-hub](https://gitcode.com/cann/cann-learning-hub)。
- 评估固定提交：`62295ba213239a470952b43eb37938a53b883c5a`，2026-11-01 读取。
- 技能：[skills/cannjudge-submit](https://gitcode.com/cann/cann-learning-hub/tree/62295ba213239a470952b43eb37938a53b883c5a/skills/cannjudge-submit)。
- 复用题目 URL 解析、真实工程下载、可编辑文件收集、只读模板核对、两类工程 payload、终端登录及会话存储模块。
- 原样 vendoring 的文件位于 `scripts/vendor/cannjudge_submit/`。保留上游声明和随附 CANN Open Software License Agreement Version 2.0；发布这些文件时一并保留该协议。它不是 MIT/Apache 许可。
- 本地新增包装层负责请求持久化、防重复 POST、原始结果保存与源码关联。不能据上游 CLI 的展示排序宣称官网排名；正式提交不走上游裸 `submit`。

上游文档引用的 `ops-direct-invoke-flash` 不构成本技能依赖。核函数开发依据本题契约、允许的技术资料和本技能优化参考；没有安装那个技能也能执行本工作流。

## 早期 ascend-operator-acm

参考作者早期维护的 `ascend-operator-acm` 技能，2026-11-01 完成评估。吸收：真实评分优先、探索/晋升分离、数值与 GM/UB/异步模型边界、失败诊断、源码/回执绑定、未知 POST 不重放、经验按证据更新。它不是本套件的运行依赖。

新技能不内置旧赛区/账号，不继承旧任务的唯一教程限制，不复制历史实验日志。已有项目可以继续使用其已验证的浏览器通道；项目工具不随本套件分发。

本文件记录影响后续维护的来源与边界。公开分发仅包含技能、共享资源和离线测试，不依赖原项目的本地评估文档。

维护脚本时可额外安装 pytest，运行 `python -m pytest tests -q`；测试禁止真实网络与用户会话访问。升级 vendor 时重新核对固定提交、文件哈希、许可与这些离线回归，新的线上行为仍需独立验证。

## 1.1.0：开发技能的委托结构

2026-11-01 继续评估后，采用“总控 → 专职技能 → 结构化交接 → 总控复核”的组织方式，六个同级技能一起安装。总控保留评分选版和唯一提交协调；执行角色为 Plan、Implement、Review、Tune、Judge。共享知识与脚本仍由本目录维护，避免六份副本漂移。

参考源：

- [cannbot / ops-direct-invoke-flash](https://gitcode.com/cann/cannbot/tree/42d62f162366455a0f23ccbfecaaa9cece4f7855/plugins-official/ops-direct-invoke-flash)，固定应用仓提交 `42d62f162366455a0f23ccbfecaaa9cece4f7855`：复杂度决定委托粒度、独立评审、冻结版本后重叠等待、定向回退。
- 该插件 `plugin-sources.json` 真正列出的共享依赖为 `ascendc-st-design` 与 `ascendc-whitebox-design`。其 `vendor/cannbot-skills` 固定提交为 `c35e9609c758bf605fb7e651b915b8af841626a8`；按该版本评估两者的黑盒约束与白盒路径推导。
- [cannbot / ops-perf-optimize](https://gitcode.com/cann/cannbot/tree/42d62f162366455a0f23ccbfecaaa9cece4f7855/plugins-official/ops-perf-optimize)：旁支参考，非 Flash 的直接依赖；吸收角色交接、恢复时核对产物和已知基线失败分类。
- [cannbot-skills](https://gitcode.com/cann/cannbot-skills/tree/17ebcd2d7c4cda5781ce54750303a3aabc5b5abe/ops) 提交 `17ebcd2d7c4cda5781ce54750303a3aabc5b5abe`：抽读 env-check、tiling、precision、sync-audit、profiling、perf-optimize 和理论估计，作为可选专题的评估来源，不把全仓视为 Flash 的传递依赖。
- [ascendc-ops-project](https://gitcode.com/cann/cann-learning-hub/tree/62295ba213239a470952b43eb37938a53b883c5a/skills/ascendc-ops-project)：吸收传统工程原型/Host/Tiling/Kernel/ACLNN 分层，落在条件参考中。

没有原样分发上游插件、项目 AGENTS/hooks、黑白盒生成器或 gate，也没有复制其模板。吸收时按赛事真实接口适配 PyTorch/ACLNN 与 `run_kernel` 的差异；区分无 golden 冒烟和全量精度验证；在交接校验中拒绝把空检查和重复检查名称当成有效通过。新 `check_handoff.py` 只承诺声明与文件一致性，明确不认证执行事实。实际职责和验收规则见 [委托协议](delegation.md) 与 [分层验证](validation-layers.md)。
