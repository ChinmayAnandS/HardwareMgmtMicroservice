# gRPC API server. Build from the repository root (the contract lives in proto/):
#   docker build -f backend/Dockerfile -t hardware-api .
FROM python:3.12-slim AS stubs
WORKDIR /build
RUN pip install --no-cache-dir grpcio-tools==1.85.1
COPY proto proto
RUN mkdir out \
 && python -m grpc_tools.protoc -Iproto --python_out=out --grpc_python_out=out --pyi_out=out proto/hardware/v1/hardware.proto \
 && touch out/hardware/__init__.py out/hardware/v1/__init__.py

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends unixodbc && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home service
COPY --from=stubs --chown=10001:10001 /build/out/hardware hardware
COPY --chown=10001:10001 backend/app app
USER 10001
EXPOSE 50051
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD ["python", "-m", "app.healthcheck"]
CMD ["python", "-m", "app.server"]
