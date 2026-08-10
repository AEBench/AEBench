//! AEBench command-monitoring shim.
//!
//! Installed in place of `bash`. It must be indistinguishable from the real
//! shell to everything above and below it: argv, stdin, the controlling
//! terminal, signals, and the exit status all pass through untouched. Its only
//! added behaviour is to announce the invocation to the broker, obey the
//! verdict, and report the output and outcome once the command is over.
//!
//! Output is forwarded to the agent live but sent to the broker only at the
//! end: the agent is what needs it in real time, the broker does not. Nothing
//! is read back after the verdict, and the shim keeps no state of its own —
//! the broker derives nesting from the process tree.
//!
//! It contains no policy. The broker decides; this binary obeys.
//!
//! Failure is always open: if the socket is missing, unreachable, or speaks
//! nonsense, the real shell runs anyway. A run that loses evidence is detected
//! later by coverage measurement; a run killed by a broker outage is a lost
//! four-hour build.
//!
//! # Protocol
//!
//! One command is one connection, and one round trip. Every message is a
//! length-prefixed frame:
//!
//! ```text
//! u32 payload_length (big endian) | u8 frame_kind | payload
//! ```
//!
//! `frame_kind` is 0 for a control frame, whose payload is a UTF-8 JSON object
//! with a `type` discriminator, and 1 or 2 for raw stdout or stderr bytes.
//! Output frames carry bytes verbatim, so binary output needs no escaping and a
//! stream larger than one frame is simply sent as several.
//!
//! ```text
//! shim -> begin      control   what is about to run
//!      <- decision   control   the command's id, and whether it may run
//! shim -> stdout     data      the child's stdout, after it has exited
//! shim -> stderr     data      the child's stderr, after it has exited
//! shim -> end        control   how the child finished
//! ```
//!
//! Nothing comes back after the decision. The connection identifies the
//! command, so no message needs to name it, and bytes already written reach the
//! broker whether or not this process has exited by the time it reads them.
//!
//! ## `begin` — shim to broker
//!
//! Only what this process alone can observe. The broker works out the rest: the
//! `-c` command string from argv, the parent command by walking `/proc` from
//! `pid`, and the duration from its own clock.
//!
//! | field      | type       | meaning                                            |
//! |------------|------------|----------------------------------------------------|
//! | `type`     | `"begin"`  | discriminator                                      |
//! | `argv`     | `[string]` | this shell's argv, `argv[0]` included, unmodified  |
//! | `cwd`      | `string`   | working directory; empty if it cannot be read      |
//! | `pid`      | `int`      | this process's pid, the root of the `/proc` walk   |
//! | `env_keys` | `[string]` | environment variable **names** only, never values  |
//!
//! Values are withheld deliberately: the agent's environment holds the API
//! keys, and the trace is written to disk.
//!
//! ## `decision` — broker to shim
//!
//! | field        | type          | meaning                                       |
//! |--------------|---------------|-----------------------------------------------|
//! | `type`       | `"decision"`  | discriminator                                 |
//! | `command_id` | `string`      | the broker's id for this command              |
//! | `allow`      | `bool`        | `false` means the command must not run        |
//! | `reason`     | `string`      | shown to the agent on stderr when denied      |
//! | `exit_code`  | `int`         | what to exit with when denied, normally 126   |
//!
//! `command_id` is for the broker's own records; the shim does not use it.
//!
//! ## `end` — shim to broker
//!
//! | field       | type          | meaning                                        |
//! |-------------|---------------|------------------------------------------------|
//! | `type`      | `"end"`       | discriminator                                  |
//! | `exit_code` | `int \| null` | the child's exit code, `null` if it was killed  |
//! | `signal`    | `int \| null` | the signal that killed it, `null` otherwise     |
//!
//! Byte counts and a duration are absent on purpose: nothing is dropped, so the
//! broker counts what it read and times the command itself.

use std::env;
use std::ffi::{OsStr, OsString};
use std::io::{self, Read, Write};
use std::os::unix::net::UnixStream;
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicI32, Ordering};
use std::thread;

