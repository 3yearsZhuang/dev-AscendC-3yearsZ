#!/usr/bin/env bash
# 构建本地 CANN 编译镜像（不依赖 Docker Hub 大镜像）。
#
# 用法：
#   skills/dev-ascendc-3yearsz/scripts/local_cann/build-image.sh \
#       [--cann-version 9.0.0] [--tag local/cann:9.0.0-toolkit] [--apt-mirror mirrors.ustc.edu.cn]
#
# 说明：
#   - 从华为官方 Artifactory 镜像站下载 toolkit .run（aarch64/x86_64 自动选择），不用 Docker Hub 大镜像：
#     实测 Docker Hub 的 ascendai/cann 在本机约 26 MB/min，4.5 GB 要数小时；该镜像站约 2 MB/s。
#   - 只装 toolkit，不装 ops/nnal：目标侧编译只需要 ccec/bisheng、tikicpulib 的 CMake 后端和 lib64。
#   - 下载包缓存在本目录 cache/（约 1.1 GB），已 gitignore，重复构建不再下载。
set -euo pipefail

CANN_VERSION="9.0.0"
TAG=""
MIRROR="https://ascend.devcloud.huaweicloud.com/artifactory/cann-run/software"
APT_MIRROR="mirrors.ustc.edu.cn"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --cann-version) CANN_VERSION="${2:?}"; shift 2 ;;
        --tag) TAG="${2:?}"; shift 2 ;;
        --apt-mirror) APT_MIRROR="${2:?}"; shift 2 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "未知参数: $1" >&2; exit 2 ;;
    esac
done
TAG="${TAG:-local/cann:${CANN_VERSION}-toolkit}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE="${HERE}/cache"
mkdir -p "${CACHE}"

case "$(uname -m)" in
    arm64|aarch64) PKG_ARCH="aarch64"; DOCKER_ARCH="linux/arm64" ;;
    x86_64|amd64)  PKG_ARCH="x86_64";  DOCKER_ARCH="linux/amd64" ;;
    *) echo "不支持的宿主架构: $(uname -m)" >&2; exit 2 ;;
esac

PKG="Ascend-cann-toolkit_${CANN_VERSION}_linux-${PKG_ARCH}.run"
URL="${MIRROR}/${CANN_VERSION}/${PKG_ARCH}/${PKG}"

if [[ ! -s "${CACHE}/${PKG}" ]]; then
    echo "[1/3] 下载 ${URL}"
    curl -fSL --retry 3 -C - -o "${CACHE}/${PKG}.part" "${URL}"
    mv "${CACHE}/${PKG}.part" "${CACHE}/${PKG}"
else
    echo "[1/3] 复用缓存 ${CACHE}/${PKG}"
fi

# .run 是自解压 shell 脚本，末尾带校验和；大小与 SHA256 一起记录，便于复核。
EXPECTED_BYTES="$(stat -f %z "${CACHE}/${PKG}" 2>/dev/null || stat -c %s "${CACHE}/${PKG}")"
echo "      大小 ${EXPECTED_BYTES} 字节  sha256 $(shasum -a 256 "${CACHE}/${PKG}" | cut -d' ' -f1)"

echo "[2/3] 构建镜像 ${TAG}（${DOCKER_ARCH}）"
docker build \
    --platform "${DOCKER_ARCH}" \
    --build-arg "CANN_PKG=${PKG}" \
    --build-arg "CANN_VERSION=${CANN_VERSION}" \
    --build-arg "APT_MIRROR=${APT_MIRROR}" \
    -t "${TAG}" \
    -f "${HERE}/Dockerfile" \
    "${HERE}"

echo "[3/3] 完成：${TAG}"
docker image inspect "${TAG}" --format '{{.Id}} {{.Architecture}} {{.Created}}'
