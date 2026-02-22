• Here’s the reality check and a concrete tuning plan.

Current capacity (your config)

- concurrent_requests_per_process=5
- processes_per_instance=1
- capacity_buffer=0.2
- queue_depth_multiplier=2

Derived:

- Effective concurrent = 5 * 1 * (1 - 0.2) = 4
- Queue capacity = 5 * 1 * 2 = 10
- Total capacity = 14
- Registered blocked at ~0.8 * 14 = 11

So with 40 simultaneous users (15 registered + 15 paid + 10 admin), you will queue immediately and throttle.

———

## What to change (recommended baseline)

Target effective concurrent ≈ 30 to keep wait under ~10–15s for a 25s average turn.

### Option A (balanced, 1 instance)

Use this first; it’s realistic on a dev box with enough CPU:

Gateway config (must match actual workers):

service_capacity.processes_per_instance=5
service_capacity.concurrent_requests_per_process=6

Gateway config update:

{
"service_capacity": {
"concurrent_requests_per_process": 6,
"processes_per_instance": 5,
"avg_processing_time_seconds": 25
},
"backpressure": {
"capacity_buffer": 0.1,
"queue_depth_multiplier": 3,
"anonymous_pressure_threshold": 0.6,
"registered_pressure_threshold": 0.9,
"hard_limit_threshold": 0.98
},
"rate_limits": {
"anonymous": { "hourly": 120, "burst": 10, "burst_window": 60 },
"registered": { "hourly": 2000, "burst": 100, "burst_window": 60 },
"paid": { "hourly": 4000, "burst": 120, "burst_window": 60 },
"privileged": { "hourly": -1, "burst": 300, "burst_window": 60 }
}
}

Derived:

- Effective concurrent ≈ 27
- Queue capacity = 30 * 3 = 90
- Total capacity ≈ 117
  This should handle your 40 users comfortably.

### Option B (lighter CPU)

If you want fewer processes:

Gateway config

service_capacity.processes_per_instance=4
service_capacity.concurrent_requests_per_process=6

Backpressure

capacity_buffer=0.1
queue_depth_multiplier=3
registered_pressure_threshold=0.9
hard_limit_threshold=0.98

Effective concurrent ≈ 22. Still OK, but expect some queue wait under a full 40‑user spike.

———

## Critical note

Changing processes_per_instance does nothing unless you actually run that many chat REST processes.

Make sure:

- service_capacity.processes_per_instance matches real worker count
- You actually start the service with that many processes
- Monitoring shows healthy_processes ≥ expected

———

## How to validate in Monitoring

Check Capacity Transparency:

- actual_capacity should reflect real processes
- healthy_processes should match service_capacity.processes_per_instance

Queue metrics:

- queue_total should stay < registered threshold
- avg_wait_time should remain reasonable


```shell
  set -euo pipefail

  echo "== OS / Kernel ==" && uname -a && cat /etc/os-release || true
  echo

  echo "== CPU ==" && lscpu || true
  echo

  echo "== Memory ==" && free -h || true
  echo

  echo "== Disk ==" && df -h || true
  echo

  echo "== Load ==" && uptime || true
  echo

  echo "== EC2 Instance Type (IMDSv2) =="
  TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 60") || true
  if [ -n "${TOKEN:-}" ]; then
    curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
      http://169.254.169.254/latest/meta-data/instance-type && echo
  else
    echo "IMDSv2 token fetch failed (IMDS may be disabled)."
  fi
```

-------------

• Given t3.xlarge (4 vCPU, 16 GiB) and a bundle that’s ~95% IO‑bound, you can safely raise concurrency per
process rather than adding more processes.

Here’s a safe starting point that should handle 15 registered + 15 paid + 10 admins:

## 1) Gateway config (must match actual workers)

service_capacity.processes_per_instance=4
service_capacity.concurrent_requests_per_process=8
MAX_QUEUE_SIZE=200     # optional safety cap; 0 = unlimited

This gives:

- Total concurrent slots = 4 × 8 = 32
- Effective concurrent (with 10% buffer) ≈ 28

That’s enough for 40 active users if they’re not all sending simultaneously.

———

## 2) Gateway config update

```json
{
  "service_capacity": {
    "concurrent_requests_per_process": 8,
    "processes_per_instance": 4,
    "avg_processing_time_seconds": 25
  },
  "backpressure": {
    "capacity_buffer": 0.1,
    "queue_depth_multiplier": 3,
    "anonymous_pressure_threshold": 0.6,
    "registered_pressure_threshold": 0.9,
    "hard_limit_threshold": 0.98
  },
  "rate_limits": {
    "anonymous": { "hourly": 120, "burst": 10, "burst_window": 60 },
    "registered": { "hourly": 2000, "burst": 100, "burst_window": 60 },
    "paid": { "hourly": 4000, "burst": 150, "burst_window": 60 },
    "privileged": { "hourly": -1, "burst": 300, "burst_window": 60 }
  }
}
```

Why these changes

- Higher concurrent_requests_per_process leverages IO‑bound nature.
- queue_depth_multiplier=3 gives buffer for short spikes.
- Higher burst limits avoid UI reload throttling.

———

## 3) If you want to push harder later

Try:

service_capacity.concurrent_requests_per_process=10

But only if:

- CPU stays < 70–80%
- avg_wait_time doesn’t spike

———

## 4) What to monitor

In Gateway Monitoring:

- queue_utilization
- avg_wait_time
- healthy_processes (must equal 4)

If avg_wait_time grows quickly, reduce `service_capacity.concurrent_requests_per_process` or increase queue depth.
