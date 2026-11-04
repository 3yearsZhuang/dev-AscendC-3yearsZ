# 提交前目标侧编译镜像：只装 CANN toolkit，不含 ops/nnal/driver。
# 目的：在没有昇腾设备的机器上验证「这段源码能否被目标工具链接受」。
ARG CANN_VERSION=9.0.0
FROM ubuntu:22.04

ARG CANN_VERSION
ARG CANN_PKG=Ascend-cann-toolkit_9.0.0_linux-aarch64.run
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai

# 换成国内镜像源：默认 ports.ubuntu.com 在本机实测约 40 KB/s，装完依赖要 20 分钟以上。
# 注意两点：
#   1) arm64 的官方 ubuntu 镜像用的是 ports.ubuntu.com（不是 archive.ubuntu.com），两条都要替换。
#   2) 基础镜像尚未装 ca-certificates，此时 https 源会因证书不受信而全部失败，
#      因此这里必须用 http（国内镜像站支持 http，apt 元数据另有签名校验）。
ARG APT_MIRROR=mirrors.ustc.edu.cn
RUN sed -i "s|http://archive.ubuntu.com/ubuntu|http://${APT_MIRROR}/ubuntu|g; \
            s|http://security.ubuntu.com/ubuntu|http://${APT_MIRROR}/ubuntu|g; \
            s|http://ports.ubuntu.com/ubuntu-ports|http://${APT_MIRROR}/ubuntu-ports|g; \
            s|https://ports.ubuntu.com/ubuntu-ports|http://${APT_MIRROR}/ubuntu-ports|g" \
        /etc/apt/sources.list \
    && ! grep -q "ports\.ubuntu\.com\|archive\.ubuntu\.com\|security\.ubuntu\.com" /etc/apt/sources.list

# 编译期依赖。cmake 走 apt，避免 ubuntu22.04 自带 cmake 3.22 与 ASC 后端冲突时难替换。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        make \
        gcc \
        g++ \
        python3 \
        python3-pip \
        python3-numpy \
        wget \
        curl \
        ca-certificates \
        file \
        patchelf \
        libblas-dev \
        gfortran \
        libblas3 \
        zlib1g \
        openssl \
        libssl-dev \
        unzip \
        git \
        vim \
        less \
        procps \
    && rm -rf /var/lib/apt/lists/*

# CANN 安装器需要 Python 运行时依赖；缺失会在 install 阶段报 tbe 相关错误。
RUN pip3 install --no-cache-dir --upgrade pip setuptools wheel \
    && pip3 install --no-cache-dir \
        attrs cython numpy decorator sympy cffi pyyaml pathlib2 \
        psutil protobuf==3.20 scipy requests absl-py

COPY cache/${CANN_PKG} /tmp/cann-toolkit.run
RUN chmod +x /tmp/cann-toolkit.run \
    && /tmp/cann-toolkit.run --quiet --install --install-for-all \
    && rm -f /tmp/cann-toolkit.run

# 安装器把 toolkit 装到 /usr/local/Ascend/cann-<version>，并维护 ascend-toolkit/latest 软链。
# 下面的 ENV 是「不 source set_env.sh 也能直接用」的一份显式副本；两者都在镜像内验证过。
# 若换 CANN 版本导致目录名变化，以 /usr/local/Ascend/ascend-toolkit/latest 软链为准。
ENV ASCEND_HOME_PATH=/usr/local/Ascend/ascend-toolkit/latest
ENV ASCEND_TOOLKIT_LATEST_HOME=/usr/local/Ascend/ascend-toolkit/latest
ENV ASCEND_OPP_PATH=/usr/local/Ascend/ascend-toolkit/latest/opp
ENV TOOLCHAIN_HOME=/usr/local/Ascend/ascend-toolkit/latest/toolkit
ENV CMAKE_PREFIX_PATH=/usr/local/Ascend/ascend-toolkit/latest/toolkit/tools/tikicpulib/lib/cmake:/usr/local/Ascend/ascend-toolkit/latest/lib64/cmake
ENV PATH=/usr/local/Ascend/ascend-toolkit/latest/bin:/usr/local/Ascend/ascend-toolkit/latest/tools/ccec_compiler/bin:/usr/local/Ascend/ascend-toolkit/latest/compiler/ccec_compiler/bin:${PATH}
ENV LD_LIBRARY_PATH=/usr/local/Ascend/ascend-toolkit/latest/lib64:/usr/local/Ascend/ascend-toolkit/latest/lib64/plugin/opskernel:/usr/local/Ascend/ascend-toolkit/latest/lib64/plugin/nnengine:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe/op_tiling:${LD_LIBRARY_PATH}

# 编译阶段不打设备日志；任何需要 driver 的路径应当在编译前就失败而不是静默降级。
ENV ASCEND_GLOBAL_LOG_LEVEL=3

WORKDIR /work
ENTRYPOINT ["/bin/bash"]
