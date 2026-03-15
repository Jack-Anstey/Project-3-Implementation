"""Report generator for RPS-targeted load test results.

Reads Locust CSVs + raw_requests.json, fetches live diagnostics from the server,
and produces Markdown, HTML, and JSON reports.

Usage:
    python tests/generate_report.py --csv-dir reports/load_test_XYZ \
        --host http://localhost:8080/order-processing \
        --output-dir reports/load_test_XYZ
"""

import argparse
import csv
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from string import Template

import requests

# ── Tier definitions (must match load_test_suite.STAGES) ─────────────────────
STAGES = [
    {"target_rps": 50, "duration": 60, "ramp": 10},
    {"target_rps": 100, "duration": 60, "ramp": 10},
    {"target_rps": 150, "duration": 60, "ramp": 10},
    {"target_rps": 200, "duration": 60, "ramp": 10},
]

# ── AWS cost constants ───────────────────────────────────────────────────────
LAMBDA_INVOCATION_COST = 0.20 / 1_000_000          # per request
LAMBDA_GB_S_COST = 0.0000166667                     # per GB-second
LAMBDA_MEMORY_GB = 128 / 1024                       # 128 MB
API_GATEWAY_COST = 3.50 / 1_000_000                 # per request
DYNAMO_WCU_COST = 0.00065 / 3600                    # per WCU-second (on-demand approx)
DYNAMO_RCU_COST = 0.00013 / 3600                    # per RCU-second (on-demand approx)
DYNAMO_WRITES_PER_ORDER = 3
DYNAMO_READS_PER_ORDER = 1

SCENARIOS = {
    "24/7 (constant)": 24 * 30,        # hours/month
    "Peak hours (8h/day)": 8 * 30,
    "Burst (1h/day)": 1 * 30,
}


# ═════════════════════════════════════════════════════════════════════════════
#  Data loading
# ═════════════════════════════════════════════════════════════════════════════

