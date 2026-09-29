use std::collections::HashMap;
use std::sync::mpsc::{channel, Receiver, Sender};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::json;
use windows::core::{BSTR, Interface, Result as WinResult};
use windows::Win32::Foundation::HWND;
use windows::Win32::System::Com::{CoCreateInstance, CoInitializeEx, CoUninitialize, CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED};
use windows::Win32::UI::Accessibility::{
    CUIAutomation, IUIAutomation, IUIAutomationElement, IUIAutomationInvokePattern,
    IUIAutomationValuePattern, UIA_InvokePatternId, UIA_ValuePatternId,
    UIA_ButtonControlTypeId, UIA_EditControlTypeId, UIA_TextControlTypeId,
};

use crate::protocol::ProtocolError;
use crate::win_utils::{self, RectBounds};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UiaElementInfo {
    pub token: String,
    pub name: String,
    pub value: String,
    pub automation_id: String,
    pub control_type: String,
    pub bounds: RectBounds,
    pub is_enabled: bool,
    pub is_offscreen: bool,
    pub supported_patterns: Vec<String>,
    pub depth: usize,
}

pub enum UiaCmd {
    Inspect {
        hwnd_val: usize,
        max_depth: usize,
        max_elements: usize,
        timeout_ms: u64,
        reply: Sender<std::result::Result<(Vec<UiaElementInfo>, bool), ProtocolError>>,
    },
    Action {
        token: String,
        action: String,
        value: Option<String>,
        reply: Sender<std::result::Result<serde_json::Value, ProtocolError>>,
    },
    ClearTokens,
    Shutdown,
}

pub struct UiaWorkerHandle {
    sender: Sender<UiaCmd>,
    _thread: Option<JoinHandle<()>>,
}

impl UiaWorkerHandle {
    pub fn new() -> Self {
        let (tx, rx) = channel::<UiaCmd>();
        let handle = thread::spawn(move || {
            uia_worker_loop(rx);
        });

        Self {
            sender: tx,
            _thread: Some(handle),
        }
    }

    pub fn inspect(
        &self,
        hwnd_val: usize,
        max_depth: usize,
        max_elements: usize,
        timeout_ms: u64,
    ) -> std::result::Result<(Vec<UiaElementInfo>, bool), ProtocolError> {
        let (reply_tx, reply_rx) = channel();
        self.sender
            .send(UiaCmd::Inspect {
                hwnd_val,
                max_depth,
                max_elements,
                timeout_ms,
                reply: reply_tx,
            })
            .map_err(|e| ProtocolError::new("uia_timeout", format!("UIA worker disconnected: {}", e)))?;

        reply_rx
            .recv()
            .map_err(|e| ProtocolError::new("uia_timeout", format!("UIA reply error: {}", e)))?
    }

    pub fn action(
        &self,
        token: String,
        action: String,
        value: Option<String>,
    ) -> std::result::Result<serde_json::Value, ProtocolError> {
        let (reply_tx, reply_rx) = channel();
        self.sender
            .send(UiaCmd::Action {
                token,
                action,
                value,
                reply: reply_tx,
            })
            .map_err(|e| ProtocolError::new("uia_timeout", format!("UIA worker disconnected: {}", e)))?;

        reply_rx
            .recv()
            .map_err(|e| ProtocolError::new("uia_timeout", format!("UIA reply error: {}", e)))?
    }

    pub fn clear_tokens(&self) {
        let _ = self.sender.send(UiaCmd::ClearTokens);
    }

    pub fn shutdown(&self) {
        let _ = self.sender.send(UiaCmd::Shutdown);
    }
}

fn control_type_to_string(id: i32) -> String {
    if id == UIA_ButtonControlTypeId.0 as i32 {
        "Button".into()
    } else if id == UIA_EditControlTypeId.0 as i32 {
        "Edit".into()
    } else if id == UIA_TextControlTypeId.0 as i32 {
        "Text".into()
    } else {
        format!("Control_{}", id)
    }
}

