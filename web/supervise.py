"""Runs Nginx (website, port 8080) and Envoy (gRPC-Web -> gRPC, loopback) in one container.

Required environment: API_HOST, the hostname of the hardware API app (no scheme, no port).
Optional: API_PORT (443), API_TLS (true), API_WAIT_SECONDS (120), ENVOY_LOG_LEVEL (warn),
PUBLIC_HOST, PUBLIC_SCHEME. If either process exits, the other is stopped and the container exits
non-zero so the platform restarts it.
"""
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

NGINX_TEMPLATE = Path('/etc/nginx/templates/site.conf.template')
ENVOY_TEMPLATE = Path('/etc/envoy/envoy.yaml.template')
TLS = '''    transport_socket:
      name: envoy.transport_sockets.tls
      typed_config:
        "@type": type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.UpstreamTlsContext
        sni: "__API_HOST__"
        common_tls_context:
          alpn_protocols: ["h2"]
          validation_context:
            trusted_ca: {filename: /etc/ssl/certs/ca-certificates.crt}
            match_typed_subject_alt_names:
            - san_type: DNS
              matcher: {exact: "__API_HOST__"}'''


def fill(text, values):
    for key, value in values.items():
        text = text.replace(key, value)
    return text


def resolver():
    for line in Path('/etc/resolv.conf').read_text().splitlines():
        if line.startswith('nameserver'):
            return line.split()[1]
    return '127.0.0.1'


def render(api_host, api_port, api_tls):
    Path('/tmp/nginx').mkdir(parents=True, exist_ok=True)
    site = fill(NGINX_TEMPLATE.read_text(), {
        '${API_ORIGIN}': 'http://127.0.0.1:8081',
        '${PUBLIC_HOST}': os.environ.get('PUBLIC_HOST', 'localhost'),
        '${PUBLIC_SCHEME}': os.environ.get('PUBLIC_SCHEME', 'https'),
    })
    Path('/tmp/nginx/site.conf').write_text(site.replace('127.0.0.11', resolver()))
    envoy = ENVOY_TEMPLATE.read_text().replace('__API_TLS__', TLS if api_tls else '')
    Path('/tmp/envoy.yaml').write_text(fill(envoy, {'__API_HOST__': api_host, '__API_PORT__': api_port}))


def wait_for_api(api_host, api_port, seconds):
    # Envoy's first health check must find the API reachable, so wait for it before starting Envoy.
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            socket.create_connection((api_host, int(api_port)), timeout=2).close()
            return
        except OSError:
            time.sleep(2)
    print(f'[supervise] {api_host}:{api_port} not reachable after {seconds}s; starting anyway', flush=True)


def main():
    api_host = os.environ.get('API_HOST', '').strip()
    if not api_host or '/' in api_host or ':' in api_host:
        sys.exit('API_HOST is required: the API app hostname only, with no scheme or port')
    api_port = os.environ.get('API_PORT', '443')
    api_tls = os.environ.get('API_TLS', 'true').lower() != 'false'
    render(api_host, api_port, api_tls)
    wait_for_api(api_host, api_port, int(os.environ.get('API_WAIT_SECONDS', '120')))
    children = {
        'envoy': subprocess.Popen(['envoy', '-c', '/tmp/envoy.yaml', '--disable-hot-restart',
                                   '--log-level', os.environ.get('ENVOY_LOG_LEVEL', 'warn')]),
        'nginx': subprocess.Popen(['nginx', '-e', 'stderr', '-c', '/etc/nginx/nginx.conf', '-g', 'daemon off;']),
    }

    def stop(*_):
        for child in children.values():
            if child.poll() is None:
                child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    exited = None
    while exited is None:
        time.sleep(1)
        for name, child in children.items():
            if child.poll() is not None:
                exited = (name, child.returncode)
                break
    print(f'[supervise] {exited[0]} exited with {exited[1]}; stopping the container', flush=True)
    stop()
    deadline = time.time() + 10
    for child in children.values():
        try:
            child.wait(max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            child.kill()
    sys.exit(exited[1] or 1)


if __name__ == '__main__':
    main()
