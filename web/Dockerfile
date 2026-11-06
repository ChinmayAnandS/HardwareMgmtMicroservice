# Website image for Azure Container Apps: Nginx (static UI) + Envoy (gRPC-Web -> gRPC) in one container.
# The gRPC API is a separate app (Dockerfile.azure). Build from the repository root:
#   docker build -f web/Dockerfile -t hardware-web .
#
# Ingress: external, HTTP/1 (transport "auto"), target port 8080. Probes (HTTP, 8080):
#   startup/liveness GET /health, readiness GET /ready.
# Required env: API_HOST (the API app's hostname). See web/supervise.py for the rest.
FROM python:3.12-slim-bookworm AS schema
WORKDIR /build
RUN pip install --no-cache-dir grpcio-tools==1.85.1 protobuf==7.36.2
COPY proto proto
COPY scripts/generate-proto.py scripts/generate-ui-schema.py scripts/
RUN mkdir backend ui && python scripts/generate-proto.py && python scripts/generate-ui-schema.py

FROM envoyproxy/envoy:v1.34-latest AS envoy

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends nginx ca-certificates \
 && rm -rf /var/lib/apt/lists/* /usr/share/nginx/html/* \
 && mkdir -p /etc/envoy /etc/nginx/templates && useradd --uid 10001 --create-home service
COPY --from=envoy /usr/local/bin/envoy /usr/local/bin/envoy
COPY web/envoy.yaml /etc/envoy/envoy.yaml.template
COPY web/nginx.conf /etc/nginx/nginx.conf
COPY ui/nginx.conf.template /etc/nginx/templates/site.conf.template
COPY web/supervise.py /supervise.py
COPY ui/index.html ui/app.js ui/rpc.js ui/style.css /usr/share/nginx/html/
COPY --from=schema /build/ui/schema.js /usr/share/nginx/html/schema.js
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/health', timeout=3)"]
CMD ["python", "/supervise.py"]
