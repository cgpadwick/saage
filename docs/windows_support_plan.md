# Native Windows Support for Local Runs

**Status:** implemented and verified on branch `windows-native` — see
"Implementation status" at the bottom.
**Date:** 2026-06-10
**Scope:** `saage run` on a native Windows box (no WSL). **Remote handoff is
explicitly out of scope** — the only remote-code changes here are the minimal
ones needed to keep its *offline unit tests* honest on Windows (§6). WSL2
remains a fully supported path; this adds native Windows alongside it.

---

## 1. Baseline: what actually breaks (measured)

Full suite on native Windows 11, Python 3.14, fresh `.venv`, Git for Windows
installed: **17 failed, 150 passed, 5 skipped, 13:00** (10 minutes of which was
a single hung test — see F2).

| # | Failure class | Symptom | Root cause |
|---|---|---|---|
| F1 | shell dialect | `test_guessing_game`: history line recorded as `'guess=0.5 -` | `subprocess.run(shell=True)` uses **cmd.exe** on Windows; the flow's `echo 'guess={{ guess }} -> {{ feedback }}' >> history.txt` had its `>` parsed as a *redirect* by cmd.exe, and single quotes aren't quotes in cmd. Flow commands are written in POSIX sh; cmd.exe is a different language. |
| F2 | venv layout | `test_venv_puts_its_bin_first_on_path` hung 10 min then `TimeoutExpired` | `venv_env()` only recognizes the POSIX `<venv>/bin/` layout (Windows venvs use `Scripts\`), so activation silently didn't happen and bare `python` launched the **system interactive REPL**, blocking on stdin. |
| F3 | venv layout | `test_venv_activated_when_present`: `$VIRTUAL_ENV` literal | F1 + F2 combined (cmd.exe doesn't expand `$VAR`; venv not detected). |
| F4 | POSIX file modes | 9 failures in `tests/remote/test_creds.py` / `test_r2.py`: loader refuses its **own freshly created** credentials file, "permissions are 0o666" | `chmod(0o600)` is a no-op on NTFS; `stat` always reports 0o666. The POSIX group/world-bits check is meaningless on Windows. |
| F5 | CRLF | 5 failures in `tests/remote/test_scripts.py`: `bash -n` → ``syntax error near unexpected token `{\r'`` | The test pipes the generated script to `bash -n` over **text-mode stdin** (`subprocess.run(..., input=script, text=True)`); on Windows the stdin `TextIOWrapper` translates `\n` → `\r\n`, and bash rejects CR. (The scripts themselves are generated with clean `\n`.) |

Everything else — hydration, primitives, skills, captures, the agent loop, git
tools, the scripted integration flows — **already passes on native Windows.**

F5 also exposes a latent *remote* bug worth recording (not fixing now): a
handoff initiated **from** a Windows laptop would push CRLF-corrupted scripts
to the Linux node (`subprocess` text-mode stdin and laptop-side `write_text()`
both translate newlines). Filed under "future work" (§7).

## 2. Design decisions

### D1 — Flow commands run under **Git Bash** on Windows, not cmd.exe

The `command:` step strings and the agent's `run_command` strings throughout
this repo (and AGENTS.md's documented conventions) are POSIX sh: single
quotes, `$VAR`, `&&`, `>>`, env-var prefixes (`STABLEWM_HOME="..." python ...`).
Making cmd.exe a second supported dialect would mean every flow is written
twice. Instead: **one command language, POSIX sh, on every OS.**

This is cheap because the engine already hard-requires git (the git tools, the
brownfield flows), and Git for Windows bundles `bash.exe`. Discovery order
(first hit wins, result cached):

1. `SAAGE_SHELL` env var — explicit override; the escape hatch. Set it to a
   bash path, or to `cmd` to force the old `shell=True` behavior.
2. `bash.exe` located **relative to `git.exe`** (`<git root>/bin/bash.exe` or
   `<git root>/usr/bin/bash.exe`) — the reliable route.
3. Conventional install dirs (`%ProgramFiles%\Git\bin`, …).
4. `shutil.which("bash")` **excluding `System32`** — `C:\Windows\System32\bash.exe`
   is the WSL launcher; silently running flow commands inside WSL is exactly
   what "native Windows support" must not do.
5. Nothing found → **hard error** naming the fix ("install Git for Windows or
   set SAAGE_SHELL"). A silent cmd.exe fallback would run flows in the wrong
   language and fail in confusing, data-dependent ways (F1 wrote a stray file
   named `higher'` and *exited 0*).

Execution: `subprocess.run([bash, "-c", command], cwd=..., env=...)` on
Windows; unchanged `shell=True` (→ `/bin/sh`) on POSIX. One helper owns this
(`saage/shell.py`) and both call sites (`CommandNode.exec`,
`tools.run_command`) use it.

### D2 — `venv_env()` understands both venv layouts

Activation = prepend the venv's executables dir to `PATH` + set `VIRTUAL_ENV`.
Detect `bin/` (POSIX) **or** `Scripts/` (Windows), whichever exists. The
existence gate (venv-creating step runs with system python; later steps use
the venv) is unchanged.

### D3 — UTF-8 everywhere, explicitly

Python on Windows still defaults to the legacy locale codepage (cp1252) for
file IO and subprocess text decoding (PEP 686 lands in 3.15). Skills and flow
files are UTF-8 markdown/YAML (this repo's own files contain `—`, `→`, `✓`).
So:

- every engine `read_text/write_text/open` gets `encoding="utf-8"`;
- command output capture gets `encoding="utf-8", errors="replace"` (a flow
  command emitting odd bytes must degrade to `�`, never crash the engine);
- `cli.py` reconfigures `sys.stdout/stderr` with `errors="replace"` on
  Windows so the engine's own log glyphs (`▶ ✓ ⚙ ↻`) can't raise
  `UnicodeEncodeError` when output is redirected to a cp1252 stream.

### D4 — `{{ python }}`: the interpreter name is a seeded shared value

Flows invoke helper scripts as `python3 "{{ flow_dir }}/x.py"`. There is no
`python3.exe` on Windows (Git Bash included; the WindowsApps `python3` alias
is a Store stub). The engine now seeds the shared store with
`python` = `"python"` on Windows / `"python3"` elsewhere (overridable in
`shared:` or via `--set`, like `workspace`/`flow_dir`/`venv`), and the bundled
flows say `{{ python }}` instead of hardcoding `python3`. Inside an activated
venv `python` always resolves correctly on both platforms.

### D5 — Windows-destructive commands join the `run_command` denylist

The policy's danger patterns are POSIX-flavored (`rm -rf`, `mkfs`, `sudo`).
Even with bash as the shell, an agent can reach cmd.exe/PowerShell
(`cmd /c …`, `powershell -c …`), so add conservative Windows equivalents:
`rd|rmdir /s`, `del /s`, `format X:`, `Remove-Item -Recurse -Force`,
`reg delete`, `vssadmin … delete`, `diskpart`, `cipher /w`, `bcdedit`.
Same philosophy as the existing list: defense in depth, not a sandbox; avoid
false positives on ordinary work. The patterns are added **unconditionally**
(not platform-gated): they never match ordinary POSIX work, PowerShell exists
on Linux too, and an unconditional list keeps the policy tests
platform-independent.

### D6 — The credentials 0600 check becomes POSIX-only

`mode & 0o077` is meaningless on NTFS (F4): the loader currently refuses a
file *it just created itself*. On Windows, skip the mode check (a per-user
`%USERPROFILE%` file is ACL-protected from other users by default — the moral
equivalent). This is a 2-line guard in `remote/creds.py`, needed because the
*offline* remote unit tests run in the default suite; it does not touch
handoff behavior.

### D7 — Generated bash scripts must never be CRLF-translated

Two distinct touch points: the laptop-side debug copies of
`bootstrap.sh`/`start.sh`/`stop.sh` in `handoff.py` get
`write_text(..., newline="\n")`; the `bash -n` validation tests in
`tests/remote/test_scripts.py` switch to **binary stdin**
(`input=script.encode()`, no `text=True`) since `subprocess.run` has no
`newline=` parameter. Those tests also resolve bash via the new
`saage.shell.find_bash()` (bare `["bash", ...]` can hit the System32 WSL
launcher on Windows) and skip when no bash exists. The ssh-stdin push path
has the same latent translation bug — remote scope (§7).

## 3. What deliberately does NOT change

- **POSIX behavior.** Every change is layout-detection, an explicit encoding,
  or a Windows-only branch. The Linux/macOS/WSL2 *execution* path is unchanged
  (`shell=True` → `/bin/sh`, `bin/` venvs). Two deliberate cross-platform
  deltas: D5's extra deny patterns (never match ordinary POSIX work), and
  D3's strict→`errors=replace` UTF-8 decoding of command/git output — a
  command emitting invalid UTF-8 used to crash the engine with
  `UnicodeDecodeError` on Linux too; now it degrades to `�` everywhere.
- **The engine's mental model.** No new step types, no schema changes, no new
  CLI flags. `{{ python }}` is just another auto-seeded shared value.
- **Remote handoff.** No functional changes beyond D6/D7's test honesty.
- **Flow authors' contract.** Commands are POSIX sh, period. AGENTS.md gains
  a short "Windows" note: use `{{ python }}`; Git for Windows required; and
  avoid POSIX-absolute paths like `/tmp` in commands — under Git Bash they
  resolve inside the MSYS root, not `C:\tmp`. Prefer `{{ workspace }}`-relative
  paths, which mean the same thing on every OS.

## 4. Implementation map

| File | Change |
|---|---|
| `saage/shell.py` (new) | `find_bash()` discovery (D1, cached) + `run_shell(command, cwd, env, timeout)` — the one place that knows how to run a flow command string on each OS |
| `saage/nodes.py` | `CommandNode.exec` → `run_shell` |
| `saage/tools.py` | `run_command` → `run_shell`; `venv_env` both layouts (D2); UTF-8 file IO (D3); `_git` subprocess gets `encoding="utf-8", errors="replace"` (locale-codepage decode of UTF-8 diffs raises `UnicodeDecodeError` otherwise) |
| `saage/skills.py`, `saage/hydrate.py`, `saage/config.py` | `read_text(encoding="utf-8")` |
| `saage/cli.py` | stream `errors="replace"` reconfigure on Windows (D3) |
| `saage/hydrate.py` | seed `python` (D4) |
| `saage/config.py` | Windows deny patterns (D5) |
| `saage/remote/creds.py` | POSIX-only mode check (D6) |
| `saage/remote/handoff.py` | `newline="\n"` on script copies (D7) |
| `flows/greenfield_ml/`, `flows/lewm_hillclimb/` | `python3` → `{{ python }}`; `setup_env.py` venv-layout awareness (`bin`/`Scripts`) so the ml-frameworks GPU stack installs on Windows too |
| `tests/` | new `test_shell.py` (discovery + dialect + argv round-trip quoting of embedded `"` and trailing `\` — `list2cmdline` rewrites those edge cases); venv `Scripts` layout cases in `test_workspace.py`; Windows policy cases; binary-stdin + `find_bash` in `tests/remote/test_scripts.py` (D7); `skipif(win32)` on **two** creds tests — the chmod-mode assertion *and* `test_refuses_world_readable_creds` (it only passes on Windows today because the check wrongly fires on every file; D6 removes that) |
| `README.md`, `AGENTS.md` | Windows requirements note + `{{ python }}` convention |

## 5. Testing strategy

1. **Full offline suite green on native Windows** — including the previously
   failing 17, minus justified skips (the literal POSIX-chmod assertion).
2. **POSIX deltas are deliberate and bounded**: Windows branches are
   unreachable on POSIX; the file-IO encoding args are behavior-preserving
   there (all the repo's files are ASCII/UTF-8); the subprocess-decode change
   (strict → `errors=replace`) applies everywhere by design (§3). CI on Linux
   re-verifies on push.
3. **CLI end-to-end on Windows**: `saage run` of a command-only smoke flow
   (no API key needed) exercising POSIX quoting, `$VAR`, `>>`, venv
   activation, `{{ python }}`, and the run summary — proving the *installed
   entry point*, not just pytest.
4. **Live agent flow** if `OPENROUTER_API_KEY`/`ANTHROPIC_API_KEY` is present
   in the environment; otherwise the scripted integration flows
   (`guessing_game` exercises exactly the F1 failure mode) stand in.

## 6. Self-review (adversarial pass)

- **"Why not make flows cmd.exe-portable instead?"** Two dialects = every flow
  author's problem forever; one dialect = one engine module. The repo's own
  flows already chose sh. Rejected.
- **"Does bash -c change quoting for POSIX?"** POSIX path is untouched
  (`shell=True` as today).
- **"`{{ flow_dir }}` renders `C:\Users\...` — does bash eat the
  backslashes?"** Inside the double quotes the flows already use
  (`"{{ flow_dir }}/x.py"`), bash preserves backslashes except before
  ``$ ` " \ ``; drive paths like `C:\Users\cpadw` survive, and Windows Python
  accepts mixed separators. Unquoted occurrences would break — the bundled
  flows all quote. Documented convention, already followed.
- **"PATH with Windows entries inside Git Bash?"** Git Bash translates
  `PATH` between Windows and POSIX forms automatically; prepending
  `<venv>\Scripts` via the env dict works (validated by the venv tests).
- **"timeout: does killing bash kill its children on Windows?"** Not
  reliably (no process groups by default). Same class of risk existed with
  cmd.exe. Accepted as a known limitation, noted in shell.py; revisit with
  Job Objects if it bites.
- **"Will `find_bash` pick up WSL bash?"** Explicitly excluded (System32
  filter) and the git-relative probe runs first.
- **"Is auto-seeding `python` a breaking change?"** `seed.setdefault` —
  a flow that defines its own `python` key wins, same as `workspace`/`venv`.
- **"errors='replace' hides real encoding bugs?"** For *display* streams and
  *model-facing command output*, garbled-but-alive beats a crashed 20-hour
  run. Engine-owned file IO stays strict UTF-8 (no `errors` arg).
- **"Spinner ANSI codes on conhost?"** Windows Terminal (the default since
  Win11) supports VT; legacy conhost may show a stray escape on an
  interactive run only. Cosmetic; not worth a colorama dependency.

## 7. Future work (explicitly deferred)

- **Remote handoff *from* a Windows laptop** — ~~deferred~~ **implemented**
  (branch `remote-from-windows`, 2026-06-11): ssh stdin is binary always
  (the F5 sibling), transfers fall back to in-Python tar-over-ssh when rsync
  is missing, foreign key paths in a copied credentials.toml resolve to
  `~/.saage/ssh/`, and `add-target` writes key paths as TOML literal strings
  (backslash escapes). Verified live: greenfield_ml handed off from native
  Windows to a Lambda A10 and a Thunder Compute A6000.
- **Windows as a *target* node** (saage running flows pushed to a Windows
  GPU box): tmux/bash scripts are Linux-shaped; out of scope.
- Process-tree kill on timeout via Job Objects.
- `saage doctor`-style preflight that reports bash/git/python discovery.

## 8. Implementation status (2026-06-10)

Implemented on `windows-native` per the map in §4 and verified on both
platforms:

- **native Windows 11 / Python 3.14**: full suite **192 passed, 7 skipped**
  (baseline: 17 failed, and 13:00 wall-clock vs 0:12 now — the hung-REPL F2
  failure was most of the old runtime), plus a command-only smoke flow run
  through the installed `saage` CLI exercising venv auto-activation
  (`Scripts` layout), POSIX quoting/redirects, `{{ python }}` helper
  invocation, UTF-8 round-trip, and a `counting_loop`+`exit_when`.
- **Linux (WSL2 Ubuntu, Python 3.12) as the POSIX regression check**: full
  suite 192 passed, 7 skipped.

One delta beyond the §4 map: `flows/poll_job/flow.yaml` also moved to
`{{ python }}` — the Linux verification surfaced that its bare `python` was
already broken on any distro without the `python-is-python3` alias, the same
problem D4 solves. (The 7 skips = 5 pre-existing env-gated ssh/live tests +
the two POSIX-file-mode tests from §4.)
