export function createApiClient({ state, requiredKey, authHeaders }) {
  async function get(path) {
    requiredKey();
    if (Date.now() < state.net.readThrottleUntil) {
      throw new Error("read_rate_limited");
    }
    const cacheKey = `GET ${path}`;
    const existing = state.net.inflightGet.get(cacheKey);
    if (existing) {
      state.net.dedupHits += 1;
      return existing;
    }
    const promise = (async () => {
      state.net.requestCount += 1;
      const cached = state.net.etagCache.get(cacheKey);
      const headers = authHeaders();
      if (cached?.etag) headers["If-None-Match"] = cached.etag;
      const res = await fetch(path, { headers });
      if (res.status === 304 && cached) return cached.payload;
      if (!res.ok) {
        let detail = `${res.status}`;
        try {
          const body = await res.json();
          detail = body.detail || detail;
        } catch (_err) {
          // ignore
        }
        if (res.status === 429 && String(detail).includes("read_rate_limited")) {
          state.net.readBackoffMs = Math.min(30000, Math.max(2000, state.net.readBackoffMs ? state.net.readBackoffMs * 2 : 2000));
          state.net.readThrottleUntil = Date.now() + state.net.readBackoffMs;
        }
        throw new Error(detail);
      }
      state.net.readBackoffMs = 0;
      state.net.readThrottleUntil = 0;
      const payload = await res.json();
      const etag = res.headers.get("ETag");
      if (etag) state.net.etagCache.set(cacheKey, { etag, payload });
      return payload;
    })();
    state.net.inflightGet.set(cacheKey, promise);
    try {
      return await promise;
    } finally {
      state.net.inflightGet.delete(cacheKey);
    }
  }

  async function post(path, payload) {
    requiredKey();
    const res = await fetch(path, {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      let detail = `${res.status}`;
      try {
        const body = await res.json();
        detail = body.detail || detail;
      } catch (_err) {
        // ignore
      }
      throw new Error(detail);
    }
    return res.json();
  }

  async function download(path, filename) {
    requiredKey();
    const res = await fetch(path, { headers: authHeaders() });
    if (!res.ok) {
      let detail = `${res.status}`;
      try {
        const body = await res.json();
        detail = body.detail || detail;
      } catch (_err) {
        // ignore
      }
      throw new Error(detail);
    }
    const blob = await res.blob();
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
  }

  return {
    get,
    post,
    download,
  };
}
