#!/usr/bin/env bash
# 宿主侧入口：在 macOS/Linux 上对任意算子工程做「提交前目标侧编译」。
#
# 用法：
#   skills/dev-ascendc-3yearsz/scripts/local_cann/precompile-project.sh <工程目录> \
#       [--arch dav-2201] [--mode npu]
#
# 行为：
#   - 源码只读挂载到 /work，在容器内的 /tmp/src 副本上编译，不污染待提交字节。
#   - 容器不挂 --device，也不带任何 /dev/davinci*：跑通即证明走的是纯编译路径。
#   - 产物写到宿主工程目录下的 build-precompile/，便于比对两次编译的时间与日志。
#   - 退出码透传 cmake --build：0 = 目标侧编译通过，非 0 = 与 CANNJudge 的 Compile Error 同类。
set -euo pipefail

IMAGE="${CANN_IMAGE:-local/cann:9.0.0-toolkit}"
ARCH="${NPU_ARCH:-dav-2201}"
MODE="npu"
PROJECT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --arch) ARCH="${2:?}"; shift 2 ;;
        --mode) MODE="${2:?}"; shift 2 ;;
        --image) IMAGE="${2:?}"; shift 2 ;;
        -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
        -*) echo "未知参数: $1" >&2; exit 2 ;;
        *) PROJECT="$1"; shift ;;
    esac
done

[[ -n "${PROJECT}" ]] || { echo "用法: $0 <工程目录> [--arch dav-2201] [--mode npu]" >&2; exit 2; }
[[ -d "${PROJECT}" ]] || { echo "目录不存在: ${PROJECT}" >&2; exit 2; }
PROJECT_ABS="$(cd "${PROJECT}" && pwd)"

docker image inspect "${IMAGE}" >/dev/null 2>&1 || {
    echo "镜像 ${IMAGE} 不存在，先运行本目录的 build-image.sh" >&2
    exit 3
}

# 宿主架构决定容器平台：Apple Silicon / arm64 主机走 linux/arm64，CANN 包也只提供同架构。
case "$(uname -m)" in
    arm64|aarch64) PLATFORM="linux/arm64" ;;
    x86_64|amd64)  PLATFORM="linux/amd64" ;;
    *) echo "不支持的宿主架构: $(uname -m)" >&2; exit 2 ;;
esac

mkdir -p "${PROJECT_ABS}/build-precompile"

echo "工程:   ${PROJECT_ABS}"
echo "镜像:   ${IMAGE} (${PLATFORM})"
echo "目标:   ${MODE} / ${ARCH}"
echo

set +e
docker run --rm \
    --platform "${PLATFORM}" \
    --network none \
    -v "${PROJECT_ABS}:/work:ro" \
    -v "${PROJECT_ABS}/build-precompile:/out" \
    -w /work \
    "${IMAGE}" \
    -lc "source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1
         cd /tmp && rm -rf src && cp -r /work src && cd src
         cmake -S . -B /out -DCMAKE_ASC_RUN_MODE=${MODE} -DCMAKE_ASC_ARCHITECTURES=${ARCH} || exit 10
         cmake --build /out --parallel \$(nproc)"
rc=$?
set -e

echo
echo "---"
if [[ ${rc} -eq 0 ]]; then
    echo "结论: 目标侧编译通过（${MODE}/${ARCH}）。"
    echo "      这只证明该源码被目标工具链接受，不证明数值、异步行为或性能正确。"
else
    echo "结论: 编译失败（退出码 ${rc}）。在 CANNJudge 上同类问题会表现为 Compile Error。" >&2
fi
exit ${rc}
