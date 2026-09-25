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

`aebench case monitor` installs the shim; nothing here needs doing by hand. The agent image carries the binary dormant at
`/usr/lib/aebench/aeshell`, and the run swaps it over `/bin/bash` inside the
container once the container is up, keeping the real shell at
`/usr/lib/aebench/bash.real`. `aebench case run` leaves the image untouched.

The shim is not swapped back. With no socket mounted -- the situation in the
container the oracle starts from the committed snapshot -- it execs the
preserved shell and is indistinguishable from it.

The swap is deliberately not done at image build time: the same image serves
both commands, so a monitored run stays comparable with an unmonitored one.

Building the binary on the host and mounting it *into the container* does not
work. It links against the host's glibc, the runtime image is Debian bookworm,
and a shell that cannot exec takes the whole container with it -- so the binary
is built in a stage of the agent image instead. A host build is still the right
thing for the local recipe below, which never leaves the host.

Monitoring is Docker-only. Replacing `/bin/bash` on a developer host is not
acceptable, and `runtime.mode = "local"` is rejected rather than silently run
unmonitored. Monitoring a local run would need a mount namespace:

```bash
unshare --mount --map-root-user \
  sh -c 'mount --bind target/release/aeshell /bin/bash && exec "$@"' -- <agent cmd>
```

## Broker

The server side is `src/runtime/shim/src/monitor.py`, supervised by
`src/runtime/monitoring.py`, which runs it as a child process on a per-run
socket and writes the trace outside the agent's reach. See
`docs/architecture/runtime.md`.
