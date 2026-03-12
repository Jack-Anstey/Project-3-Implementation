"""Orchestrator for the RPS-targeted load test suite.

Starts a timestamped run, waits for the server, runs Locust, then generates reports.

Usage:
    python tests/run_load_test.py --host http://localhost:8080/order-processing
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

import requests


def wait_for_health(host: str, timeout: int = 30) -> bool:
    """Poll GET /health until 200 or timeout."""
    url = f"{host}/health"
    print(f"[orchestrator] Waiting for {url} ...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                print("[orchestrator] Server is healthy.")
                return True
        except requests.ConnectionError:
            pass
        time.sleep(1)
    print(f"[orchestrator] Server did not respond within {timeout}s.")
    return False


def main():
    parser = argparse.ArgumentParser(description="Run RPS-targeted load test and generate reports")
    parser.add_argument("--host", default="http://localhost:8080/order-processing",
                        help="Base URL of the running server")
    parser.add_argument("--run-time", default="280s",
                        help="Total Locust run time (default: 280s for 4 tiers)")
    args = parser.parse_args()

    # 1. Create timestamped output directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join("reports", f"load_test_{timestamp}")
    os.makedirs(out_dir, exist_ok=True)
    csv_prefix = os.path.join(out_dir, "stats")
    print(f"[orchestrator] Output directory: {out_dir}")

    # 2. Wait for server health
    if not wait_for_health(args.host):
        print("[orchestrator] Aborting — server not available.")
        sys.exit(1)

    # 3. Run Locust
    locust_cmd = [
        sys.executable, "-m", "locust",
        "-f", "tests/load_test_suite.py",
        "--host", args.host,
        "--csv", csv_prefix,
        "--csv-full-history",
        "--headless",
        "--run-time", args.run_time,
    ]
    print(f"[orchestrator] Running Locust: {' '.join(locust_cmd)}")
    result = subprocess.run(locust_cmd)
    if result.returncode != 0:
        print(f"[orchestrator] Locust exited with code {result.returncode}")
        # Continue to report generation — partial data is still useful

    # 4. Generate reports
    report_cmd = [
        sys.executable, "tests/generate_report.py",
        "--csv-dir", out_dir,
        "--host", args.host,
        "--output-dir", out_dir,
    ]
    print(f"[orchestrator] Generating reports...")
    result = subprocess.run(report_cmd)
    if result.returncode != 0:
        print(f"[orchestrator] Report generation failed with code {result.returncode}")
        sys.exit(1)

    # 5. Print summary
    print(f"\n{'='*60}")
    print(f"  Load test complete!")
    print(f"  Output directory: {out_dir}")
    print(f"  Reports:")
    print(f"    - {os.path.join(out_dir, 'report.md')}")
    print(f"    - {os.path.join(out_dir, 'report.html')}")
    print(f"    - {os.path.join(out_dir, 'summary.json')}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
