#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
DATA=/var/lib/pidashcam
install -d -m 0700 "$DATA"
if [[ ! -s $DATA/admin-password ]]; then
    secret=$(openssl rand -base64 24)
    printf '%s\n' "$secret" | htpasswd -i -B -c /etc/nginx/dashcam.htpasswd admin
    chown root:www-data /etc/nginx/dashcam.htpasswd
    chmod 0640 /etc/nginx/dashcam.htpasswd
    printf 'Username: admin\nPassword: %s\n' "$secret" > "$DATA/admin-password"
    unset secret
fi
ssh-keygen -A
touch "$DATA/.firstboot-complete"
