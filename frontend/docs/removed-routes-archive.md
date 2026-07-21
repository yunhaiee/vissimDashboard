# Removed Routes Archive

Date: 2026-04-23
Scope: `frontend`

## What was removed

The following route directories were removed:

- `app/map`
- `app/simulate`

The top navigation in `components/AppShell.tsx` was updated to remove links to:

- `/map`
- `/simulate`

## Why this archive exists

The user requested cleanup now, but asked to keep a record so the pages can be restored later without re-discovery work.

## Related components kept in codebase

These components still exist and can be reused when restoring routes:

- `components/MapViewer.tsx`
- `components/IncidentPanel.tsx`
- `components/SimulationControl.tsx`

## Quick restore checklist

1. Recreate route files:
   - `app/map/page.tsx`
   - `app/simulate/page.tsx`
2. Wire each page to existing components (MapViewer / SimulationControl / IncidentPanel as needed).
3. Re-add top navigation links in `components/AppShell.tsx` for:
   - `/map`
   - `/simulate`
4. Verify route discovery by running build/test.

## Minimal page stubs (restore template)

```tsx
// app/map/page.tsx
export default function MapPage() {
  return <div>Map page restored.</div>;
}
```

```tsx
// app/simulate/page.tsx
export default function SimulatePage() {
  return <div>Simulation page restored.</div>;
}
```

## Notes

- This archive is documentation only (no runtime code).
- If route behavior needs to match a previous UI snapshot exactly, restore from VCS history if available.
