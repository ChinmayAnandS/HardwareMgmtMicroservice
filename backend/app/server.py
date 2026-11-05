import logging
import os
import signal
from concurrent.futures import ThreadPoolExecutor
import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from hardware.v1 import hardware_pb2 as pb, hardware_pb2_grpc as rpc
from .auth import Authenticator
from .config import Settings
from .service import AuthServicer, HardwareServicer, OpsServicer
from .sql_store import AzureSqlStore
from .store import Store

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
log = logging.getLogger('hardware')
# Requests are small; refuse anything bigger than the old 16 KiB request-body limit.
MAX_REQUEST_BYTES = 16384


class Application:
    """The gRPC server and the resources it owns."""

    def __init__(self, settings=None, address=None):
        self.cfg = settings or Settings()
        self.cfg.check()
        self.store = Store(self.cfg)
        self.sql_store = AzureSqlStore(self.cfg)
        auth = Authenticator(self.cfg, self.store)
        self.server = grpc.server(ThreadPoolExecutor(max_workers=32, thread_name_prefix='rpc'),
                                  options=[('grpc.max_receive_message_length', MAX_REQUEST_BYTES)],
                                  maximum_concurrent_rpcs=128)
        rpc.add_HardwareServiceServicer_to_server(HardwareServicer(auth, self.store, self.sql_store), self.server)
        rpc.add_AuthServiceServicer_to_server(AuthServicer(auth, self.cfg), self.server)
        rpc.add_OpsServiceServicer_to_server(OpsServicer(self.store), self.server)
        self.health = health.HealthServicer()
        health_pb2_grpc.add_HealthServicer_to_server(self.health, self.server)
        for service in ('', *(s.full_name for s in pb.DESCRIPTOR.services_by_name.values())):
            self.health.set(service, health_pb2.HealthCheckResponse.SERVING)
        self.port = self.server.add_insecure_port(address or f'[::]:{self.cfg.grpc_port}')

    def start(self):
        self.server.start()
        return self

    def stop(self, grace=0):
        self.server.stop(grace).wait()
        self.store.client.close()
        self.sql_store.close()


def main():
    if os.getenv('APPLICATIONINSIGHTS_CONNECTION_STRING'):
        from azure.monitor.opentelemetry import configure_azure_monitor
        configure_azure_monitor(sampling_ratio=0.1)
    app = Application().start()
    log.info('Hardware gRPC service listening on port %s', app.port)
    signal.signal(signal.SIGTERM, lambda *_: app.server.stop(10))
    app.server.wait_for_termination()
    app.store.client.close()


if __name__ == '__main__':
    main()
