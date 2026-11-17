"""Smoke test for a deployed API, with no credentials: TLS works, the gRPC health check says SERVING,
and OpsService/Ready (which also checks MongoDB) says ready.

    python scripts/smoke_grpc.py hardware-grpc-app---canary.<env domain>:443

Add --insecure for a plaintext server (local Compose). Retries for about a minute so a revision that is still starting (or scaling up from zero) can answer.
Exit 0 on success, 1 otherwise.
"""
import sys
import time
from pathlib import Path
import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from hardware.v1 import hardware_pb2 as pb, hardware_pb2_grpc as rpc  # noqa: E402


def check(target, tls):
    credentials = grpc.ssl_channel_credentials() if tls else None
    with (grpc.secure_channel(target, credentials) if tls else grpc.insecure_channel(target)) as channel:
        health = health_pb2_grpc.HealthStub(channel).Check(health_pb2.HealthCheckRequest(service=''), timeout=10)
        if health.status != health_pb2.HealthCheckResponse.SERVING:
            raise RuntimeError(f'health check says {health_pb2.HealthCheckResponse.ServingStatus.Name(health.status)}')
        ready = rpc.OpsServiceStub(channel).Ready(pb.ReadyRequest(), timeout=10)
        if ready.status != 'ready':
            raise RuntimeError(f'Ready says {ready.status!r}')


def main():
    targets = [a for a in sys.argv[1:] if not a.startswith('--')]
    if len(targets) != 1:
        sys.exit(__doc__)
    target, tls = targets[0], '--insecure' not in sys.argv
    last = None
    for attempt in range(1, 13):
        try:
            check(target, tls)
            print(f'smoke ok: {target} is serving and ready (attempt {attempt})')
            return
        except (grpc.RpcError, RuntimeError) as error:
            last = error.details() if isinstance(error, grpc.RpcError) else error
            print(f'smoke attempt {attempt} failed: {last}', flush=True)
            time.sleep(5)
    sys.exit(f'smoke FAILED: {target}: {last}')


if __name__ == '__main__':
    main()
