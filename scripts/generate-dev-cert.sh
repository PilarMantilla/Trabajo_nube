#!/usr/bin/env sh
set -eu

mkdir -p certs
openssl req -x509 -newkey rsa:3072 -nodes -sha256 -days 365 \
  -keyout certs/dev.key -out certs/dev.crt \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,DNS:namenode,DNS:datanode1,DNS:datanode2,DNS:datanode3,IP:127.0.0.1"
chmod 600 certs/dev.key
printf 'Created certs/dev.crt and certs/dev.key for local demonstrations.\n'
