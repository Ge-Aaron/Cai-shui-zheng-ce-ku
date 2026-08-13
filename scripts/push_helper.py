import subprocess
import sys
import os

REPO_URL = "https://github.com/Ge-Aaron/Cai-shui-zheng-ce-ku.git"


def run(cmd, capture=True, timeout=120):
    """Run a git command and return (rc, stdout, stderr)."""
    kwargs = {
        "cwd": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "shell": True,
    }
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    try:
        p = subprocess.run(cmd, **kwargs, timeout=timeout)
        out = p.stdout.decode("utf-8", "replace") if capture and p.stdout else ""
        err = p.stderr.decode("utf-8", "replace") if capture and p.stderr else ""
        return p.returncode, out, err
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"


def local_head():
    rc, out, _ = run("git rev-parse HEAD")
    return out.strip() if rc == 0 else None


def remote_head():
    rc, out, _ = run("git ls-remote origin HEAD")
    if rc != 0 or not out:
        return None
    first_line = out.splitlines()[0].strip()
    return first_line.split()[0] if first_line else None


def main():
    # Push to GitHub.  Even if git exits non-zero, objects may have been
    # written successfully (common with Git credential helpers on Windows).
    rc, out, err = run("git push -u origin master", timeout=180)
    combined = (out + "\n" + err).strip()
    if combined:
        print(combined)

    if rc == 0:
        print("\n  [OK] Code pushed successfully.")
        return 0

    # Double-check by comparing remote HEAD with local HEAD.
    print("\n  Push returned non-zero, verifying remote HEAD...")
    local = local_head()
    remote = remote_head()
    if local and remote and local.lower() == remote.lower():
        print("  Remote HEAD matches local commit, push succeeded.")
        print("\n  [OK] Code pushed successfully.")
        return 0

    print(f"\n  [FAILED] Push did not succeed (exit code {rc}).")
    print("  Common reasons:")
    print("    1) Not logged in to GitHub (browser did not pop up / was cancelled)")
    print("    2) Network problem")
    print("    3) Diverged history - run manually: git pull origin master")
    print("\n  Fix the issue and run this file again.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
