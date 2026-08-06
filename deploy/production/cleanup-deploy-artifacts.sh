#!/usr/bin/env bash
set -Eeuo pipefail

DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/wechat-publisher}"
RELEASES="${DEPLOY_ROOT}/releases"
CURRENT="${DEPLOY_ROOT}/current"
LOCK_FILE="${DEPLOY_ROOT}/shared/deploy.lock"
DISK_THRESHOLD="${DISK_THRESHOLD:-80}"
RELEASE_KEEP="${RELEASE_KEEP:-5}"
IMAGE_KEEP="${IMAGE_KEEP:-5}"
BUILD_CACHE_MAX_AGE="${BUILD_CACHE_MAX_AGE:-72h}"
FORCE_CLEANUP="${FORCE_CLEANUP:-false}"
IMAGE_REPOSITORY="${IMAGE_REPOSITORY:-wechat-auto-publisher}"
REQUIRED_CONTAINERS=(
  wechat-publisher-postgres-1
  wechat-publisher-api-1
  wechat-publisher-web-1
  wechat-publisher-admin-1
)
HEALTH_URLS=(
  http://127.0.0.1:18776/health
  http://127.0.0.1:18775/
  http://127.0.0.1:18777/admin/
)

log() {
  printf '[%s] %s\n' "$(date -Is)" "$*"
}

is_positive_integer() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]]
}

is_positive_integer "${DISK_THRESHOLD}" && (( DISK_THRESHOLD <= 100 )) || {
  log "invalid DISK_THRESHOLD=${DISK_THRESHOLD}"
  exit 2
}
is_positive_integer "${RELEASE_KEEP}" || {
  log "invalid RELEASE_KEEP=${RELEASE_KEEP}"
  exit 2
}
is_positive_integer "${IMAGE_KEEP}" || {
  log "invalid IMAGE_KEEP=${IMAGE_KEEP}"
  exit 2
}

mkdir -p "${DEPLOY_ROOT}/shared" "${RELEASES}"
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  log "deployment is active; cleanup skipped"
  exit 0
fi

disk_usage_percent() {
  df -P "${DEPLOY_ROOT}" | awk 'NR == 2 {gsub(/%/, "", $5); print $5}'
}

production_healthy() {
  local container state url
  for container in "${REQUIRED_CONTAINERS[@]}"; do
    state="$(
      docker inspect --format '{{.State.Running}}' "${container}" 2>/dev/null \
        || true
    )"
    if [[ "${state}" != "true" ]]; then
      log "required container is not running: ${container}"
      return 1
    fi
  done
  for url in "${HEALTH_URLS[@]}"; do
    if ! curl --fail --silent --show-error --max-time 15 "${url}" >/dev/null; then
      log "production health check failed: ${url}"
      return 1
    fi
  done
}

before_usage="$(disk_usage_percent)"
if [[ "${FORCE_CLEANUP}" != "true" ]] && (( before_usage < DISK_THRESHOLD )); then
  log "disk usage ${before_usage}% is below ${DISK_THRESHOLD}%; nothing to clean"
  exit 0
fi

log "cleanup started at ${before_usage}% disk usage"
current_release="$(readlink -f "${CURRENT}" 2>/dev/null || true)"
case "${current_release}" in
  "${RELEASES}"/git-*)
    if [[ ! -d "${current_release}" ]]; then
      log "current release directory is missing; cleanup skipped"
      exit 0
    fi
    ;;
  *)
    log "current symlink is not a managed release; cleanup skipped"
    exit 0
    ;;
esac
if ! production_healthy; then
  log "production is not healthy; cleanup skipped"
  exit 0
fi
log "production preflight passed; only unused artifacts will be removed"

mapfile -t release_paths < <(
  find "${RELEASES}" -mindepth 1 -maxdepth 1 -type d -name 'git-*' \
    -printf '%T@ %p\n' | sort -nr | awk '{print $2}'
)
for index in "${!release_paths[@]}"; do
  candidate="$(readlink -f "${release_paths[$index]}" 2>/dev/null || true)"
  [[ -n "${candidate}" ]] || continue
  if (( index < RELEASE_KEEP )) || [[ "${candidate}" == "${current_release}" ]]; then
    continue
  fi
  case "${candidate}" in
    "${RELEASES}"/git-*)
      log "removing old release ${candidate}"
      rm -rf -- "${candidate}"
      ;;
    *)
      log "refusing release outside managed directory: ${candidate}"
      ;;
  esac
done

mapfile -t used_image_ids < <(
  container_ids="$(docker ps -aq)"
  if [[ -n "${container_ids}" ]]; then
    docker inspect --format '{{.Image}}' ${container_ids} 2>/dev/null | sort -u
  fi
)
mapfile -t image_tags < <(
  docker image ls "${IMAGE_REPOSITORY}" \
    --format '{{.Repository}}:{{.Tag}}' \
    | awk '$0 !~ /:<none>$/ && !seen[$0]++'
)
for index in "${!image_tags[@]}"; do
  tag="${image_tags[$index]}"
  if (( index < IMAGE_KEEP )); then
    continue
  fi
  image_id="$(docker image inspect --format '{{.Id}}' "${tag}" 2>/dev/null || true)"
  in_use=false
  for used_id in "${used_image_ids[@]}"; do
    if [[ -n "${image_id}" && "${image_id}" == "${used_id}" ]]; then
      in_use=true
      break
    fi
  done
  if [[ "${in_use}" == "true" ]]; then
    log "keeping image used by a container: ${tag}"
    continue
  fi
  log "removing old application image ${tag}"
  docker image rm "${tag}"
done

log "pruning dangling images older than ${BUILD_CACHE_MAX_AGE}"
docker image prune --force --filter "until=${BUILD_CACHE_MAX_AGE}"
log "pruning unused build cache older than ${BUILD_CACHE_MAX_AGE}"
docker builder prune --force --filter "until=${BUILD_CACHE_MAX_AGE}"

after_usage="$(disk_usage_percent)"
if ! production_healthy; then
  log "post-cleanup production health check failed"
  exit 1
fi
log "cleanup finished: disk usage ${before_usage}% -> ${after_usage}%"
