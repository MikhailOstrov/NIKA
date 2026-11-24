FROM ubuntu:noble AS base

ENV CCACHE_DIR=/ccache
USER root

COPY scripts /nika/scripts
COPY conanfile.py /nika/conanfile.py
COPY CMakePresets.json /nika/CMakePresets.json
COPY CMakeLists.txt /nika/CMakeLists.txt
COPY requirements.txt /nika/requirements.txt

# tini is an init system to forward interrupt signals properly
RUN apt update && apt install -y --no-install-recommends sudo tini curl ccache python3 python3-pip pipx cmake build-essential ninja-build

# Install Conan
RUN pipx install conan && \
    pipx ensurepath

FROM base AS devdeps
WORKDIR /nika

SHELL ["/bin/bash", "-c"]
RUN python3 -m venv /nika/.venv && \
    source /nika/.venv/bin/activate && \
    pip3 install -r /nika/requirements.txt

ENV PATH="/root/.local/bin:$PATH"
RUN conan remote add ostis-ai https://conan.ostis.net/artifactory/api/conan/ostis-ai-library --insecure && \
    conan profile detect && \
    conan install . --build=missing

# Use the exact Conan packages used to compile NIKA. Mixing these libraries with
# release archives from another patch version causes undefined ScLogger symbols
# and prevents all NIKA extension modules from loading at runtime.
RUN SC_MACHINE_PACKAGE="$(dirname "$(dirname "$(find /root/.conan2/p/b -path '*/p/bin/sc-machine' -print -quit)")")" && \
    SCL_MACHINE_PACKAGE="$(dirname "$(dirname "$(find /root/.conan2/p -path '*/p/lib/libinference.so' -print -quit)")")" && \
    mkdir -p install/sc-machine install/scl-machine && \
    cp -a "$SC_MACHINE_PACKAGE"/bin "$SC_MACHINE_PACKAGE"/lib install/sc-machine/ && \
    cp -a "$SCL_MACHINE_PACKAGE"/lib install/scl-machine/

FROM devdeps AS devcontainer
RUN apt install -y --no-install-recommends cppcheck valgrind gdb bash-completion ninja-build curl
ENTRYPOINT ["/bin/bash"]

FROM devdeps AS builder
COPY . .
RUN --mount=type=cache,target=/ccache/ cmake --preset release-conan && cmake --build --preset release

# Gathering all artifacts together
FROM devdeps AS final

COPY --from=builder /nika/scripts /nika/scripts
COPY --from=builder /nika/install /nika/install
COPY --from=builder /nika/build/Release /nika/build/Release
COPY --from=builder /nika/.venv /nika/.venv

WORKDIR /nika

EXPOSE 8090

ENTRYPOINT ["/usr/bin/tini", "--", "/nika/scripts/docker_entrypoint.sh"]