def load_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_raw_requests(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def fetch_diagnostics(host: str) -> dict:
    """Fetch application-level diagnostics from the running server."""
    endpoints = {
        "analytics_summary": "/analytics/summary",
        "top_skus": "/analytics/top-skus?limit=10",
        "retry_pending": "/retry-queue/pending",
        "dead_letters": "/retry-queue/dead-letters",
        "sagas": "/saga/all",
    }
    result = {}
    for key, path in endpoints.items():
        try:
            resp = requests.get(f"{host}{path}", timeout=10)
            result[key] = resp.json()
        except Exception as e:
            result[key] = {"error": str(e)}
    return result


# ═════════════════════════════════════════════════════════════════════════════
#  Per-tier analysis
# ═════════════════════════════════════════════════════════════════════════════

def compute_tier_boundaries(raw_requests: list[dict]) -> list[tuple[float, float]]:
    """Return (start_ts, end_ts) for each tier based on stage durations."""
    if not raw_requests:
        return []
    t0 = raw_requests[0]["timestamp"]
    boundaries = []
    elapsed = 0.0
    for stage in STAGES:
        start = t0 + elapsed
        elapsed += stage["ramp"] + stage["duration"]
        end = t0 + elapsed
        boundaries.append((start, end))
    return boundaries


def compute_tier_metrics(raw_requests: list[dict], boundaries: list[tuple[float, float]]) -> list[dict]:
    """Compute per-tier latency percentiles, RPS, and error rates."""
    tiers = []
    for i, (start, end) in enumerate(boundaries):
        tier_reqs = [r for r in raw_requests if start <= r["timestamp"] < end]
        duration = end - start
        if not tier_reqs:
            tiers.append({
                "tier": i + 1,
                "target_rps": STAGES[i]["target_rps"],
                "actual_rps": 0,
                "total_requests": 0,
                "error_count": 0,
                "error_rate": 0,
                "latency": {},
                "per_endpoint": {},
            })
            continue

        latencies = [r["response_time"] for r in tier_reqs]
        errors = [r for r in tier_reqs if r["status_code"] >= 400 or r["status_code"] == 0]

        # Per-endpoint breakdown
        endpoints: dict[str, list[dict]] = {}
        for r in tier_reqs:
            endpoints.setdefault(r["name"], []).append(r)

        per_endpoint = {}
        for ep_name, ep_reqs in endpoints.items():
            ep_lat = [r["response_time"] for r in ep_reqs]
            ep_errors = [r for r in ep_reqs if r["status_code"] >= 400 or r["status_code"] == 0]
            per_endpoint[ep_name] = {
                "count": len(ep_reqs),
                "error_count": len(ep_errors),
                "error_rate": round(len(ep_errors) / len(ep_reqs) * 100, 2) if ep_reqs else 0,
                "p50": round(statistics.median(ep_lat), 2),
                "p90": round(statistics.quantiles(ep_lat, n=10)[-1], 2) if len(ep_lat) >= 2 else round(ep_lat[0], 2),
                "p95": round(statistics.quantiles(ep_lat, n=20)[-1], 2) if len(ep_lat) >= 2 else round(ep_lat[0], 2),
                "p99": round(statistics.quantiles(ep_lat, n=100)[-1], 2) if len(ep_lat) >= 2 else round(ep_lat[0], 2),
            }

        tiers.append({
            "tier": i + 1,
            "target_rps": STAGES[i]["target_rps"],
            "actual_rps": round(len(tier_reqs) / duration, 2),
            "total_requests": len(tier_reqs),
            "error_count": len(errors),
            "error_rate": round(len(errors) / len(tier_reqs) * 100, 2),
            "latency": {
                "p50": round(statistics.median(latencies), 2),
                "p90": round(statistics.quantiles(latencies, n=10)[-1], 2) if len(latencies) >= 2 else round(latencies[0], 2),
                "p95": round(statistics.quantiles(latencies, n=20)[-1], 2) if len(latencies) >= 2 else round(latencies[0], 2),
                "p99": round(statistics.quantiles(latencies, n=100)[-1], 2) if len(latencies) >= 2 else round(latencies[0], 2),
            },
            "per_endpoint": per_endpoint,
        })
    return tiers


# ═════════════════════════════════════════════════════════════════════════════
#  Bottleneck detection
# ═════════════════════════════════════════════════════════════════════════════

def detect_bottlenecks(tier_metrics: list[dict], diagnostics: dict) -> list[dict]:
    """Auto-detect bottlenecks and produce recommendations."""
    issues = []

    for tm in tier_metrics:
        tier = tm["tier"]
        # High P99 endpoints
        for ep, stats in tm["per_endpoint"].items():
            if stats["p99"] > 1000:
                issues.append({
                    "tier": tier,
                    "type": "high_latency",
                    "endpoint": ep,
                    "detail": f"P99 latency {stats['p99']}ms > 1000ms threshold",
                    "recommendation": "Profile this endpoint; consider async processing or caching.",
                })
            if stats["error_rate"] > 5:
                issues.append({
                    "tier": tier,
                    "type": "high_error_rate",
                    "endpoint": ep,
                    "detail": f"Error rate {stats['error_rate']}% > 5% threshold",
                    "recommendation": "Check server logs for root cause; may indicate resource exhaustion.",
                })

        # RPS plateau
        if tm["target_rps"] > 0 and tm["actual_rps"] < tm["target_rps"] * 0.8:
            issues.append({
                "tier": tier,
                "type": "throughput_plateau",
                "endpoint": "aggregate",
                "detail": f"Achieved {tm['actual_rps']} RPS vs target {tm['target_rps']} RPS ({round(tm['actual_rps']/tm['target_rps']*100, 1)}%)",
                "recommendation": "Server is saturated — increase uvicorn workers or scale horizontally.",
            })

    # Dead letters
    dead_letters = diagnostics.get("dead_letters", [])
    if isinstance(dead_letters, list) and len(dead_letters) > 0:
        issues.append({
            "tier": "all",
            "type": "dead_letters",
            "endpoint": "retry-queue",
            "detail": f"{len(dead_letters)} dead-letter tasks found — retries exhausted",
            "recommendation": "Investigate failed tasks; consider increasing retry limits or fixing upstream errors.",
        })

    # Stuck sagas
    sagas = diagnostics.get("sagas", {})
    if isinstance(sagas, dict):
        saga_list = sagas.get("sagas", [])
        non_terminal = [s for s in saga_list if s.get("current_status") not in ("confirmed", "released", "rejected", "not_found")]
        if len(non_terminal) > 10:
            issues.append({
                "tier": "all",
                "type": "stuck_sagas",
                "endpoint": "saga",
                "detail": f"{len(non_terminal)} sagas in non-terminal states",
                "recommendation": "Check reservation TTL and cleanup cycle; sagas may be orphaned under load.",
            })

    return issues


# ═════════════════════════════════════════════════════════════════════════════
#  Cost projection
# ═════════════════════════════════════════════════════════════════════════════

def compute_cost_projections(tier_metrics: list[dict]) -> list[dict]:
    """Monthly AWS cost projections per tier per scenario."""
    projections = []
    for tm in tier_metrics:
        rps = tm["actual_rps"]
        # Average duration in seconds from average latency
        avg_duration_s = (tm["latency"].get("p50", 50) / 1000) if tm["latency"] else 0.05

        for scenario_name, hours_per_month in SCENARIOS.items():
            total_requests = rps * 3600 * hours_per_month

            lambda_invocation = total_requests * LAMBDA_INVOCATION_COST
            lambda_duration = total_requests * avg_duration_s * LAMBDA_MEMORY_GB * LAMBDA_GB_S_COST
            api_gw = total_requests * API_GATEWAY_COST

            # DynamoDB: estimate order-intake is ~37% of traffic (weight 10+2+2+1 / total 29)
            order_fraction = 15 / 29
            order_requests = total_requests * order_fraction
            dynamo_write = order_requests * DYNAMO_WRITES_PER_ORDER * DYNAMO_WCU_COST
            dynamo_read = order_requests * DYNAMO_READS_PER_ORDER * DYNAMO_RCU_COST

            total = lambda_invocation + lambda_duration + api_gw + dynamo_write + dynamo_read

            projections.append({
                "tier": tm["tier"],
                "target_rps": tm["target_rps"],
                "actual_rps": tm["actual_rps"],
                "scenario": scenario_name,
                "monthly_requests": int(total_requests),
                "lambda_invocation": round(lambda_invocation, 2),
                "lambda_duration": round(lambda_duration, 2),
                "api_gateway": round(api_gw, 2),
                "dynamodb_estimated": round(dynamo_write + dynamo_read, 2),
                "total_monthly": round(total, 2),
            })
    return projections


# ═════════════════════════════════════════════════════════════════════════════
#  Report generation — Markdown
# ═════════════════════════════════════════════════════════════════════════════

def generate_markdown(
    tier_metrics: list[dict],
    bottlenecks: list[dict],
    cost_projections: list[dict],
    diagnostics: dict,
    locust_stats: list[dict],
    locust_failures: list[dict],
    csv_dir: str,
) -> str:
    lines = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines.append(f"# Load Test Report\n")
    lines.append(f"**Generated**: {now}\n")

    # ── 1. Executive Summary ─────────────────────────────────────────────
    lines.append("## 1. Executive Summary\n")
    for tm in tier_metrics:
        status = "PASS" if tm["error_rate"] < 5 and tm["actual_rps"] >= tm["target_rps"] * 0.8 else "WARN"
        lines.append(
            f"- **Tier {tm['tier']}** ({tm['target_rps']} RPS target): "
            f"**{status}** — {tm['actual_rps']} RPS achieved, "
            f"{tm['error_rate']}% error rate, P50={tm['latency'].get('p50', 'N/A')}ms"
        )
    lines.append("")

    # ── 2. Throughput Analysis ───────────────────────────────────────────
    lines.append("## 2. Throughput Analysis\n")
    lines.append("| Tier | Target RPS | Actual RPS | Total Requests | Achievement |")
    lines.append("|------|-----------|------------|----------------|-------------|")
    for tm in tier_metrics:
        pct = round(tm["actual_rps"] / tm["target_rps"] * 100, 1) if tm["target_rps"] > 0 else 0
        lines.append(
            f"| {tm['tier']} | {tm['target_rps']} | {tm['actual_rps']} | "
            f"{tm['total_requests']} | {pct}% |"
        )
    lines.append("")

    # ── 3. Latency Analysis ──────────────────────────────────────────────
    lines.append("## 3. Latency Analysis\n")
    for tm in tier_metrics:
        lines.append(f"### Tier {tm['tier']} ({tm['target_rps']} RPS)\n")
        lines.append("| Endpoint | Count | P50 (ms) | P90 (ms) | P95 (ms) | P99 (ms) | Error % |")
        lines.append("|----------|-------|----------|----------|----------|----------|---------|")
        for ep, stats in sorted(tm["per_endpoint"].items()):
            lines.append(
                f"| {ep} | {stats['count']} | {stats['p50']} | {stats['p90']} | "
                f"{stats['p95']} | {stats['p99']} | {stats['error_rate']}% |"
            )
        lines.append("")

    # ── 4. Error Analysis ────────────────────────────────────────────────
    lines.append("## 4. Error Analysis\n")
    lines.append("| Tier | Total Requests | Errors | Error Rate |")
    lines.append("|------|----------------|--------|------------|")
    for tm in tier_metrics:
        lines.append(f"| {tm['tier']} | {tm['total_requests']} | {tm['error_count']} | {tm['error_rate']}% |")
    lines.append("")

    if locust_failures:
        lines.append("### Failure Details\n")
        lines.append("| Method | Name | Count | Message |")
        lines.append("|--------|------|-------|---------|")
        for f in locust_failures:
            lines.append(f"| {f.get('Method', '')} | {f.get('Name', '')} | {f.get('Occurrences', '')} | {f.get('Message', '')[:80]} |")
        lines.append("")

    # ── 5. Bottleneck Identification ─────────────────────────────────────
    lines.append("## 5. Bottleneck Identification\n")
    if not bottlenecks:
        lines.append("No bottlenecks detected.\n")
    else:
        for b in bottlenecks:
            lines.append(f"- **[Tier {b['tier']}] {b['type']}** — {b['endpoint']}: {b['detail']}")
            lines.append(f"  - *Recommendation*: {b['recommendation']}")
        lines.append("")

    # ── 6. AWS Cost Projection ───────────────────────────────────────────
    lines.append("## 6. AWS Cost Projection\n")
    lines.append("| Tier | Scenario | Monthly Requests | Lambda ($) | API GW ($) | DynamoDB ($) | Total ($) |")
    lines.append("|------|----------|-----------------|------------|------------|-------------|-----------|")
    for cp in cost_projections:
        lambda_total = cp["lambda_invocation"] + cp["lambda_duration"]
        lines.append(
            f"| {cp['tier']} ({cp['target_rps']} RPS) | {cp['scenario']} | "
            f"{cp['monthly_requests']:,} | {lambda_total:.2f} | {cp['api_gateway']:.2f} | "
            f"{cp['dynamodb_estimated']:.2f} | {cp['total_monthly']:.2f} |"
        )
    lines.append("")
    lines.append("*Assumptions: Lambda 128 MB, REST API Gateway pricing, DynamoDB on-demand (~3 writes + 1 read per order).*\n")

    # ── 7. Application Diagnostics ───────────────────────────────────────
    lines.append("## 7. Application Diagnostics\n")

    analytics = diagnostics.get("analytics_summary", {})
    if "error" not in analytics:
        lines.append("### Analytics Summary\n")
        lines.append(f"- Total orders processed: {analytics.get('total_orders', 'N/A')}")
        lines.append(f"- Accepted items: {analytics.get('accepted_items', 'N/A')}")
        lines.append(f"- Rejected items: {analytics.get('rejected_items', 'N/A')}")
        lines.append(f"- Acceptance rate: {analytics.get('acceptance_rate', 'N/A')}")
        lines.append("")

    top_skus = diagnostics.get("top_skus", {})
    if "error" not in top_skus:
        sku_list = top_skus.get("top_skus", [])
        if sku_list:
            lines.append("### Top SKUs\n")
            lines.append("| Rank | SKU | Quantity |")
            lines.append("|------|-----|----------|")
            for i, s in enumerate(sku_list[:10], 1):
                lines.append(f"| {i} | {s.get('sku', '')} | {s.get('total_quantity', '')} |")
            lines.append("")

    sagas = diagnostics.get("sagas", {})
    if isinstance(sagas, dict) and "error" not in sagas:
        saga_list = sagas.get("sagas", [])
        if saga_list:
            status_counts: dict[str, int] = {}
            for s in saga_list:
                st = s.get("current_status", "unknown")
                status_counts[st] = status_counts.get(st, 0) + 1
            lines.append("### Saga State Distribution\n")
            lines.append("| Status | Count |")
            lines.append("|--------|-------|")
            for st, cnt in sorted(status_counts.items()):
                lines.append(f"| {st} | {cnt} |")
            lines.append("")

    # ── 8. Known Limitations ─────────────────────────────────────────────
    lines.append("## 8. Known Limitations\n")
    lines.append("- **Single-worker uvicorn**: Default `workers=1` may saturate at higher RPS tiers. Recommend re-testing with `workers=4`.")
    lines.append("- **In-memory storage caps**: `InMemoryAnalytics` caps at 10,000 events; at 200 RPS this evicts data mid-test. Recommend DynamoDB for production load tests.")
    lines.append("- **3-second reservation TTL**: The 2-second cleanup cycle + 3s TTL causes heavy reservation churn under load, creating lock contention on inventory.\n")

    # ── 9. Appendix ──────────────────────────────────────────────────────
    lines.append("## 9. Appendix\n")
    lines.append(f"- Raw CSV data: `{csv_dir}/`")
    lines.append(f"- Raw request log: `{csv_dir}/raw_requests.json`")
    lines.append(f"- Locust stats: `{csv_dir}/stats_stats.csv`")
    lines.append(f"- Locust failures: `{csv_dir}/stats_failures.csv`")
    lines.append("")

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
#  Report generation — HTML
# ═════════════════════════════════════════════════════════════════════════════

HTML_TEMPLATE = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Load Test Report</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         max-width: 1200px; margin: 0 auto; padding: 20px; color: #333; background: #f8f9fa; }
  h1 { color: #1a1a2e; margin-bottom: 8px; }
  h2 { color: #16213e; margin: 32px 0 16px; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; }
  h3 { color: #0f3460; margin: 20px 0 12px; }
  .timestamp { color: #666; margin-bottom: 24px; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0 24px; }
  th, td { border: 1px solid #d1d5db; padding: 8px 12px; text-align: left; font-size: 14px; }
  th { background: #1a1a2e; color: white; }
  tr:nth-child(even) { background: #f1f5f9; }
  .pass { color: #059669; font-weight: bold; }
  .warn { color: #d97706; font-weight: bold; }
  .green { background: #d1fae5; }
  .yellow { background: #fef3c7; }
  .red { background: #fee2e2; }
  .bottleneck { background: #fff7ed; border-left: 4px solid #f59e0b; padding: 12px; margin: 8px 0; border-radius: 4px; }
  .recommendation { color: #4b5563; font-style: italic; margin-top: 4px; }
  .summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; margin: 16px 0; }
  .summary-card { background: white; border-radius: 8px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
  .summary-card .value { font-size: 28px; font-weight: bold; color: #1a1a2e; }
  .summary-card .label { font-size: 13px; color: #6b7280; margin-top: 4px; }
  .bar-chart { display: flex; align-items: flex-end; gap: 8px; height: 120px; margin: 16px 0; }
  .bar-group { display: flex; flex-direction: column; align-items: center; flex: 1; }
  .bar { width: 100%; border-radius: 4px 4px 0 0; min-height: 4px; transition: height 0.3s; }
  .bar-label { font-size: 11px; color: #666; margin-top: 4px; }
  .bar-value { font-size: 11px; font-weight: bold; margin-bottom: 2px; }
  .bar-target { background: #93c5fd; }
  .bar-actual { background: #3b82f6; }
  .legend { display: flex; gap: 16px; font-size: 12px; margin-bottom: 8px; }
  .legend-item { display: flex; align-items: center; gap: 4px; }
  .legend-swatch { width: 12px; height: 12px; border-radius: 2px; }
  .limitations { background: #fffbeb; border: 1px solid #fbbf24; border-radius: 8px; padding: 16px; margin: 16px 0; }
  ul { margin-left: 20px; }
  li { margin: 4px 0; }
</style>
</head>
<body>
<h1>Load Test Report</h1>
<p class="timestamp">Generated: $timestamp</p>

<h2>1. Executive Summary</h2>
<div class="summary-grid">
$summary_cards
</div>

<h2>2. Throughput Analysis</h2>
<div class="legend">
  <div class="legend-item"><div class="legend-swatch bar-target"></div> Target RPS</div>
  <div class="legend-item"><div class="legend-swatch bar-actual"></div> Actual RPS</div>
</div>
<div class="bar-chart">
$throughput_bars
</div>
$throughput_table

<h2>3. Latency Analysis</h2>
$latency_tables

<h2>4. Error Analysis</h2>
$error_table
$failure_details

<h2>5. Bottleneck Identification</h2>
$bottleneck_section

<h2>6. AWS Cost Projection</h2>
$cost_table
<p><em>Assumptions: Lambda 128 MB, REST API Gateway pricing, DynamoDB on-demand (~3 writes + 1 read per order).</em></p>

<h2>7. Application Diagnostics</h2>
$diagnostics_section

<h2>8. Known Limitations</h2>
<div class="limitations">
<ul>
  <li><strong>Single-worker uvicorn</strong>: Default workers=1 may saturate at higher RPS tiers. Recommend re-testing with workers=4.</li>
  <li><strong>In-memory storage caps</strong>: InMemoryAnalytics caps at 10,000 events; at 200 RPS this evicts data mid-test. Recommend DynamoDB for production load tests.</li>
  <li><strong>3-second reservation TTL</strong>: The 2-second cleanup cycle + 3s TTL causes heavy reservation churn under load, creating lock contention on inventory.</li>
</ul>
</div>

<h2>9. Appendix</h2>
<ul>
  <li>Raw CSV data: <code>$csv_dir/</code></li>
  <li>Raw request log: <code>$csv_dir/raw_requests.json</code></li>
</ul>

</body>
</html>
""")


def _latency_class(ms: float) -> str:
    if ms < 200:
        return "green"
    if ms < 1000:
        return "yellow"
    return "red"


def generate_html(
    tier_metrics: list[dict],
    bottlenecks: list[dict],
    cost_projections: list[dict],
    diagnostics: dict,
    locust_stats: list[dict],
    locust_failures: list[dict],
    csv_dir: str,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Summary cards
    cards = []
    for tm in tier_metrics:
        status_cls = "pass" if tm["error_rate"] < 5 and tm["actual_rps"] >= tm["target_rps"] * 0.8 else "warn"
        status_label = "PASS" if status_cls == "pass" else "WARN"
        cards.append(
            f'<div class="summary-card">'
            f'<div class="value">Tier {tm["tier"]} — <span class="{status_cls}">{status_label}</span></div>'
            f'<div class="label">{tm["actual_rps"]} / {tm["target_rps"]} RPS &bull; '
            f'{tm["error_rate"]}% errors &bull; P50 {tm["latency"].get("p50", "N/A")}ms</div>'
            f'</div>'
        )

    # Throughput bars (SVG-less, pure CSS)
    max_rps = max((tm["target_rps"] for tm in tier_metrics), default=1)
    bars = []
    for tm in tier_metrics:
        t_h = int(tm["target_rps"] / max_rps * 100)
        a_h = int(tm["actual_rps"] / max_rps * 100) if max_rps > 0 else 0
        bars.append(
            f'<div class="bar-group">'
            f'<div class="bar-value">{tm["actual_rps"]}</div>'
            f'<div style="display:flex;gap:4px;align-items:flex-end;height:100px;width:100%">'
            f'<div class="bar bar-target" style="height:{t_h}px;flex:1"></div>'
            f'<div class="bar bar-actual" style="height:{a_h}px;flex:1"></div>'
            f'</div>'
            f'<div class="bar-label">Tier {tm["tier"]}<br>{tm["target_rps"]} RPS</div>'
            f'</div>'
        )

    # Throughput table
    t_rows = []
    for tm in tier_metrics:
        pct = round(tm["actual_rps"] / tm["target_rps"] * 100, 1) if tm["target_rps"] > 0 else 0
        t_rows.append(
            f'<tr><td>{tm["tier"]}</td><td>{tm["target_rps"]}</td><td>{tm["actual_rps"]}</td>'
            f'<td>{tm["total_requests"]}</td><td>{pct}%</td></tr>'
        )
    throughput_table = (
        '<table><tr><th>Tier</th><th>Target RPS</th><th>Actual RPS</th><th>Total Requests</th><th>Achievement</th></tr>'
        + "".join(t_rows)
        + '</table>'
    )

    # Latency tables
    lat_sections = []
    for tm in tier_metrics:
        rows = []
        for ep, stats in sorted(tm["per_endpoint"].items()):
            rows.append(
                f'<tr><td>{ep}</td><td>{stats["count"]}</td>'
                f'<td class="{_latency_class(stats["p50"])}">{stats["p50"]}</td>'
                f'<td class="{_latency_class(stats["p90"])}">{stats["p90"]}</td>'
                f'<td class="{_latency_class(stats["p95"])}">{stats["p95"]}</td>'
                f'<td class="{_latency_class(stats["p99"])}">{stats["p99"]}</td>'
                f'<td>{stats["error_rate"]}%</td></tr>'
            )
        lat_sections.append(
            f'<h3>Tier {tm["tier"]} ({tm["target_rps"]} RPS)</h3>'
            f'<table><tr><th>Endpoint</th><th>Count</th><th>P50 (ms)</th><th>P90 (ms)</th>'
            f'<th>P95 (ms)</th><th>P99 (ms)</th><th>Error %</th></tr>'
            + "".join(rows)
            + '</table>'
        )

    # Error table
    e_rows = []
    for tm in tier_metrics:
        e_rows.append(
            f'<tr><td>{tm["tier"]}</td><td>{tm["total_requests"]}</td>'
            f'<td>{tm["error_count"]}</td><td>{tm["error_rate"]}%</td></tr>'
        )
    error_table = (
        '<table><tr><th>Tier</th><th>Total Requests</th><th>Errors</th><th>Error Rate</th></tr>'
        + "".join(e_rows)
        + '</table>'
    )

    # Failure details
    failure_html = ""
    if locust_failures:
        f_rows = []
        for f in locust_failures:
            msg = f.get("Message", "")[:100]
            f_rows.append(
                f'<tr><td>{f.get("Method", "")}</td><td>{f.get("Name", "")}</td>'
                f'<td>{f.get("Occurrences", "")}</td><td>{msg}</td></tr>'
            )
        failure_html = (
            '<h3>Failure Details</h3>'
            '<table><tr><th>Method</th><th>Name</th><th>Count</th><th>Message</th></tr>'
            + "".join(f_rows)
            + '</table>'
        )

    # Bottlenecks
    if not bottlenecks:
        bottleneck_html = "<p>No bottlenecks detected.</p>"
    else:
        parts = []
        for b in bottlenecks:
            parts.append(
                f'<div class="bottleneck">'
                f'<strong>[Tier {b["tier"]}] {b["type"]}</strong> &mdash; {b["endpoint"]}: {b["detail"]}'
                f'<div class="recommendation">Recommendation: {b["recommendation"]}</div>'
                f'</div>'
            )
        bottleneck_html = "".join(parts)

    # Cost table
    c_rows = []
    for cp in cost_projections:
        lambda_total = cp["lambda_invocation"] + cp["lambda_duration"]
        c_rows.append(
            f'<tr><td>{cp["tier"]} ({cp["target_rps"]} RPS)</td><td>{cp["scenario"]}</td>'
            f'<td>{cp["monthly_requests"]:,}</td><td>${lambda_total:.2f}</td>'
            f'<td>${cp["api_gateway"]:.2f}</td><td>${cp["dynamodb_estimated"]:.2f}</td>'
            f'<td><strong>${cp["total_monthly"]:.2f}</strong></td></tr>'
        )
    cost_table = (
        '<table><tr><th>Tier</th><th>Scenario</th><th>Monthly Requests</th>'
        '<th>Lambda ($)</th><th>API GW ($)</th><th>DynamoDB ($)</th><th>Total ($)</th></tr>'
        + "".join(c_rows)
        + '</table>'
    )

    # Diagnostics
    diag_parts = []
    analytics = diagnostics.get("analytics_summary", {})
    if "error" not in analytics:
        diag_parts.append(
            f'<h3>Analytics Summary</h3><ul>'
            f'<li>Total orders: {analytics.get("total_orders", "N/A")}</li>'
            f'<li>Accepted items: {analytics.get("accepted_items", "N/A")}</li>'
            f'<li>Rejected items: {analytics.get("rejected_items", "N/A")}</li>'
            f'<li>Acceptance rate: {analytics.get("acceptance_rate", "N/A")}</li></ul>'
        )

    top_skus = diagnostics.get("top_skus", {})
    if "error" not in top_skus:
        sku_list = top_skus.get("top_skus", [])
        if sku_list:
            s_rows = "".join(
                f'<tr><td>{i}</td><td>{s.get("sku","")}</td><td>{s.get("total_quantity","")}</td></tr>'
                for i, s in enumerate(sku_list[:10], 1)
            )
            diag_parts.append(
                f'<h3>Top SKUs</h3>'
                f'<table><tr><th>Rank</th><th>SKU</th><th>Quantity</th></tr>{s_rows}</table>'
            )

    sagas = diagnostics.get("sagas", {})
    if isinstance(sagas, dict) and "error" not in sagas:
        saga_list = sagas.get("sagas", [])
        if saga_list:
            status_counts: dict[str, int] = {}
            for s in saga_list:
                st = s.get("current_status", "unknown")
                status_counts[st] = status_counts.get(st, 0) + 1
            sg_rows = "".join(f'<tr><td>{st}</td><td>{cnt}</td></tr>' for st, cnt in sorted(status_counts.items()))
            diag_parts.append(
                f'<h3>Saga State Distribution</h3>'
                f'<table><tr><th>Status</th><th>Count</th></tr>{sg_rows}</table>'
            )

    return HTML_TEMPLATE.substitute(
        timestamp=now,
        summary_cards="\n".join(cards),
        throughput_bars="\n".join(bars),
        throughput_table=throughput_table,
        latency_tables="\n".join(lat_sections),
        error_table=error_table,
        failure_details=failure_html,
        bottleneck_section=bottleneck_html,
        cost_table=cost_table,
        diagnostics_section="\n".join(diag_parts) if diag_parts else "<p>Diagnostics unavailable.</p>",
        csv_dir=csv_dir,
    )


# ═════════════════════════════════════════════════════════════════════════════
#  Main
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Generate load test report")
    parser.add_argument("--csv-dir", required=True, help="Directory containing Locust CSV output + raw_requests.json")
    parser.add_argument("--host", required=True, help="Base URL of the running server (e.g. http://localhost:8080/order-processing)")
    parser.add_argument("--output-dir", required=True, help="Directory to write report files")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # Load data
    print("[report] Loading Locust CSV data...")
    locust_stats = load_csv(os.path.join(args.csv_dir, "stats_stats.csv"))
    locust_failures = load_csv(os.path.join(args.csv_dir, "stats_failures.csv"))

    print("[report] Loading raw requests...")
    raw_requests = load_raw_requests(os.path.join(args.csv_dir, "raw_requests.json"))

    print("[report] Fetching diagnostics from server...")
    diagnostics = fetch_diagnostics(args.host)

    # Compute metrics
    print("[report] Computing per-tier metrics...")
    boundaries = compute_tier_boundaries(raw_requests)
    tier_metrics = compute_tier_metrics(raw_requests, boundaries)

    print("[report] Detecting bottlenecks...")
    bottlenecks = detect_bottlenecks(tier_metrics, diagnostics)

    print("[report] Computing cost projections...")
    cost_projections = compute_cost_projections(tier_metrics)

    # Generate reports
    print("[report] Generating Markdown report...")
    md = generate_markdown(tier_metrics, bottlenecks, cost_projections, diagnostics, locust_stats, locust_failures, args.csv_dir)
    md_path = os.path.join(args.output_dir, "report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    print("[report] Generating HTML report...")
    html = generate_html(tier_metrics, bottlenecks, cost_projections, diagnostics, locust_stats, locust_failures, args.csv_dir)
    html_path = os.path.join(args.output_dir, "report.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print("[report] Generating summary JSON...")
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tiers": tier_metrics,
        "bottlenecks": bottlenecks,
        "cost_projections": cost_projections,
        "diagnostics": diagnostics,
    }
    json_path = os.path.join(args.output_dir, "summary.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"\n[report] Done! Reports written to:")
    print(f"  Markdown: {md_path}")
    print(f"  HTML:     {html_path}")
    print(f"  JSON:     {json_path}")


if __name__ == "__main__":
    main()
