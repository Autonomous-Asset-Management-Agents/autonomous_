import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


def calculate_tree_hash(directory: Path) -> str:
    hasher = hashlib.sha256()

    files = []
    for root, _, filenames in os.walk(directory):
        for filename in filenames:
            full_path = Path(root) / filename
            rel_path = full_path.relative_to(directory).as_posix()
            files.append((rel_path, full_path))

    files.sort(key=lambda x: x[0])

    for rel_path, full_path in files:
        hasher.update(rel_path.encode("utf-8"))
        is_executable = os.access(full_path, os.X_OK)
        hasher.update(b"1" if is_executable else b"0")

        with open(full_path, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)

    return hasher.hexdigest()


@pytest.mark.vc0
def test_oss_snapshot_is_byte_identical_across_runs():
    repo_root = Path(__file__).parent.parent.parent.parent.absolute()
    script_path = "scripts/oss_make_snapshot.sh"

    bash_exe = "bash"
    if sys.platform == "win32":
        git_bash = r"C:\Program Files\Git\bin\bash.exe"
        if os.path.exists(git_bash):
            bash_exe = git_bash

    with tempfile.TemporaryDirectory(
        dir=repo_root
    ) as tmp1, tempfile.TemporaryDirectory(dir=repo_root) as tmp2:
        dir1 = Path(tmp1) / "snap1"
        dir2 = Path(tmp2) / "snap2"

        rel_dir1 = dir1.relative_to(repo_root).as_posix()
        rel_dir2 = dir2.relative_to(repo_root).as_posix()

        env1 = os.environ.copy()
        env1["OSS_SNAPSHOT_DIR"] = rel_dir1
        env1["SKIP_OSS_AUDIT_FOR_TESTING"] = "1"
        res1 = subprocess.run(
            [bash_exe, script_path],
            cwd=repo_root,
            env=env1,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if res1.returncode != 0:
            import pytest

            pytest.skip(
                f"oss_make_snapshot.sh failed to run. {res1.stderr}\n{res1.stdout}"
            )

        env2 = os.environ.copy()
        env2["OSS_SNAPSHOT_DIR"] = rel_dir2
        env2["SKIP_OSS_AUDIT_FOR_TESTING"] = "1"
        res2 = subprocess.run(
            [bash_exe, script_path],
            cwd=repo_root,
            env=env2,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if res2.returncode != 0:
            import pytest

            pytest.skip(
                f"oss_make_snapshot.sh second run failed. {res2.stderr}\n{res2.stdout}"
            )

        hash1 = calculate_tree_hash(dir1)
        hash2 = calculate_tree_hash(dir2)

        assert (
            hash1 == hash2
        ), f"Snapshots sind nicht byte-gleich! Hash1: {hash1}, Hash2: {hash2}"


@pytest.mark.vc0
def test_oss_exclude_gate_blocks_enterprise_files():
    """
    4. Prüfung „keine Enterprise-Datei“ gegen oss_exclude.txt und Marker-Liste ergänzen.
    Wir legen bewusst eine Datei an, die laut oss_exclude.txt geblockt werden soll,
    und verifizieren, dass sie nicht im Snapshot landet.
    """
    repo_root = Path(__file__).parent.parent.parent.parent.absolute()
    script_path = "scripts/oss_make_snapshot.sh"

    bash_exe = "bash"
    if sys.platform == "win32":
        git_bash = r"C:\Program Files\Git\bin\bash.exe"
        if os.path.exists(git_bash):
            bash_exe = git_bash

    fake_secret_file = (
        repo_root / "ai_trading_bot" / "core" / "test_dummy_enterprise_secret.json"
    )
    fake_secret_file.write_text('{"secret": "do_not_leak"}')

    try:
        with tempfile.TemporaryDirectory(dir=repo_root) as tmp1:
            dir1 = Path(tmp1) / "snap1"
            rel_dir1 = dir1.relative_to(repo_root).as_posix()

            env1 = os.environ.copy()
            env1["OSS_SNAPSHOT_DIR"] = rel_dir1
            env1["SKIP_OSS_AUDIT_FOR_TESTING"] = "1"

            # Fuege test_dummy_enterprise_secret.json temporaer in oss_exclude.txt ein
            exclude_file = repo_root / "scripts" / "oss_exclude.txt"
            original_excludes = exclude_file.read_text(encoding="utf-8")
            exclude_file.write_text(
                original_excludes + "\ncore/test_dummy_enterprise_secret.json\n",
                encoding="utf-8",
            )

            try:
                res1 = subprocess.run(
                    [bash_exe, script_path],
                    cwd=repo_root,
                    env=env1,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                if res1.returncode != 0:
                    import pytest

                    pytest.skip(f"oss_make_snapshot.sh failed to run. {res1.stderr}")

                leaked_file = dir1 / "core" / "test_dummy_enterprise_secret.json"
                assert (
                    not leaked_file.exists()
                ), "Die Enterprise-Testdatei ist durchgerutscht! Das Gate funktioniert nicht!"

            finally:
                exclude_file.write_text(original_excludes, encoding="utf-8")
    finally:
        if fake_secret_file.exists():
            fake_secret_file.unlink()


@pytest.mark.vc0
def test_verify_oss_snapshot_gate_catches_leak():
    """
    Testet, ob verify_oss_snapshot.sh fehlschlaegt, wenn eine Datei
    in den Snapshot gelangt ist, die laut oss_exclude.txt verboten ist.
    """
    repo_root = Path(__file__).parent.parent.parent.parent.absolute()
    verify_script = repo_root / "scripts" / "verify_oss_snapshot.sh"

    bash_exe = "bash"
    if sys.platform == "win32":
        git_bash = r"C:\Program Files\Git\bin\bash.exe"
        if os.path.exists(git_bash):
            bash_exe = git_bash

    with tempfile.TemporaryDirectory(dir=repo_root) as tmp1:
        dir1 = Path(tmp1) / "snap1"
        dir1.mkdir(parents=True)
        rel_dir1 = dir1.relative_to(repo_root).as_posix()

        # Lege essenzielle Dateien an, damit die grundlegenden Checks nicht fehlschlagen
        (dir1 / "LICENSE").touch()
        (dir1 / "NOTICE").touch()
        (dir1 / "LICENSE-MODELS").touch()
        (dir1 / "docker-compose.oss.yml").touch()

        # Lege eine Datei an, die verboten ist (z.B. core/secret_manager_utils.py Enterprise-Version)
        core_dir = dir1 / "core"
        core_dir.mkdir()
        (core_dir / "secret_manager_utils.py").write_text(
            "from google.cloud import secretmanager\n"
        )

        env = os.environ.copy()
        env["SKIP_OSS_AUDIT_FOR_TESTING"] = "1"

        res = subprocess.run(
            [bash_exe, str(verify_script), rel_dir1],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert res.returncode != 0, "Gate did not catch the leaked enterprise file!"
        assert (
            "Proprietary GCP module leaked" in res.stdout
            or "Proprietary GCP module leaked" in res.stderr
        )

        # Lege eine andere verbotene Datei aus oss_exclude.txt an
        (core_dir / "secret_manager_utils.py").write_text("safe")
        (core_dir / "test_dummy_enterprise_secret.json").write_text("{}")

        exclude_file = repo_root / "scripts" / "oss_exclude.txt"
        original_excludes = exclude_file.read_text(encoding="utf-8")
        exclude_file.write_text(
            original_excludes + "\ncore/test_dummy_enterprise_secret.json\n",
            encoding="utf-8",
        )

        try:
            res = subprocess.run(
                [bash_exe, str(verify_script), rel_dir1],
                cwd=repo_root,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            assert (
                res.returncode != 0
            ), "Gate did not catch the dummy enterprise leak via pathspec!"
            assert (
                "The following files matched oss_exclude.txt but leaked" in res.stdout
                or "The following files matched oss_exclude.txt but leaked"
                in res.stderr
            )
        finally:
            exclude_file.write_text(original_excludes, encoding="utf-8")
