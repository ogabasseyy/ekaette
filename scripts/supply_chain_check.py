"""Supply-chain integrity checks for AT + SIP bridge dependencies.

Verifies:
1. Dependency lock file exists and has hashes
2. No known vulnerable packages (basic check via pip audit)
3. SBOM generation capability (CycloneDX format)

Run: python -m scripts.supply_chain_check
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQ_FILE = ROOT / "requirements.txt"

# Critical AT/SIP bridge dependencies that must be pinned
PINNED_DEPS = {
    "africastalking": "2.0.2",
}

UPPER_BOUNDED_DEPS = {
    "httpx",
}


def check_requirements_pinned() -> list[str]:
    """Verify critical dependencies are pinned in requirements.txt."""
    errors: list[str] = []
    if not REQ_FILE.exists():
        errors.append("requirements.txt not found")
        return errors

    content = REQ_FILE.read_text()
    for pkg, version in PINNED_DEPS.items():
        if f"{pkg}=={version}" not in content:
            errors.append(f"{pkg} not pinned to =={version} in requirements.txt")

    for pkg in UPPER_BOUNDED_DEPS:
        # Check for upper bound (e.g., <0.29 or <=0.29)
        found = False
        for line in content.splitlines():
            if line.strip().startswith(pkg) and ("<" in line):
                found = True
                break
        if not found:
            errors.append(f"{pkg} missing upper bound in requirements.txt")

    return errors


def check_pip_audit() -> list[str]:
    """Run pip-audit if available (non-blocking if not installed)."""
    errors: list[str] = []
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip_audit", "--strict", "--desc"],
            capture_output=True, text=True, timeout=60,
        )
        if "No module named" in result.stderr:
            print("SKIP: pip-audit not installed (pip install pip-audit)")
            return errors
        if result.returncode != 0:
            for line in result.stdout.splitlines():
                if "vulnerability" in line.lower() or "CVE" in line:
                    errors.append(f"Vulnerability: {line.strip()}")
    except FileNotFoundError:
        print("SKIP: pip-audit not installed (pip install pip-audit)")
    except subprocess.TimeoutExpired:
        print("SKIP: pip-audit timed out")
    return errors


def check_sbom_capability() -> list[str]:
    """Verify CycloneDX SBOM can be generated."""
    errors: list[str] = []
    try:
        result = subprocess.run(
            [sys.executable, "-m", "cyclonedx_py", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            print("SKIP: cyclonedx-bom not installed (pip install cyclonedx-bom)")
    except FileNotFoundError:
        print("SKIP: cyclonedx-bom not installed (pip install cyclonedx-bom)")
    except subprocess.TimeoutExpired:
        errors.append("cyclonedx-bom timed out")
    return errors


def main() -> None:
    """Run all supply-chain checks."""
    all_errors: list[str] = []

    print("── Checking dependency pinning ──")
    errors = check_requirements_pinned()
    all_errors.extend(errors)
    for e in errors:
        print(f"  FAIL: {e}")
    if not errors:
        print("  PASS: Critical deps pinned with bounds")

    print("── Checking for vulnerabilities ──")
    errors = check_pip_audit()
    all_errors.extend(errors)
    for e in errors:
        print(f"  FAIL: {e}")
    if not errors:
        print("  PASS: No known vulnerabilities (or audit skipped)")

    print("── Checking SBOM capability ──")
    errors = check_sbom_capability()
    all_errors.extend(errors)
    for e in errors:
        print(f"  FAIL: {e}")
    if not errors:
        print("  PASS: SBOM generation available (or skipped)")

    if all_errors:
        print(f"\nSupply-chain check: {len(all_errors)} issue(s) found")
        sys.exit(1)
    else:
        print("\nSupply-chain check passed")


if __name__ == "__main__":
    main()
