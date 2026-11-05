"""Container health probe: exit 0 when the local gRPC server reports SERVING."""
import os
import sys
import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

try:
    with grpc.insecure_channel('127.0.0.1:' + os.getenv('GRPC_PORT', '50051')) as channel:
        reply = health_pb2_grpc.HealthStub(channel).Check(health_pb2.HealthCheckRequest(), timeout=3)
    sys.exit(0 if reply.status == health_pb2.HealthCheckResponse.SERVING else 1)
except grpc.RpcError:
    sys.exit(1)
