import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASH_PATH = shutil.which("bash")
if os.name == "nt" and (git_path := shutil.which("git")):
    git_bash = Path(git_path).resolve().parent.parent / "bin" / "bash.exe"
    if git_bash.is_file():
        BASH_PATH = str(git_bash)


SHELL_COMMANDS = r'''
getprop() {
    case "$1" in
        ro.build.version.sdk) echo 35 ;;
        sys.boot_completed) echo 1 ;;
    esac
}
ls() {
    if [ "$1" = "/data/user/" ]; then
        [ "$WETYPE_TEST_USER_LIST_STATUS" = "0" ] || return "$WETYPE_TEST_USER_LIST_STATUS"
        printf '%s\n' 0 10 11
    else
        command ls "$@"
    fi
}
pm() {
    printf 'pm %s\n' "$*" >> "$WETYPE_TEST_LOG"
    case "$1" in
        install) return "$WETYPE_TEST_INSTALL_STATUS" ;;
        install-existing) [ "$3" != "$WETYPE_TEST_INSTALL_FAILED_USER" ] ;;
        *) return 1 ;;
    esac
}
cmd() {
    printf 'cmd %s\n' "$*" >> "$WETYPE_TEST_LOG"
    [ "$4" != "$WETYPE_TEST_ENABLE_FAILED_USER" ]
}
am() {
    printf 'am %s\n' "$*" >> "$WETYPE_TEST_LOG"
}
getevent() { echo 'input event KEY_VOLUMEDOWN'; }
set_perm_recursive() { :; }
abort() { echo "$*" >&2; exit 1; }
unset MODULE_HOT_INSTALL_REQUEST
. "$0"
printf 'hot_install=%s\n' "${MODULE_HOT_INSTALL_REQUEST:-false}"
'''


