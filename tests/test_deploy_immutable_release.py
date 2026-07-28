from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_deploy_immutable_release_updates_tracking_ref_when_deploying_branch() -> None:
    script = (PROJECT_ROOT / "scripts/deploy_immutable_release.sh").read_text(
        encoding="utf-8"
    )

    assert (
        'git fetch "$REMOTE" "refs/heads/${BRANCH}:refs/remotes/${REMOTE}/${BRANCH}"'
        in script
    )
    assert script.count('--branch "$BRANCH"') == 3
