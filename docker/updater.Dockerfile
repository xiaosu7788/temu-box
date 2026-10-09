FROM alpine:3.21

# 边车只装 CLI：它通过挂载的 docker.sock 操作宿主守护进程，容器内不需要 dockerd。
# alpine 的 docker-cli / docker-cli-compose 是多架构包，x86_64 与 arm64 服务器都能直接构建。
RUN apk add --no-cache python3 bash git docker-cli docker-cli-compose tzdata ca-certificates

# 项目目录是从宿主机挂进来的，属主不一定是容器内的 root；不放开 safe.directory，
# git 会以 dubious ownership 为由直接拒绝操作。
RUN git config --system --add safe.directory '*'

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/updater
COPY docker/updater/server.py /opt/updater/server.py

EXPOSE 8899
CMD ["python3", "/opt/updater/server.py"]
