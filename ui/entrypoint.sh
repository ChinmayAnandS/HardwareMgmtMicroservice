#!/bin/sh
set -eu
: "${API_ORIGIN:?API_ORIGIN required}"
: "${PUBLIC_HOST:?PUBLIC_HOST required}"
: "${PUBLIC_SCHEME:=https}"
export PUBLIC_SCHEME
# Docker's resolver is not available on Azure; derive the platform resolver.
resolver=$(awk '/^nameserver/{print $2;exit}' /etc/resolv.conf)
envsubst '${API_ORIGIN} ${PUBLIC_HOST} ${PUBLIC_SCHEME}' < /etc/nginx/templates/site.conf.template > /tmp/default.conf
sed "s/127.0.0.11/$resolver/" /tmp/default.conf > /etc/nginx/conf.d/default.conf
exec nginx -g 'daemon off;'