@unittest.skipUnless(BASH_PATH, "需要 Bash 运行模块脚本测试")
class ModuleScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.module_dir = Path(self.temp_dir.name) / "module"
        self.module_dir.mkdir()
        self.command_log = self.module_dir / "commands.log"
        for script_name in ("customize.sh", "service.sh"):
            shutil.copy2(PROJECT_ROOT / "module_template" / script_name, self.module_dir)
        files_dir = self.module_dir / "files"
        files_dir.mkdir()
        (files_dir / "WetypeMonet.apk").write_bytes(b"overlay-apk")

    def write_config(self, *, first_installation="1", multi_user="1", package_type="dynamic"):
        (self.module_dir / "config.conf").write_text(
            f'is_first_installation="{first_installation}"\n'
            f'is_multi_user="{multi_user}"\n'
            f'package_type="{package_type}"\n',
            encoding="utf-8",
        )

    def read_config(self):
        return dict(
            (key, value.strip('"'))
            for key, value in (
                line.split("=", 1)
                for line in (self.module_dir / "config.conf").read_text(encoding="utf-8").splitlines()
            )
        )

    def run_script(self, script_name, **overrides):
        environment = {
            **os.environ,
            "MODPATH": self.module_dir.as_posix(),
            "WETYPE_TEST_LOG": self.command_log.as_posix(),
            "WETYPE_TEST_INSTALL_STATUS": "0",
            "WETYPE_TEST_INSTALL_FAILED_USER": "",
            "WETYPE_TEST_ENABLE_FAILED_USER": "",
            "WETYPE_TEST_USER_LIST_STATUS": "0",
            **overrides,
        }
        result = subprocess.run(
            [BASH_PATH, "-c", SHELL_COMMANDS, (self.module_dir / script_name).as_posix()],
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def commands(self):
        if self.command_log.is_file():
            return self.command_log.read_text(encoding="utf-8").splitlines()
        return []

    def test_hot_install_failure_requires_reboot(self):
        self.write_config(first_installation="0")

        output = self.run_script("customize.sh", WETYPE_TEST_INSTALL_STATUS="1")

        self.assertIn("hot_install=false", output)
        self.assertNotIn("无需重启", output)
        self.assertEqual(self.read_config()["is_first_installation"], "1")
        self.assertNotIn("am force-stop --user 0 com.tencent.wetype", self.commands())
        staged_apk = self.module_dir / "system/priv-app/WetypeMonet/WetypeMonet.apk"
        self.assertEqual(staged_apk.read_bytes(), b"overlay-apk")

    def test_hot_install_success_requests_hot_install(self):
        self.write_config(first_installation="0")

        output = self.run_script("customize.sh")

        self.assertIn("hot_install=true", output)
        self.assertEqual(self.read_config()["is_first_installation"], "0")
        self.assertIn("am force-stop --user 0 com.tencent.wetype", self.commands())

    def test_first_install_stages_apk_for_reboot(self):
        self.write_config()

        output = self.run_script("customize.sh")

        self.assertIn("hot_install=false", output)
        self.assertEqual(self.read_config()["is_first_installation"], "1")
        self.assertEqual(self.commands(), [])
        staged_apk = self.module_dir / "system/priv-app/WetypeMonet/WetypeMonet.apk"
        self.assertEqual(staged_apk.read_bytes(), b"overlay-apk")

    def test_static_update_requires_reboot(self):
        self.write_config(first_installation="0", package_type="static")

        output = self.run_script("customize.sh")

        self.assertIn("hot_install=false", output)
        self.assertEqual(self.commands(), [])

    def test_boot_enables_all_selected_users(self):
        self.write_config()

        self.run_script("service.sh")

        self.assertEqual(self.read_config()["is_first_installation"], "0")
        for user_id in ("0", "10", "11"):
            self.assertIn(f"cmd overlay enable --user {user_id} monet.com.tencent.wetype", self.commands())
            self.assertIn(f"am force-stop --user {user_id} com.tencent.wetype", self.commands())
        for user_id in ("10", "11"):
            self.assertIn(f"pm install-existing --user {user_id} monet.com.tencent.wetype", self.commands())

    def test_boot_respects_multi_user_opt_out(self):
        self.write_config(multi_user="0")

        self.run_script("service.sh")

        self.assertEqual(self.read_config()["is_first_installation"], "0")
        self.assertEqual(
            self.commands(),
            [
                "cmd overlay enable --user 0 monet.com.tencent.wetype",
                "am force-stop --user 0 com.tencent.wetype",
            ],
        )

    def test_static_boot_installs_selected_users_without_dynamic_enable(self):
        self.write_config(package_type="static")

        self.run_script("service.sh")

        self.assertEqual(self.read_config()["is_first_installation"], "0")
        self.assertEqual(
            self.commands(),
            [
                "pm install-existing --user 10 monet.com.tencent.wetype",
                "am force-stop --user 10 com.tencent.wetype",
                "pm install-existing --user 11 monet.com.tencent.wetype",
                "am force-stop --user 11 com.tencent.wetype",
            ],
        )

    def test_boot_keeps_retry_state_when_user_listing_fails(self):
        self.write_config()

        self.run_script("service.sh", WETYPE_TEST_USER_LIST_STATUS="1")

        self.assertEqual(self.read_config()["is_first_installation"], "1")
        self.assertIn("cmd overlay enable --user 0 monet.com.tencent.wetype", self.commands())

    def test_boot_retries_failed_secondary_user_install(self):
        self.write_config()

        self.run_script("service.sh", WETYPE_TEST_INSTALL_FAILED_USER="10")

        self.assertEqual(self.read_config()["is_first_installation"], "1")
        self.assertNotIn("cmd overlay enable --user 10 monet.com.tencent.wetype", self.commands())
        self.assertNotIn("am force-stop --user 10 com.tencent.wetype", self.commands())
        self.assertIn("cmd overlay enable --user 11 monet.com.tencent.wetype", self.commands())
        self.command_log.unlink()

        self.run_script("service.sh")

        self.assertEqual(self.read_config()["is_first_installation"], "0")
        self.assertIn("pm install-existing --user 10 monet.com.tencent.wetype", self.commands())
        self.assertIn("cmd overlay enable --user 10 monet.com.tencent.wetype", self.commands())

    def test_boot_retries_failed_overlay_enable(self):
        self.write_config()

        self.run_script("service.sh", WETYPE_TEST_ENABLE_FAILED_USER="10")

        self.assertEqual(self.read_config()["is_first_installation"], "1")
        self.assertNotIn("am force-stop --user 10 com.tencent.wetype", self.commands())
        self.assertIn("cmd overlay enable --user 11 monet.com.tencent.wetype", self.commands())

        self.run_script("service.sh")

        self.assertEqual(self.read_config()["is_first_installation"], "0")
        self.assertIn("am force-stop --user 10 com.tencent.wetype", self.commands())


class ModuleTemplateTests(unittest.TestCase):
    def test_customize_restores_old_config_before_reading_package_type(self):
        script = (Path(__file__).resolve().parents[1] / "module_template" / "customize.sh").read_text(
            encoding="utf-8"
        )
        main_body = script[script.index("main()") :]

        self.assertLess(main_body.index("backup"), main_body.index("check_package_type"))


if __name__ == "__main__":
    unittest.main()
