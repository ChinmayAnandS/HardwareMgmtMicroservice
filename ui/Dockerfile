FROM nginxinc/nginx-unprivileged:1.28-alpine
USER root
# Apply security updates from the configured Alpine repositories.
RUN apk upgrade --no-cache
COPY nginx.conf.template /etc/nginx/templates/site.conf.template
COPY entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh && chown -R 101:101 /etc/nginx/conf.d
COPY --chown=101:101 index.html app.js style.css /usr/share/nginx/html/
USER 101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD wget -q -O /dev/null http://127.0.0.1:8080/health || exit 1
ENTRYPOINT ["/entrypoint.sh"]
