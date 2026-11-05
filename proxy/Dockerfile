# Translates the browser's gRPC-Web requests into native gRPC for the API.
FROM envoyproxy/envoy:v1.34-latest
COPY envoy.yaml /etc/envoy/envoy.yaml
# Run unprivileged from the start: the image's root entrypoint needs capabilities that compose drops.
USER envoy
EXPOSE 8081
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=6 \
  CMD ["bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/8081"]
