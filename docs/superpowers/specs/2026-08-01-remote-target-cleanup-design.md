# saage remote: target list / cleanup + ps progress — design

Date: 2026-08-01. Status: approved (brainstormed in-session).

## Problem

Targets in `credentials.toml` accumulate forever: `add-target` appends, nothing
removes. Auto-spawned boxes (`spawn`, sweep runs) get registered and never
unregistered — even `terminate` leaves the target behind. Result: a long list of
dead names that confuses every error message and makes `saage remote ps` crawl
(serial ssh probe per target, ConnectTimeout=10, no output until done — reads as
a hang).

## Decisions

- **No liveness-based automation.** Ping tells "reachable now", not "still
  yours"; offline it tells nothing; a recycled cloud IP can even answer for a
  stranger's box. Reachability is shown only on demand, informational only,
  and never mutates state.
- **User-driven cleanup**, not aging/timestamps (YAGNI — the user knows which
  boxes are dead).
- Removing a target only deletes ssh bookkeeping. It never terminates a box.
  The flip side is printed as a reminder: a removed Lambda target keeps billing.

## Changes

1. **`saage remote list`** — print registered targets from `credentials.toml`:
   name, user@host:port, hourly rate, per-target key marker. Pure local, no
   network.
2. **`creds.remove_target(name)`** — delete the `[targets.<name>]` section by
   text splice (the rest of the file stays byte-identical; TOML re-emit would
   lose comments). Preserves 0600. Never deletes key files (Thunder
   per-instance keys are unrecoverable). Error on unknown name.
3. **`saage remote cleanup`** — loop over targets, per target prompt
   `remove <name> (user@host)? [y/N]`, default N. Before the prompt, warn if a
   non-final-phase run in the local ledger references the target. Optional
   `--check`: ssh-probe each target first and show reachable/unreachable as
   info only. Ends with the "removing a target does not terminate the box"
   reminder if anything was removed.
4. **`saage remote terminate`** — after terminating the instance, also remove
   the matching target registration (stop creating tomorrow's clutter).
5. **`saage remote ps` progress** — print `checking <name> (user@host)… ` per
   target before the probe, then `ok (N sessions)` / `unreachable` on the same
   line, so dead targets read as progress instead of a hang.

## Placement

- `remove_target` in `saage/remote/creds.py` (owns the TOML file format).
- `cleanup` in `saage/remote/observe.py` (already imports creds + state;
  module is "observe and manage").
- CLI wiring in `saage/remote/cli.py`.

## Testing

Offline unit tests in `tests/remote/` using the `saage_home` fixture:
splice removal (middle/last section, comments preserved, 0600 kept, unknown
name error), cleanup prompt loop with monkeypatched `input` (y/n/default,
active-run warning), list output, ps progress lines with a stubbed
`SshTarget`, terminate unregisters target.
