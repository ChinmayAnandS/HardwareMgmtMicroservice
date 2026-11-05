# Static admin UI served by Nginx. Build from the repository root:
#   docker build -f ui/Dockerfile -t hardware-ui .
FROM python:3.12-slim AS schema
WORKDIR /build
RUN pip install --no-cache-dir grpcio-tools==1.85.1 protobuf==7.36.2
COPY proto proto
COPY scripts/generate-proto.py scripts/generate-ui-schema.py scripts/
RUN mkdir backend ui && python scripts/generate-proto.py && python scripts/generate-ui-schema.py

FROM nginxinc/nginx-unprivileged:1.28-alpine
USER root
COPY ui/nginx.conf.template /etc/nginx/templates/site.conf.template
COPY ui/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh && chown -R 101:101 /etc/nginx/conf.d
COPY --chown=101:101 ui/index.html ui/app.js ui/rpc.js ui/style.css /usr/share/nginx/html/
COPY --from=schema --chown=101:101 /build/ui/schema.js /usr/share/nginx/html/schema.js
USER 101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD wget -q -O /dev/null http://127.0.0.1:8080/health || exit 1
ENTRYPOINT ["/entrypoint.sh"]
