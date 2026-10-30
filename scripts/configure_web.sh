#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
DATA=/var/lib/pidashcam
install -d -m 0700 "$DATA"
if [[ -e /etc/nginx/sites-enabled/default ]]; then
    expected=$(dpkg-query -W -f='${Conffiles}\n' nginx-common | awk '$1=="/etc/nginx/sites-available/default" {print $2}')
    actual=$(md5sum /etc/nginx/sites-enabled/default | cut -d ' ' -f 1)
    if [[ -z $expected || $actual != "$expected" || $(readlink -f /etc/nginx/sites-enabled/default) != /etc/nginx/sites-available/default ]]; then
        echo 'Existing nginx default site: back it up and remove sites-enabled/default before deployment.' >&2
        exit 1
    fi
    rm /etc/nginx/sites-enabled/default
fi
if [[ ! -s $DATA/admin-password ]]; then
    secret=$(openssl rand -base64 24)
    printf '%s\n' "$secret" | htpasswd -i -B -c /etc/nginx/dashcam.htpasswd admin
    chown root:www-data /etc/nginx/dashcam.htpasswd
    chmod 0640 /etc/nginx/dashcam.htpasswd
    printf 'Username: admin\nPassword: %s\n' "$secret" > "$DATA/admin-password"
    unset secret
fi
[[ -s /etc/nginx/dashcam.htpasswd ]] || { echo 'Password database missing; restore it before deploying.' >&2; exit 1; }
cat > /etc/nginx/sites-available/pidashcam <<'NGINX'
server {
    listen 80 default_server;
    server_name _;
    auth_basic "PiDashCam";
    auth_basic_user_file /etc/nginx/dashcam.htpasswd;
    # Only same-origin browser access; reject cross-site state-changing requests.
    set $request_origin $http_origin;
    if ($http_origin = "") { set $request_origin "$scheme://$http_host"; }
    if ($request_origin != "$scheme://$http_host") { return 403; }
    if ($http_sec_fetch_site = cross-site) { return 403; }
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $http_host;
        proxy_hide_header Access-Control-Allow-Origin;
        proxy_buffering off;
        proxy_read_timeout 300s;
    }
}
NGINX
chmod 0644 /etc/nginx/sites-available/pidashcam
ln -sfn /etc/nginx/sites-available/pidashcam /etc/nginx/sites-enabled/pidashcam
nginx -t
systemctl enable nginx.service