const MAX_FRAME_BYTES: usize = 16 * 1024 * 1024;
const READ_BYTES: usize = 64 * 1024;

const ENV_SOCKET: &str = "AEBENCH_COMMAND_SOCKET";
const ENV_REAL_SHELL: &str = "AEBENCH_REAL_SHELL";

const DEFAULT_SOCKET: &str = "/run/aebench/command.sock";
const DEFAULT_REAL_SHELL: &str = "/usr/lib/aebench/bash.real";

/// Set once the child exists so the signal handler can forward to it.
static CHILD_PID: AtomicI32 = AtomicI32::new(0);

fn main() {
    let argv: Vec<OsString> = env::args_os().collect();
    if argv.is_empty() {
        eprintln!("aeshell: empty argv");
        std::process::exit(127);
    }

    let real_shell = env::var_os(ENV_REAL_SHELL).unwrap_or_else(|| DEFAULT_REAL_SHELL.into());
    let socket_path = env::var_os(ENV_SOCKET).unwrap_or_else(|| DEFAULT_SOCKET.into());

    let Ok(stream) = UnixStream::connect(&socket_path) else {
        exec_real(&real_shell, &argv);
    };

    match negotiate(stream, &argv) {
        Ok(Session::Denied { reason, exit_code }) => {
            let _ = writeln!(io::stderr(), "aeshell: {reason}");
            std::process::exit(exit_code);
        }
        Ok(Session::Monitored { stream }) => {
            std::process::exit(run_monitored(stream, &real_shell, &argv));
        }
        Err(_) => exec_real(&real_shell, &argv),
    }
}

enum Session {
    Denied { reason: String, exit_code: i32 },
    Monitored { stream: UnixStream },
}

/// The body of the `begin` message. See the protocol section above for the
/// meaning of each field.
struct CommandInfo {
    argv: Vec<String>,
    cwd: String,
    pid: i64,
    env_keys: Vec<String>,
}

impl CommandInfo {
    /// Describes a given command into a CommandInfo.
    ///
    /// Environment *values* are never collected, only the names, so credentials
    /// in the agent's environment stay out of the trace.
    fn describe(argv: &[OsString]) -> Self {
        Self {
            argv: argv
                .iter()
                .map(|arg| arg.to_string_lossy().into_owned())
                .collect(),
            cwd: env::current_dir()
                .map(|path| path.to_string_lossy().into_owned())
                .unwrap_or_default(),
            pid: std::process::id() as i64,
            env_keys: env::vars_os()
                .map(|(key, _)| {
                    let value: &OsStr = &key;
                    value.to_string_lossy().into_owned()
                })
                .collect(),
        }
    }

    /// Renders the JSON body. The wire calls this message `begin`; the type is
    /// named for what it carries.
    fn to_json(&self) -> String {
        serde_json::json!({
            "type": "begin",
            "argv": self.argv,
            "cwd": self.cwd,
            "pid": self.pid,
            "env_keys": self.env_keys,
        })
        .to_string()
    }
}

/// Negotiates the command with the server.
fn negotiate(mut stream: UnixStream, argv: &[OsString]) -> io::Result<Session> {
    let cmd_info = CommandInfo::describe(argv);
    stream.write_all(&encode_frame(
        FrameKind::CommandInfo,
        cmd_info.to_json().as_bytes(),
    ))?;

    let Some((kind, payload)) = read_frame(&mut stream)? else {
        return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "no decision"));
    };
    if kind != FrameKind::CommandInfo {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "expected a control frame",
        ));
    }

    let decision: serde_json::Value = serde_json::from_slice(&payload)
        .map_err(|err| io::Error::new(io::ErrorKind::InvalidData, err))?;
    if decision["type"] != "decision" {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "unexpected decision message",
        ));
    }

    if !decision["allow"].as_bool().unwrap_or(true) {
        return Ok(Session::Denied {
            reason: decision["reason"].as_str().unwrap_or_default().to_string(),
            exit_code: decision["exit_code"].as_i64().unwrap_or(126) as i32,
        });
    }

    Ok(Session::Monitored { stream })
}

