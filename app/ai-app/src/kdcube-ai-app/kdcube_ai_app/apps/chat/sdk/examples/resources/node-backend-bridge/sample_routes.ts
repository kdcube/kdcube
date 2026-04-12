export function registerSampleRoutes(app, { projectRoot = '' } = {}) {
  app.get('/api/projects/status', (_req, res) => {
    res.json({
      ok: true,
      runtime: 'node-bridge',
      project_root: projectRoot || null,
    })
  })

  app.post('/api/projects/search', (req, res) => {
    const query = String(req.body?.query || '').trim()
    const items = query
      ? [
          {
            id: 'sample-1',
            title: `Match for ${query}`,
            source: 'sample-node-bridge',
          },
        ]
      : []

    res.json({
      ok: true,
      items,
      total: items.length,
    })
  })
}
