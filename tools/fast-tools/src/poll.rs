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
    unsafe { libc::kill(pid, 0) == 0 }
}

pub fn check_port_open(port: u16, timeout: Duration) -> bool {
    let addr: SocketAddr = match format!("127.0.0.1:{}", port).parse() {
        Ok(a) => a,
        Err(_) => return false,
    };
    TcpStream::connect_timeout(&addr, timeout).is_ok()
}

pub fn poll_service(config: &PollConfig) -> PollReport {
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
