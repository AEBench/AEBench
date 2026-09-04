# `aeshell` — the command-monitoring shim

A transparent stand-in for `bash`. It announces each shell invocation to the
AEBench command broker, obeys the verdict, runs the real shell, and reports the
output and outcome once the command is over.

Output is *not* streamed to the broker. The agent reads it live from the shim's
own descriptors, which is the only place it is needed in real time; the broker
receives both streams in full just before the `end` message. Nothing is read
back after the verdict.

It contains **no policy**. The broker decides; this binary obeys. Everything
that can live on the server does, because the shim is the only component inside
the agent's reach.

## Build

```bash
cargo build --release --manifest-path src/runtime/shim/Cargo.toml
# -> src/runtime/shim/target/release/aeshell
```

## Configuration

Read from the environment once at startup:

| Variable | Meaning | Default |
|---|---|---|
| `AEBENCH_COMMAND_SOCKET` | Broker socket. Unreachable means run unmonitored. | `/run/aebench/command.sock` |
| `AEBENCH_REAL_SHELL` | The real shell to exec. | `/usr/lib/aebench/bash.real` |

The defaults exist so a stripped environment still finds a properly installed
broker, though a deliberate `env -i` sends the shim to the default socket rather
than the run's own.

The shim keeps no state and sets nothing in the child's environment. It reports
argv, cwd, its pid, and the *names* of its environment variables — nothing
else. Everything derivable from those is derived by the broker: the `-c`
command string from argv, nesting by walking `/proc` from the pid until it
reaches a process behind another live connection, and the duration from its own
timestamps.

## Behaviour contract

- **argv is passed through unmodified**, including `argv[0]`, so `-bash` still
  means a login shell and an `sh`-named invocation still selects POSIX mode.
- **stdin is inherited.** Heredocs, pipes, and interactive prompts work.
- **A stream that is a TTY is never piped**, so `isatty()` stays true for the
  child and colour, progress bars, and buffering behave normally. The broker
  records that stream as `skipped_tty`.
- **SIGINT, SIGTERM, SIGHUP, and SIGQUIT are forwarded** to the child, so an
  outer `timeout` still terminates the real work. Terminal-generated ones —
  Ctrl-C, Ctrl-\, a hangup — are *not* forwarded: the kernel delivers those to
  every process in the foreground group, so the child already has one, and a
  second copy reads as a second Ctrl-C to anything that escalates on one.
- **A signal ignored on entry stays ignored**, so `nohup` still works. No
  handler is installed over an inherited `SIG_IGN`, and the child inherits the
  ignore across `exec` just as it would under the real shell.
- **The exit status is reproduced exactly** — the child's code, or `128+signal`
  when it was killed.
- **Failure is always open.** A missing socket, a refused connection, or a
  broker speaking an unknown protocol version all result in the real shell
  running anyway. A run that loses evidence is detected later by coverage
  measurement; a run killed by a broker outage is a lost four-hour build.

## What it does not see

Bash parses `bash -c "a & b & c"` itself and `fork()`s copies of the running
process before `execve`ing each target, so no second `/bin/bash` is ever
executed. The shim observes **one record per shell invocation**, never the
sub-commands inside one. It *is* re-entered whenever a non-shell process execs a
shell — `make` recipe lines, `#!/bin/bash` shebangs, `subprocess(shell=True)` —
and the broker links those to their parent through the process tree.

Complete per-command coverage would require execve interception (eBPF,
seccomp-unotify, or ptrace) in the broker. That is deliberately out of scope.

## Installation

The shim has to sit where the agent will find it. Replacing `/bin/bash`
system-wide is not acceptable on a developer host, so for `runtime.mode =
"local"` prefer a mount namespace:

```bash
unshare --mount --map-root-user \
  sh -c 'mount --bind target/release/aeshell /bin/bash && exec "$@"' -- <agent cmd>
```

A `PATH`-prepended private `bin/bash` also works, but only if the harness
resolves `bash` through `PATH` rather than by absolute path. Run
`tools/probe_shell_invocations.py` to find out which applies to a given agent.
