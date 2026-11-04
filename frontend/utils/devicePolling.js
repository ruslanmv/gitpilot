// Use the minimum interval supplied by GitHub (default five seconds) even
// before the very first poll; never request a token immediately after /code.
export function initialDevicePollInterval(data) {
  const suggested = Number(data?.interval);
  return Number.isFinite(suggested) && suggested >= 1
    ? Math.min(Math.ceil(suggested), 300) : 5;
}

// GitHub device authorization requires a minimum polling interval. Retry
// transient transport/upstream errors, but don't retry rejected credentials or
// a server-side session-storage failure after the device was authorized.
export function nextDevicePoll(status, data = {}, interval = 5, failures = 0) {
  const current = Number.isFinite(interval) && interval >= 1 ? interval : 5;
  if (status === 202) return { interval: current, failures: 0 };

  if (data?.error === "slow_down") {
    const suggested = Number(data.interval);
    return {
      interval: Math.max(current + 5, Number.isFinite(suggested) ? suggested : 0),
      failures: 0,
    };
  }

  if ([0, 500, 502, 503, 504].includes(status)
      && data?.code !== "session_storage_unavailable" && failures < 3) {
    return { interval: Math.min(Math.max(current * 2, 5), 30), failures: failures + 1 };
  }
  return null;
}
