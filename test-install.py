#!/usr/bin/env python3
"""Exercise install hooks with private fixtures and a fake service manager."""
import os
from pathlib import Path
import stat
import subprocess
import tempfile


def main():
    project = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="leigod-hooks-test.") as temp:
        root = Path(temp)
        helpers = root / "helpers"
        helpers.mkdir()
        for name in ("firewall.sh", "ensure-wlan0.sh"):
            helper = helpers / name
            helper.write_text('#!/bin/sh\nprintf "%s\\n" "' + name + ' $*" >> "$EVENTS"\n')
            helper.chmod(0o755)
        hooks = root / "hooks.sh"
        hooks.write_text((project / "leigod-plugin.install").read_text().replace(
            "/usr/lib/leigod/", str(helpers) + "/"))
        live = root / "accelerator.ini"
        state = root / "private"
        snapshot = state / "accelerator.ini"
        events = root / "events"
        env = dict(os.environ, TEST_ROOT=str(root), EVENTS=str(events))

        def invoke(action, active=False):
            script = '''
source "$TEST_ROOT/hooks.sh"
BIND_FILE="$TEST_ROOT/accelerator.ini"
STATE_DIR="$TEST_ROOT/private"
BIND_SAVE="$STATE_DIR/accelerator.ini"
RESTART_FILE="$STATE_DIR/restart-service"
systemctl() {
    printf '%s\n' "systemctl $*" >> "$EVENTS"
    case "$1" in
        is-active) [ "$MOCK_ACTIVE" = 1 ] ;;
        stop) printf '[device]\ntoken=synthetic-after-stop\n' > "$BIND_FILE" ;;
        *) return 0 ;;
    esac
}
''' + action + "\n"
            result = subprocess.run(["bash"], input=script, text=True, capture_output=True,
                                    env=dict(env, MOCK_ACTIVE="1" if active else "0"))
            if result.returncode:
                raise RuntimeError(result.stderr)

        live.write_text("[device]\ntoken=synthetic-before-stop\n")
        invoke("pre_upgrade", active=True)
        assert stat.S_IMODE(state.stat().st_mode) == 0o700
        assert stat.S_IMODE(snapshot.stat().st_mode) == 0o600
        assert snapshot.read_text() == "[device]\ntoken=synthetic-after-stop\n"
        assert (state / "restart-service").exists()
        live.write_text("[device]\n")  # Simulate package file replacement.
        invoke("post_upgrade")
        assert live.read_text() == "[device]\ntoken=synthetic-after-stop\n"
        assert stat.S_IMODE(live.stat().st_mode) == 0o600
        assert not snapshot.exists() and not (state / "restart-service").exists()
        assert events.read_text().splitlines() == [
            "systemctl is-active --quiet leigod", "systemctl stop leigod",
            "systemctl daemon-reload", "systemctl start leigod",
        ]
        print("PASS: upgrade stops writers, saves privately, restores and restarts")

        events.write_text("")
        invoke("pre_upgrade", active=False)
        invoke("post_upgrade")
        assert "systemctl start" not in events.read_text()
        assert "systemctl stop" not in events.read_text()
        print("PASS: inactive service stays inactive")

        invoke("pre_install")
        assert snapshot.exists()
        live.write_text("[device]\ntoken=   \n")
        invoke("pre_install")
        assert not snapshot.exists()
        invoke("post_install")
        assert "synthetic" not in live.read_text()
        print("PASS: intentional unbind does not restore stale credentials")

        events.write_text("")
        invoke("pre_remove")
        assert events.read_text().splitlines() == [
            "systemctl stop leigod", "firewall.sh cleanup", "ensure-wlan0.sh cleanup",
        ]
        assert snapshot.exists()
        live.unlink()
        invoke("pre_install")  # Missing live config must retain reinstall snapshot.
        live.write_text("[device]\n")
        invoke("post_install")
        assert live.read_text() == "[device]\ntoken=synthetic-after-stop\n"
        print("PASS: removal cleans owned resources and retains private reinstall state")


if __name__ == "__main__":
    main()
