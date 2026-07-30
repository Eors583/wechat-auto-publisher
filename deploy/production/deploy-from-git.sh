#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="${REPO_URL:-https://github.com/Eors583/wechat-auto-publisher.git}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/wechat-publisher}"
SHARED_ENV="${SHARED_ENV:-${DEPLOY_ROOT}/shared/.env.production}"
MIRROR="${DEPLOY_ROOT}/repository.git"
RELEASES="${DEPLOY_ROOT}/releases"
CURRENT="${DEPLOY_ROOT}/current"
LOCK_FILE="${DEPLOY_ROOT}/shared/deploy.lock"

mkdir -p "${DEPLOY_ROOT}/shared" "${RELEASES}"
test -s "${SHARED_ENV}" || {
  echo "Production environment file is missing: ${SHARED_ENV}" >&2
  exit 1
}

exec 9>"${LOCK_FILE}"
flock -n 9 || {
  echo "Another deployment is already running." >&2
  exit 1
}

if [[ ! -d "${MIRROR}" ]]; then
  git clone --mirror "${REPO_URL}" "${MIRROR}"
else
  git --git-dir="${MIRROR}" remote set-url origin "${REPO_URL}"
  git --git-dir="${MIRROR}" fetch origin --prune
fi

ref="refs/heads/${DEPLOY_BRANCH}"
commit="$(git --git-dir="${MIRROR}" rev-parse "${ref}^{commit}")"
short_commit="$(git --git-dir="${MIRROR}" rev-parse --short=12 "${commit}")"
release="${RELEASES}/git-${short_commit}"
previous="$(readlink -f "${CURRENT}" 2>/dev/null || true)"
previous_image="$(
  docker inspect --format '{{.Config.Image}}' \
    wechat-publisher-api-1 2>/dev/null || true
)"
previous_tag="${previous_image##*:}"

if [[ ! -d "${release}" ]]; then
  mkdir -p "${release}"
  git --git-dir="${MIRROR}" archive "${commit}" | tar -x -C "${release}"
fi

cd "${release}"
export APP_VERSION="git-${short_commit}"
docker compose \
  --env-file "${SHARED_ENV}" \
  -f compose.production.yaml \
  build

ln -sfn "${release}" "${CURRENT}.next"
mv -Tf "${CURRENT}.next" "${CURRENT}"

rollback() {
  status=$?
  if [[ ${status} -eq 0 ]]; then
    return
  fi
  echo "Deployment failed; restoring the previous release." >&2
  if [[ -n "${previous}" && -d "${previous}" ]]; then
    ln -sfn "${previous}" "${CURRENT}.rollback"
    mv -Tf "${CURRENT}.rollback" "${CURRENT}"
    if [[ -n "${previous_tag}" ]]; then
      (
        cd "${previous}"
        APP_VERSION="${previous_tag}" docker compose \
          --env-file "${SHARED_ENV}" \
          -f compose.production.yaml \
          up -d --no-build --remove-orphans
      ) || true
    fi
  fi
  exit "${status}"
}
trap rollback ERR

docker compose \
  --env-file "${SHARED_ENV}" \
  -f compose.production.yaml \
  up -d --no-build --remove-orphans

healthy=false
for _ in $(seq 1 36); do
  if curl -fsS http://127.0.0.1:18776/health >/dev/null \
    && curl -fsS http://127.0.0.1:18775/ >/dev/null \
    && curl -fsS http://127.0.0.1:18777/ >/dev/null; then
    healthy=true
    break
  fi
  sleep 5
done
[[ "${healthy}" == "true" ]]

cat >"${DEPLOY_ROOT}/shared/current-release.txt" <<EOF
commit=${commit}
branch=${DEPLOY_BRANCH}
released_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
release=${release}
EOF

trap - ERR
echo "Deployment succeeded: ${DEPLOY_BRANCH}@${commit}"
