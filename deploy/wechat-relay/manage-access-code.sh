#!/usr/bin/env bash
set -Eeuo pipefail

readonly DEFAULT_HTPASSWD_FILE="/etc/nginx/.htpasswd-wechat-relay"
readonly ACCESS_CODE_VERSION="wr1"
readonly CHECKSUM_LENGTH=16

usage() {
    cat >&2 <<'EOF'
Usage:
  sudo ./manage-access-code.sh issue <username> [htpasswd-file]
  sudo ./manage-access-code.sh revoke <username> [htpasswd-file]

Examples:
  sudo ./manage-access-code.sh issue wechat-client-001
  sudo ./manage-access-code.sh revoke wechat-client-001

The issue command prints exactly one wr1 access code to stdout. Store and
transmit it as a secret. The underlying password is never printed separately.
EOF
}

die() {
    printf 'Error: %s\n' "$1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

validate_username() {
    local username="$1"
    [[ "$username" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] \
        || die "username must use 1-64 letters, digits, dots, underscores, or hyphens"
}

base64url() {
    openssl base64 -A | tr '+/' '-_' | tr -d '='
}

checksum() {
    openssl dgst -sha256 -r \
        | awk -v length="$CHECKSUM_LENGTH" '{print substr($1, 1, length)}'
}

issue_access_code() {
    local username="$1"
    local htpasswd_file="$2"
    local password username_segment password_segment payload checksum_value

    require_command openssl
    require_command htpasswd
    require_command awk
    require_command tr

    [[ -d "$(dirname -- "$htpasswd_file")" ]] \
        || die "parent directory does not exist: $(dirname -- "$htpasswd_file")"

    # 256 random bits represented as 64 printable ASCII characters. This stays
    # below bcrypt's 72-byte password limit.
    password="$(openssl rand -hex 32)"

    if [[ -e "$htpasswd_file" ]]; then
        printf '%s\n' "$password" \
            | htpasswd -iB "$htpasswd_file" "$username" >/dev/null
    else
        (
            umask 027
            printf '%s\n' "$password" \
                | htpasswd -ciB "$htpasswd_file" "$username" >/dev/null
        )
    fi

    chmod 640 "$htpasswd_file"
    if [[ "${EUID:-$(id -u)}" -eq 0 ]] && command -v getent >/dev/null 2>&1; then
        local nginx_group="${WECHAT_RELAY_NGINX_GROUP:-www-data}"
        if getent group "$nginx_group" >/dev/null 2>&1; then
            chown "root:$nginx_group" "$htpasswd_file"
        fi
    fi

    username_segment="$(printf '%s' "$username" | base64url)"
    password_segment="$(printf '%s' "$password" | base64url)"
    payload="${ACCESS_CODE_VERSION}.${username_segment}.${password_segment}"
    checksum_value="$(printf '%s' "$payload" | checksum)"

    printf '%s.%s\n' "$payload" "$checksum_value"
    unset password
}

revoke_access() {
    local username="$1"
    local htpasswd_file="$2"

    require_command htpasswd
    [[ -f "$htpasswd_file" ]] || die "htpasswd file not found: $htpasswd_file"
    htpasswd -D "$htpasswd_file" "$username" >/dev/null \
        || die "username was not found or could not be revoked"
    printf 'Revoked relay access for %s\n' "$username" >&2
}

main() {
    [[ "$#" -ge 2 && "$#" -le 3 ]] || {
        usage
        exit 2
    }

    local action="$1"
    local username="$2"
    local htpasswd_file="${3:-${WECHAT_RELAY_HTPASSWD_FILE:-$DEFAULT_HTPASSWD_FILE}}"
    validate_username "$username"

    case "$action" in
        issue)
            issue_access_code "$username" "$htpasswd_file"
            ;;
        revoke)
            revoke_access "$username" "$htpasswd_file"
            ;;
        *)
            usage
            exit 2
            ;;
    esac
}

main "$@"
