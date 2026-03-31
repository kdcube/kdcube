const INITIAL_DATA = __PREFERENCES_JSON__;

function PreferencesBrowser() {
  const [query, setQuery] = React.useState("");
  const currentEntries = Object.entries(INITIAL_DATA.current || {});
  const recent = INITIAL_DATA.recent || [];
  const filterValue = query.trim().toLowerCase();

  const visibleCurrent = currentEntries.filter(([key, value]) => {
    const haystack = `${key} ${value?.value || ""}`.toLowerCase();
    return !filterValue || haystack.includes(filterValue);
  });

  const visibleRecent = recent.filter((item) => {
    const haystack = `${item.key || ""} ${item.value || ""} ${item.evidence || ""}`.toLowerCase();
    return !filterValue || haystack.includes(filterValue);
  });

  return (
    <div style={{
      fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      minHeight: "100vh",
      margin: 0,
      background: "linear-gradient(160deg, #f6f4ee 0%, #f3efe2 45%, #e8f0ed 100%)",
      color: "#18231d",
      padding: "24px",
      boxSizing: "border-box",
    }}>
      <div style={{
        maxWidth: "1100px",
        margin: "0 auto",
        display: "grid",
        gap: "18px",
      }}>
        <section style={{
          background: "rgba(255,255,255,0.78)",
          border: "1px solid rgba(24,35,29,0.12)",
          borderRadius: "24px",
          padding: "24px",
          boxShadow: "0 24px 64px rgba(24,35,29,0.08)",
        }}>
          <div style={{ display: "flex", gap: "12px", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}>
            <div>
              <div style={{ fontSize: "13px", textTransform: "uppercase", letterSpacing: "0.12em", opacity: 0.6 }}>
                Versatile Bundle Widget
              </div>
              <h1 style={{ margin: "8px 0 6px", fontSize: "32px", lineHeight: 1.1 }}>
                Preference Browser
              </h1>
              <p style={{ margin: 0, maxWidth: "720px", opacity: 0.8 }}>
                Current preferences and recent preference observations captured for
                <strong> {INITIAL_DATA.user_id}</strong>.
              </p>
            </div>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Filter by key, value, or evidence"
              style={{
                minWidth: "260px",
                padding: "12px 16px",
                borderRadius: "999px",
                border: "1px solid rgba(24,35,29,0.18)",
                outline: "none",
                fontSize: "14px",
                background: "rgba(255,255,255,0.92)",
              }}
            />
          </div>
        </section>

        <section style={{ display: "grid", gridTemplateColumns: "1.1fr 1fr", gap: "18px" }}>
          <div style={{
            background: "rgba(255,255,255,0.86)",
            border: "1px solid rgba(24,35,29,0.12)",
            borderRadius: "24px",
            padding: "22px",
          }}>
            <h2 style={{ marginTop: 0 }}>Current snapshot</h2>
            {visibleCurrent.length === 0 ? (
              <p style={{ opacity: 0.72 }}>No current preferences matched the filter.</p>
            ) : (
              <div style={{ display: "grid", gap: "12px" }}>
                {visibleCurrent.map(([key, value]) => (
                  <div key={key} style={{
                    padding: "14px 16px",
                    borderRadius: "18px",
                    background: "#f8faf8",
                    border: "1px solid rgba(24,35,29,0.08)",
                  }}>
                    <div style={{ fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.08em", opacity: 0.6 }}>
                      {key}
                    </div>
                    <div style={{ fontSize: "18px", marginTop: "4px" }}>{String(value?.value ?? "")}</div>
                    <div style={{ fontSize: "12px", marginTop: "8px", opacity: 0.65 }}>
                      {value?.origin || "unknown"} • {value?.updated_at || "unknown time"}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div style={{
            background: "rgba(255,255,255,0.86)",
            border: "1px solid rgba(24,35,29,0.12)",
            borderRadius: "24px",
            padding: "22px",
          }}>
            <h2 style={{ marginTop: 0 }}>Recent observations</h2>
            {visibleRecent.length === 0 ? (
              <p style={{ opacity: 0.72 }}>No recent observations matched the filter.</p>
            ) : (
              <div style={{ display: "grid", gap: "12px" }}>
                {visibleRecent.map((item, index) => (
                  <div key={`${item.captured_at}-${index}`} style={{
                    padding: "14px 16px",
                    borderRadius: "18px",
                    background: "#f4f7f5",
                    border: "1px solid rgba(24,35,29,0.08)",
                  }}>
                    <div style={{ fontWeight: 600 }}>
                      {item.key}: {String(item.value)}
                    </div>
                    <div style={{ fontSize: "12px", marginTop: "6px", opacity: 0.72 }}>
                      {item.origin} • {item.source} • {item.captured_at}
                    </div>
                    {item.evidence ? (
                      <div style={{ marginTop: "10px", fontSize: "13px", opacity: 0.8 }}>
                        {item.evidence}
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

export default PreferencesBrowser;