/// Runs the real shell, then reports its output and outcome.
fn run_monitored(mut stream: UnixStream, real_shell: &OsStr, argv: &[OsString]) -> i32 {
    let stdout_tty = is_tty(libc::STDOUT_FILENO);
    let stderr_tty = is_tty(libc::STDERR_FILENO);

    let mut command = Command::new(real_shell);
    command
        .arg0(&argv[0])
        .args(&argv[1..])
        .stdin(Stdio::inherit())
        // A pipe would flip isatty() for the child and change how programs
        // buffer and color their output, so a terminal is left attached and
        // simply goes uncaptured.
        .stdout(if stdout_tty {
            Stdio::inherit()
        } else {
            Stdio::piped()
        })
        .stderr(if stderr_tty {
            Stdio::inherit()
        } else {
            Stdio::piped()
        });

    let mut child: Child = match command.spawn() {
        Ok(child) => child,
        Err(err) => {
            let _ = writeln!(
                io::stderr(),
                "aeshell: cannot spawn {}: {err}",
                real_shell.to_string_lossy()
            );
            return 127;
        }
    };

    CHILD_PID.store(child.id() as i32, Ordering::SeqCst);
    install_signal_forwarders();

    // Both pipes must be drained concurrently: a child that fills one while we
    // read the other would block forever.
    let stdout_pump = child
        .stdout
        .take()
        .map(|pipe| spawn_pump(pipe, FrameKind::Stdout));
    let stderr_pump = child
        .stderr
        .take()
        .map(|pipe| spawn_pump(pipe, FrameKind::Stderr));

    let status = child.wait();
    let stdout = stdout_pump
        .and_then(|pump| pump.join().ok())
        .unwrap_or_default();
    let stderr = stderr_pump
        .and_then(|pump| pump.join().ok())
        .unwrap_or_default();

    let (exit_code, signal) = match &status {
        Ok(status) => (status.code(), status.signal()),
        Err(_) => (None, None),
    };
    let end = serde_json::json!({
        "type": "end",
        "exit_code": exit_code,
        "signal": signal,
    });

    // The broker only needs the output now that the command is over, and
    // nothing comes back: bytes already written reach it whether or not this
    // process has exited by the time it reads them. A broker that has gone
    // away costs the record, never the command.
    let _ = send_output(&mut stream, FrameKind::Stdout, &stdout);
    let _ = send_output(&mut stream, FrameKind::Stderr, &stderr);
    let _ = stream.write_all(&encode_frame(
        FrameKind::CommandInfo,
        end.to_string().as_bytes(),
    ));

    match (exit_code, signal) {
        (Some(code), _) => code,
        (None, Some(signal)) => 128 + signal,
        _ => 127,
    }
}

/// Forwards one pipe to the inherited descriptor and accumulates a copy.
///
/// This is required to make sure that the agent sees the output live.
fn spawn_pump<R: Read + Send + 'static>(
    mut source: R,
    kind: FrameKind,
) -> thread::JoinHandle<Vec<u8>> {
    thread::spawn(move || {
        let mut collected = Vec::new();
        let mut buffer = vec![0_u8; READ_BYTES];
        loop {
            let read = match source.read(&mut buffer) {
                Ok(0) => return collected,
                Ok(read) => read,
                Err(ref err) if err.kind() == io::ErrorKind::Interrupted => continue,
                Err(_) => return collected,
            };
            let chunk = &buffer[..read];

            if kind == FrameKind::Stdout {
                let mut out = io::stdout().lock();
                let _ = out.write_all(chunk);
                let _ = out.flush();
            } else {
                let mut err = io::stderr().lock();
                let _ = err.write_all(chunk);
                let _ = err.flush();
            }
            collected.extend_from_slice(chunk);
        }
    })
}

