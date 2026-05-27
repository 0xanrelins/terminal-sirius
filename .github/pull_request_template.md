## Summary

<!-- What changed and why -->

## Nautilus / API contract

- [ ] I did **not** change `/ws` event `type` values or required fields without updating:
  - `frontend/src/types.ts`
  - `backend/ws_contract.py`
  - `docs/ws-api-contract.md`
- [ ] Ran offline regression: `backend/scripts/run_smoke_tests.sh`
- [ ] If Polymarket/exec touched: checked `docs/nautilus-migration-smoke.md` after local restart

## Test plan

- [ ] `cd backend && ./scripts/run_smoke_tests.sh`
- [ ] Manual: UI widgets still receive expected WS types
