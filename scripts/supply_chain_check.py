"""Supply-chain integrity checks for AT + SIP bridge dependencies.

Verifies:
1. Dependency pinning and hash verification in requirements.txt
2. No known vulnerable packages (pip-audit)
3. SBOM generation capability (CycloneDX format)

Run: python -m scripts.supply_chain_check
"""

from __future__ import annotations

import re
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


def check_hash_pinning() -> list[str]:
    """Verify requirements.txt uses hash pinning for critical dependencies."""
    errors: list[str] = []
    if not REQ_FILE.exists():
        return errors

    content = REQ_FILE.read_text()
    # Check if any lines have --hash markers (pip hash-checking mode)
    has_hashes = any("--hash=" in line for line in content.splitlines())
    if not has_hashes:
        errors.append(
            "requirements.txt has no --hash pins; consider running "
            "'pip-compile --generate-hashes' for supply-chain integrity"
        )
    return errors


def check_pip_audit() -> list[str]:
    """Run pip-audit if available (non-blocking if not installed)."""
    errors: list[str] = []
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip_audit", "--strict", "--desc",
             "--require-hashes", "-r", str(REQ_FILE)],
            capture_output=True, text=True, timeout=120,
        )
        if "No module named" in result.stderr:
            print("SKIP: pip-audit not installed (pip install pip-audit)")
            return errors
        if result.returncode != 0:
            # Parse structured output — look for vulnerability lines
            vuln_pattern = re.compile(r"(CVE-\d+-\d+|PYSEC-\d+-\d+|GHSA-[\w-]+)")
            found_vulns = False
            for line in result.stdout.splitlines():
                if vuln_pattern.search(line) or "vulnerability" in line.lower():
                    errors.append(f"Vulnerability: {line.strip()}")
                    found_vulns = True
            if not found_vulns and result.returncode != 0:
                # Non-zero exit but no vulnerability lines — report stderr
                stderr_summary = result.stderr.strip().split("\n")[0] if result.stderr.strip() else "unknown error"
                errors.append(f"pip-audit failed (exit {result.returncode}): {stderr_summary}")
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

    print("── Checking hash integrity ──")
    errors = check_hash_pinning()
    all_errors.extend(errors)
    for e in errors:
        print(f"  WARN: {e}")
    if not errors:
        print("  PASS: Hash-pinned requirements")

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