/// Sends a whole captured stream, split to respect the frame ceiling.
fn send_output(stream: &mut UnixStream, kind: FrameKind, payload: &[u8]) -> io::Result<()> {
    for chunk in payload.chunks(MAX_FRAME_BYTES) {
        stream.write_all(&encode_frame(kind, chunk))?;
    }
    Ok(())
}

#[derive(Debug, PartialEq, Eq, Copy, Clone)]
enum FrameKind {
    CommandInfo,
    Stdout,
    Stderr,
    Invalid,
}

impl FrameKind {
    fn from_u8(value: u8) -> Self {
        match value {
            0 => FrameKind::CommandInfo,
            1 => FrameKind::Stdout,
            2 => FrameKind::Stderr,
            _ => FrameKind::Invalid,
        }
    }
    fn to_u8(self) -> u8 {
        match self {
            FrameKind::CommandInfo => 0,
            FrameKind::Stdout => 1,
            FrameKind::Stderr => 2,
            FrameKind::Invalid => 255,
        }
    }
}

fn encode_frame(kind: FrameKind, payload: &[u8]) -> Vec<u8> {
    let mut frame = Vec::with_capacity(5 + payload.len());
    frame.extend_from_slice(&(payload.len() as u32).to_be_bytes());
    frame.push(kind.to_u8());
    frame.extend_from_slice(payload);
    frame
}

fn read_frame(stream: &mut UnixStream) -> io::Result<Option<(FrameKind, Vec<u8>)>> {
    let mut header = [0_u8; 5];
    if !read_exact_or_eof(stream, &mut header)? {
        return Ok(None);
    }

    let length = u32::from_be_bytes([header[0], header[1], header[2], header[3]]) as usize;
    if length > MAX_FRAME_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "frame too large",
        ));
    }

    let mut payload = vec![0_u8; length];
    if length > 0 && !read_exact_or_eof(stream, &mut payload)? {
        return Err(io::Error::new(
            io::ErrorKind::UnexpectedEof,
            "stream ended mid-frame",
        ));
    }
    Ok(Some((FrameKind::from_u8(header[4]), payload)))
}

/// Fills `buffer`. Returns `false` only for a clean end of stream before any
/// byte of it arrived; a stream that ends part-way through is an error.
fn read_exact_or_eof(stream: &mut UnixStream, buffer: &mut [u8]) -> io::Result<bool> {
    let mut filled = 0;
    while filled < buffer.len() {
        match stream.read(&mut buffer[filled..]) {
            Ok(0) if filled == 0 => return Ok(false),
            Ok(0) => {
                return Err(io::Error::new(
                    io::ErrorKind::UnexpectedEof,
                    "stream ended mid-frame",
                ))
            }
            Ok(read) => filled += read,
            Err(ref err) if err.kind() == io::ErrorKind::Interrupted => continue,
            Err(err) => return Err(err),
        }
    }
    Ok(true)
}

/// Replaces this process with the real shell. Never returns on success.
fn exec_real(real_shell: &OsStr, argv: &[OsString]) -> ! {
    let error = Command::new(real_shell)
        .arg0(&argv[0])
        .args(&argv[1..])
        .exec();
    let _ = writeln!(
        io::stderr(),
        "aeshell: cannot exec {}: {error}",
        real_shell.to_string_lossy()
    );
    std::process::exit(127);
}

extern "C" fn forward_signal(signal: libc::c_int) {
    let pid = CHILD_PID.load(Ordering::SeqCst);
    if pid > 0 {
        // kill() is async-signal-safe, so this is legal inside a handler.
        unsafe { libc::kill(pid, signal) };
    }
}

fn install_signal_forwarders() {
    for signal in [libc::SIGINT, libc::SIGTERM, libc::SIGHUP, libc::SIGQUIT] {
        unsafe { libc::signal(signal, forward_signal as *const () as libc::sighandler_t) };
    }
}

fn is_tty(fd: libc::c_int) -> bool {
    unsafe { libc::isatty(fd) == 1 }
}
