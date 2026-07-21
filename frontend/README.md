# VISSIM frontend

This app is the Next.js frontend for the VISSIM scenario-comparison dashboard in `platform/`.

## Main surfaces
- `app/page.tsx` ? primary dashboard landing page
- `components/CorridorSectionComparison.tsx` ? corridor spread comparison
- `components/NodeDirectionTable.tsx` ? node/intersection bottleneck drilldown
- `lib/api.ts` ? frontend API bindings for `/api/scenarios`, `/compare`, and `/compare-summary`

## Local development
From `platform/frontend`:

```bash
npm run dev
```

The default local app URL is [http://localhost:3000](http://localhost:3000).

## Verification commands
Run these after frontend changes:

```bash
npm run lint
npm run test:dashboard-contract
npx tsc --noEmit
npm run build
```

## Recommended full-stack smoke flow
1. Start the backend from `platform/backend`
2. Start the frontend from `platform/frontend`
3. Open `/`
4. Select two or more scenarios
5. Confirm the landing page reads in this order:
   - scenario selection
   - readiness/status
   - compact overview summary
   - corridor comparison
   - node-direction drilldown toggle
6. Confirm the node-direction workspace stays hidden until the explicit expand button is pressed.

## Related project docs
- `../docs/intersection-dashboard-information-architecture.md`
- `../docs/vissim-dashboard-redesign-review.md`
- `../docs/vissim-intersection-dashboard-runbook.md`
- `../docs/vissim-intersection-dashboard-integration-verification.md`

## Notes
- This frontend uses Next.js 16 app-router structure.
- ESLint uses the Next.js 16 flat-config entrypoints from `eslint-config-next/core-web-vitals` and `eslint-config-next/typescript`.
- The landing page now uses one linear summary-first flow instead of switching between overview/intersection/corridor tabs.
- The node-direction workspace remains collapsed by default to preserve progressive disclosure.
- When changing dashboard UX, keep docs and verification notes updated in the matching `docs/` files.
