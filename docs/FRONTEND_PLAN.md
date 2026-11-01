# Frontend Plan

## Goal

Build one browser-facing investigation screen that makes a failed call
understandable in under 30 seconds. It should lead with the causal verdict, then
let an engineer inspect the trace evidence and intervention comparison.

## First release

1. A React/Vite application in `frontend/` with no credentials in browser code.
2. A configurable API base URL, defaulting to local FastAPI at port 8000.
3. A “run demo investigation” action that submits the seeded STT failure and
   requests its diagnosis.
4. One dashboard showing call state, transcript mismatch, control/intervention
   pass rates, statistical verdict, and raw trace facts.
5. Responsive styling for a usable laptop demo.

## Follow-on releases

- Audio waveform/player linked to trace timestamps once the live adapter stores
  real files.
- Patch approval and three-set validation actions.
- Review queue, regression suite, red-team, and self-validation pages.
- Authentication and production API configuration.

## Verification

- Run FastAPI locally on port 8000.
- Run `npm run build` in `frontend/`.
- Use the dashboard action to create a run and show a proven diagnosis.
