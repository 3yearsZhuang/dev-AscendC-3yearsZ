# 本地提交前先行编译

在提交前判断「这段源码能否被目标 CANN 工具链接受」，不依赖昇腾设备。属于 [分层验证](validation-layers.md) 中的**目标编译**层，只回答编译这一件事。

## 边界

| 能证明 | 不能证明 |
|---|---|
| 指定源码在指定 CANN/Bisheng 版本下编译通过 | 数值、累加顺序、舍入与比较器语义 |
| 宏展开、类型推导、`__gm__` 属性、C++14 两分支实例化等硬错误 | 运行时错误、越界、同步缺失、队列槽位不足 |
| 目标架构（`--npu-arch`）接受该 template/指令组合 | 设备侧性能、跨核异步行为、隐藏点覆盖 |
| 打包后源码与本地源码编译结果一致（同一份字节） | 官方评分、部分得分、排名 |

**编译通过 ≠ 正确**。历史上有本地模型通过但正式 WA、以及本地忽略的宏缺陷在正式提交触发 Compile Error 的反例。本层最擅长的是后一类。

## 环境

Docker（或任何 Linux 容器运行时）+ 与题目匹配的 CANN toolkit。不需要 NPU、驱动或固件；容器不挂 `--device`，任何走到设备打开的路径都会立刻失败。

本套件在 Apple Silicon（macOS arm64）上实测通过，容器为 `linux/arm64`。CANN 只提供与容器架构一致的包：arm64 宿主用 `aarch64` 包，x86_64 宿主用 `x86_64` 包。**不要指望 Rosetta/qemu 转译**：CANN 工具链是原生二进制，转译下的行为没有验证价值。

题目元数据里的 `cann_version` 必须与实际镜像一致（例如 `problem.json` 写 `9.0.0` 就用 9.0.0 的包）。版本不一致时编译结论不能直接搬到正式提交。

## 一次性准备

```bash
skills/dev-ascendc-3yearsz/scripts/local_cann/build-image.sh --cann-version 9.0.0
```

脚本从华为官方 Artifactory 下载 toolkit（约 1.1 GB，缓存在 `cache/`，已 gitignore），装进 `ubuntu:22.04`，产出 `local/cann:9.0.0-toolkit`（约 8 GB）。

实测要点：

- **别拉 `ascendai/cann` 镜像**：本机实测 Docker Hub 约 26 MB/min，4.5 GB 要数小时；官方镜像站约 2 MB/s。
- apt 必须换国内源，且 **arm64 的官方 ubuntu 镜像用的是 `ports.ubuntu.com` 不是 `archive.ubuntu.com`**；只替换后者会静默沿用慢源（约 40 KB/s）。
- 基础镜像没有 `ca-certificates`，此刻用 `https` 源会因证书不受信全部失败，必须先走 `http`。
- 不装 ops/nnal：目标编译只需要 ccec/bisheng、tikicpulib 的 CMake 后端和 `lib64`。

## 每次提交前

```bash
skills/dev-ascendc-3yearsz/scripts/local_cann/precompile-project.sh <工程目录> [--arch dav-2201] [--mode npu]
```

在工程目录生成 `build-precompile/`，退出码 0 表示通过、非 0 表示编译失败。默认 `--mode npu` 只编译不执行；`--mode cpu` 会走 CPU 调测分支，需要额外确认调测库在 arm64 可用。

实测耗时：pdist 621 行 kernel 约 3.5 秒（含容器启动），三个 xiamen 工程均通过。

## 负控

编译沙箱本身也需要验证：只有当它能对已知缺陷报错时，「通过」才有意义。保留两个最小负控，改动任何编译参数后重跑：

| 负控 | 注入方式 | 预期 |
|---|---|---|
| 宏声明 | `GM_ADDR a = x, b = y;` —— `GM_ADDR` 展开为 `__gm__ uint8_t*`，星号只作用于第一个声明符 | `non-pointer variable b is not allowed to have any CCE memory attribute` |
| C++14 分支 | `if (sizeof(T)==4) { LocalTensor<float> f; Adds(f, t, ...); }` 与 half 张量同处一个函数 | `no matching function for call to 'Adds'`（两分支都为每种 dtype 实例化） |

两个负控在 9.0.0 上均按预期失败（退出码 2）。第一项正是 3yearsZ 记录里 v19 的正式 Compile Error 成因，CPU 模型用 `typedef` 掩盖了它，而这里能复现。

## 从「编译」扩展到「判题」

只做编译能拦 CE，但拦不住 WA。若还需要数值判定，可在同一镜像上搭本地 OJ：真实编译后以 CPU 孪生模式执行工程自带的 `sample.py` 比较器，判 AC/WA/CE/RE。做法的两个关键约束（`main.asc` 的全局 `check()` 与 `AscendC::check` 冲突需在评测副本上改名；CPU 模式必须显式带 `--npu-arch`，否则 bisheng 崩溃）见私有工作区 AscendC-workbench 的 `oj/README.md`。CPU 仿真耗时只能做同机相对比较，不能当作 NPU 性能。

## 判读与记录

- 报 `COMPILE_OK` 时记录：源码 SHA、CANN 版本、`--npu-arch`、镜像 ID、命令、耗时。
- 报错时保留**首次**失败原文，不要只留最后一次。
- 只编译了部分文件、用了缓存跳过重编、或 `--mode cpu` 未真正跑起来，都要写成「未覆盖」而不是通过。
- 源码改动后旧结论作废：哈希变了就重新编译，不能把上一次的 `COMPILE_OK` 转给新字节。
- 通过之后仍要按风险补 CPU 模型/局部探针（数值、尾部、UB 预算）；本层不替代它们。

## 故障排查

| 现象 | 原因 |
|---|---|
| `find_package(ASC REQUIRED)` 失败 | 没 `source set_env.sh`，或 `CMAKE_PREFIX_PATH` 未含 `tikicpulib/lib/cmake` |
| `ASCEND_HOME_PATH not set` | 工程 `run.sh` 要求先 source；本套件在容器内已 source |
| 链接期找不到 `tiling_api`/`register` | 只装了 toolkit 却按传统注册工程链接 ops 库；核函数直调工程不需要 |
| `gmake: *** No rule to make target` | 容器内没有该工程声明的源文件，检查只读挂载路径 |
| BuildKit 报 `operation not permitted` 写 `~/.docker/buildx` | 沙箱限制；用 `DOCKER_BUILDKIT=0` 走 legacy builder |
