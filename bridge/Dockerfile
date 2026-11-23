FROM node:24-alpine

ENV NODE_ENV=production
WORKDIR /app

COPY package.json ./
COPY server.js ./
COPY lib ./lib
# 只带 anti_content（登录签名生成器）；会话文件由 /data 卷提供，不能进镜像
COPY runtime/anti_content ./runtime/anti_content

# 会话文件与缓存写到 /data（挂卷持久化），进程以非 root 运行
ENV TEMU_OPS_DATA=/data \
    PORT=8898 \
    HOST=0.0.0.0

RUN addgroup -S bridge && adduser -S bridge -G bridge \
    && mkdir -p /data && chown -R bridge:bridge /app /data
USER bridge

EXPOSE 8898

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD node -e "require('http').get('http://127.0.0.1:8898/ping',r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"

CMD ["node", "server.js"]