fn uia_worker_loop(receiver: Receiver<UiaCmd>) {
    let _ = win_utils::attach_thread_to_input_desktop();
    unsafe {
        let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
    }

    let uia: WinResult<IUIAutomation> = unsafe { CoCreateInstance(&CUIAutomation, None, CLSCTX_INPROC_SERVER) };
    if uia.is_err() {
        eprintln!("UiaWorker: failed to create CUIAutomation: {:?}", uia);
        return;
    }
    let uia = uia.unwrap();

    let mut token_map: HashMap<String, IUIAutomationElement> = HashMap::new();
    let mut token_counter: u64 = 0;

    while let Ok(cmd) = receiver.recv() {
        match cmd {
            UiaCmd::Inspect {
                hwnd_val,
                max_depth,
                max_elements,
                timeout_ms,
                reply,
            } => {
                token_map.clear();
                let deadline = Instant::now() + Duration::from_millis(timeout_ms);

                let hwnd = HWND(hwnd_val as *mut _);
                let root_elem = unsafe { uia.ElementFromHandle(hwnd) };
                let root = match root_elem {
                    Ok(r) => r,
                    Err(e) => {
                        let _ = reply.send(Err(ProtocolError::new(
                            "window_gone",
                            format!("Failed to get UIA element from window: {}", e.message()),
                        )));
                        continue;
                    }
                };

                let walker = match unsafe { uia.ControlViewWalker() } {
                    Ok(w) => w,
                    Err(e) => {
                        let _ = reply.send(Err(ProtocolError::new(
                            "capture_failed",
                            format!("Failed to get ControlViewWalker: {}", e.message()),
                        )));
                        continue;
                    }
                };

                let mut results = Vec::new();
                let mut truncated = false;
                let mut queue: Vec<(IUIAutomationElement, usize)> = vec![(root, 0)];

                while let Some((elem, depth)) = queue.pop() {
                    if Instant::now() >= deadline {
                        truncated = true;
                        break;
                    }

                    if results.len() >= max_elements {
                        truncated = true;
                        break;
                    }

                    token_counter += 1;
                    let token = format!("elem_{}", token_counter);

                    let name = unsafe { elem.CurrentName().map(|s| s.to_string()).unwrap_or_default() };
                    let auto_id = unsafe { elem.CurrentAutomationId().map(|s| s.to_string()).unwrap_or_default() };
                    let ctype_id = unsafe { elem.CurrentControlType().map(|c| c.0 as i32).unwrap_or(0) };
                    let ctype_str = control_type_to_string(ctype_id);

                    let rect = unsafe { elem.CurrentBoundingRectangle().unwrap_or_default() };
                    let bounds = RectBounds {
                        x: rect.left,
                        y: rect.top,
                        w: rect.right - rect.left,
                        h: rect.bottom - rect.top,
                    };

                    let is_enabled = unsafe { elem.CurrentIsEnabled().map(|b| b.as_bool()).unwrap_or(false) };
                    let is_offscreen = unsafe { elem.CurrentIsOffscreen().map(|b| b.as_bool()).unwrap_or(false) };

                    let mut supported_patterns = Vec::new();
                    let mut val_string = String::new();
                    if let Ok(ip_unk) = unsafe { elem.GetCurrentPattern(UIA_InvokePatternId) } {
                        if !Interface::as_raw(&ip_unk).is_null() {
                            supported_patterns.push("Invoke".into());
                        }
                    }
                    if let Ok(vp_unk) = unsafe { elem.GetCurrentPattern(UIA_ValuePatternId) } {
                        if !Interface::as_raw(&vp_unk).is_null() {
                            supported_patterns.push("Value".into());
                            if let Ok(vp) = vp_unk.cast::<IUIAutomationValuePattern>() {
                                if let Ok(bstr) = unsafe { vp.CurrentValue() } {
                                    val_string = bstr.to_string();
                                }
                            }
                        }
                    }

                    results.push(UiaElementInfo {
                        token: token.clone(),
                        name,
                        value: val_string,
                        automation_id: auto_id,
                        control_type: ctype_str,
                        bounds,
                        is_enabled,
                        is_offscreen,
                        supported_patterns,
                        depth,
                    });

                    token_map.insert(token, elem.clone());

                    // Enqueue children if within depth
                    if depth < max_depth {
                        let mut children = Vec::new();
                        if let Ok(child) = unsafe { walker.GetFirstChildElement(&elem) } {
                            let mut curr = child;
                            loop {
                                children.push((curr.clone(), depth + 1));
                                match unsafe { walker.GetNextSiblingElement(&curr) } {
                                    Ok(next) => curr = next,
                                    Err(_) => break,
                                }
                            }
                        }
                        // Reverse children so DFS preserves original order when popping
                        children.reverse();
                        queue.extend(children);
                    }
                }

                let _ = reply.send(Ok((results, truncated)));
            }
            UiaCmd::Action {
                token,
                action,
                value,
                reply,
            } => {
                let elem = match token_map.get(&token) {
                    Some(e) => e,
                    None => {
                        let _ = reply.send(Err(ProtocolError::new(
                            "stale_element",
                            format!("Element token '{}' not found in current observation", token),
                        )));
                        continue;
                    }
                };

                match action.as_str() {
                    "invoke" => unsafe {
                        let pattern_unknown = match elem.GetCurrentPattern(UIA_InvokePatternId) {
                            Ok(p) if !Interface::as_raw(&p).is_null() => p,
                            _ => {
                                let _ = reply.send(Err(ProtocolError::new(
                                    "unsupported",
                                    "InvokePattern not supported on this element",
                                )));
                                continue;
                            }
                        };

                        let invoke_pattern: std::result::Result<IUIAutomationInvokePattern, _> =
                            pattern_unknown.cast();
                        match invoke_pattern {
                            Ok(ip) => match ip.Invoke() {
                                Ok(_) => {
                                    let _ = reply.send(Ok(json!({ "status": "invoked" })));
                                }
                                Err(e) => {
                                    let _ = reply.send(Err(ProtocolError::new(
                                        "input_failed",
                                        format!("Failed to invoke element: {}", e.message()),
                                    )));
                                }
                            },
                            Err(_) => {
                                let _ = reply.send(Err(ProtocolError::new(
                                    "unsupported",
                                    "Cannot cast to IUIAutomationInvokePattern",
                                )));
                            }
                        }
                    },
                    "set_value" => unsafe {
                        let val_str = value.unwrap_or_default();
                        let pattern_unknown = match elem.GetCurrentPattern(UIA_ValuePatternId) {
                            Ok(p) if !Interface::as_raw(&p).is_null() => p,
                            _ => {
                                let _ = reply.send(Err(ProtocolError::new(
                                    "unsupported",
                                    "ValuePattern not supported on this element",
                                )));
                                continue;
                            }
                        };

                        let val_pattern: std::result::Result<IUIAutomationValuePattern, _> =
                            pattern_unknown.cast();
                        match val_pattern {
                            Ok(vp) => {
                                let bstr = BSTR::from(val_str);
                                match vp.SetValue(&bstr) {
                                    Ok(_) => {
                                        let _ = reply.send(Ok(json!({ "status": "value_set" })));
                                    }
                                    Err(e) => {
                                        let _ = reply.send(Err(ProtocolError::new(
                                            "input_failed",
                                            format!("Failed to set value: {}", e.message()),
                                        )));
                                    }
                                }
                            }
                            Err(_) => {
                                let _ = reply.send(Err(ProtocolError::new(
                                    "unsupported",
                                    "Cannot cast to IUIAutomationValuePattern",
                                )));
                            }
                        }
                    },
                    unknown => {
                        let _ = reply.send(Err(ProtocolError::new(
                            "unsupported",
                            format!("Unknown action '{}'", unknown),
                        )));
                    }
                }
            }
            UiaCmd::ClearTokens => {
                token_map.clear();
            }
            UiaCmd::Shutdown => {
                token_map.clear();
                break;
            }
        }
    }

    unsafe {
        CoUninitialize();
    }
}
