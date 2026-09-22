use serde::{Deserialize, Serialize};
use std::net::{SocketAddr, TcpStream};
use std::time::{Duration, Instant};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TargetState {
    Alive,
    Terminated,
    Ready,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PollConfig {
    pub pid: Option<i32>,
    pub port: Option<u16>,
    pub state: TargetState,
    pub timeout_ms: u64,
    pub interval_ms: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PollReport {
    pub success: bool,
    pub elapsed_ms: u64,
    pub message: String,
}

pub fn check_pid_alive(pid: i32) -> bool {
    let ret = unsafe { libc::kill(pid, 0) };
    if ret == 0 {
        #[cfg(target_os = "linux")]
        {
            if let Ok(stat) = std::fs::read_to_string(format!("/proc/{}/stat", pid)) {
                if let Some(idx) = stat.rfind(')') {
                    let rest = stat[idx + 1..].trim_start();
                    if let Some(state_char) = rest.chars().next() {
                        if state_char == 'Z' || state_char == 'X' {
                            return false;
                        }
                    }
                }
            }
        }
        true
    } else {
        let err = std::io::Error::last_os_error();
        matches!(err.raw_os_error(), Some(libc::EPERM))
    }
}

pub fn check_port_open(port: u16, timeout: Duration) -> bool {
    let addr: SocketAddr = match format!("127.0.0.1:{}", port).parse() {
        Ok(a) => a,
        Err(_) => return false,
    };
    TcpStream::connect_timeout(&addr, timeout).is_ok()
}

pub fn poll_service(config: &PollConfig) -> PollReport {
    if config.pid.is_none() && config.port.is_none() {
        return PollReport {
            success: false,
            elapsed_ms: 0,
            message: "Neither pid nor port was specified for polling.".to_string(),
        };
    }

    let start = Instant::now();
    let timeout = Duration::from_millis(config.timeout_ms);
    let interval = Duration::from_millis(config.interval_ms.max(10));

    loop {
        let mut condition_met = true;

        if let Some(pid) = config.pid {
            let is_alive = check_pid_alive(pid);
            match config.state {
                TargetState::Alive => {
                    if !is_alive {
                        condition_met = false;
                    }
                }
                TargetState::Terminated => {
                    if is_alive {
                        condition_met = false;
                    }
                }
                TargetState::Ready => {
                    if !is_alive {
                        condition_met = false;
                    }
                }
            }
        }

        if let Some(port) = config.port {
            let is_open = check_port_open(port, Duration::from_millis(100));
            match config.state {
                TargetState::Ready | TargetState::Alive => {
                    if !is_open {
                        condition_met = false;
                    }
                }
                TargetState::Terminated => {
                    if is_open {
                        condition_met = false;
                    }
                }
            }
        }

        if condition_met {
            return PollReport {
                success: true,
                elapsed_ms: start.elapsed().as_millis() as u64,
                message: format!("Target condition {:?} met in {} ms", config.state, start.elapsed().as_millis()),
            };
        }

        if start.elapsed() >= timeout {
            return PollReport {
                success: false,
                elapsed_ms: start.elapsed().as_millis() as u64,
                message: format!("Timeout after {} ms waiting for condition {:?}", start.elapsed().as_millis(), config.state),
            };
        }

        std::thread::sleep(interval);
    }
}
