from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = ROOT / "deploy" / "production"
SCRIPT = (PRODUCTION / "cleanup-deploy-artifacts.sh").read_text(
    encoding="utf-8"
)
SERVICE = (PRODUCTION / "wechat-publisher-cleanup.service").read_text(
    encoding="utf-8"
)
TIMER = (PRODUCTION / "wechat-publisher-cleanup.timer").read_text(
    encoding="utf-8"
)


def test_cleanup_is_threshold_gated_and_serialized_with_deployments() -> None:
    assert 'DISK_THRESHOLD="${DISK_THRESHOLD:-80}"' in SCRIPT
    assert 'exec 9>"${LOCK_FILE}"' in SCRIPT
    assert "flock -n 9" in SCRIPT
    assert "before_usage < DISK_THRESHOLD" in SCRIPT


def test_cleanup_preserves_current_and_recent_rollback_versions() -> None:
    assert 'RELEASE_KEEP="${RELEASE_KEEP:-5}"' in SCRIPT
    assert 'IMAGE_KEEP="${IMAGE_KEEP:-5}"' in SCRIPT
    assert '[[ "${candidate}" == "${current_release}" ]]' in SCRIPT
    assert '"${image_id}" == "${used_id}"' in SCRIPT
    assert 'docker image rm "${tag}"' in SCRIPT


def test_cleanup_never_prunes_containers_or_volumes() -> None:
    assert "docker system prune" not in SCRIPT
    assert "--volumes" not in SCRIPT
    assert "docker volume" not in SCRIPT
    assert "docker container" not in SCRIPT
    assert "docker image prune" in SCRIPT
    assert "docker builder prune" in SCRIPT


def test_systemd_timer_checks_hourly_and_runs_the_versioned_script() -> None:
    assert "OnCalendar=hourly" in TIMER
    assert "Persistent=true" in TIMER
    assert "DISK_THRESHOLD=80" in SERVICE
    assert "RELEASE_KEEP=5" in SERVICE
    assert "IMAGE_KEEP=5" in SERVICE
    assert (
        "ExecStart=/opt/wechat-publisher/shared/cleanup-deploy-artifacts.sh"
        in SERVICE
    )
