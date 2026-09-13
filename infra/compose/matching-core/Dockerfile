FROM debian:13-slim AS build

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        libgrpc++-dev \
        libprotobuf-dev \
        pkg-config \
        protobuf-compiler \
        protobuf-compiler-grpc && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY services/careerops-matching-core ./services/careerops-matching-core
COPY proto ./proto

RUN cmake \
        -S services/careerops-matching-core \
        -B /build \
        -DCMAKE_BUILD_TYPE=Release && \
    cmake --build /build --parallel

RUN mkdir -p /runtime-root && \
    ldd /build/careerops-matching-core | \
    awk '($2 == "=>" && $3 ~ /^\//) {print $3} ($1 ~ /^\//) {print $1}' | \
    sort -u | \
    xargs -r -I '{}' cp --parents '{}' /runtime-root

FROM debian:13-slim

COPY --from=build /runtime-root/ /
COPY --from=build /build/careerops-matching-core /usr/local/bin/careerops-matching-core

USER 65532:65532

ENTRYPOINT ["/usr/local/bin/careerops-matching-core"]
