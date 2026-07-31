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
    assert "snapshot variant_suite variant_count < 100" in script
    assert "snapshot variants < 100" in script
    assert "technical_evidence incomplete" in script
    assert "blocked_incomplete_raw_data" in script
    assert "raw_coverage=" in script
    assert "latest_trade_date missing outside blocked raw refresh" in script
    guard = (
        'if [ "$SKIP_FRONTEND_BUILD" = "true" ] && { '
        '[ ! -d "$RELEASE_DIR/frontend/node_modules" ] || '
        '[ ! -d "$RELEASE_DIR/frontend/dist" ]; }; then'
    )
    assert guard in script
    assert script.index(guard) < script.index('if [ ! -d "$RELEASE_DIR" ]; then')
    assert "--skip-frontend-build requires an existing release with complete frontend artifacts" in script
    assert script.index("def _is_finite(value):") < script.index(
        'technical_evidence = first.get("technical_evidence")'
    )
