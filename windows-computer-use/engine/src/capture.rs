use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use serde_json::json;
use windows::core::{factory, Interface, Result};
use windows::Foundation::TypedEventHandler;
use windows::Graphics::Capture::{
    Direct3D11CaptureFrame, Direct3D11CaptureFramePool, GraphicsCaptureItem, GraphicsCaptureSession,
};
use windows::Graphics::DirectX::Direct3D11::IDirect3DDevice;
use windows::Graphics::DirectX::DirectXPixelFormat;
use windows::Win32::Foundation::HWND;
use windows::Win32::Graphics::Gdi::HMONITOR;
use windows::Win32::Graphics::Direct3D::{D3D_DRIVER_TYPE_HARDWARE, D3D_FEATURE_LEVEL_11_0};
use windows::Win32::Graphics::Direct3D11::{
    D3D11CreateDevice, ID3D11Device, ID3D11DeviceContext, ID3D11Texture2D,
    D3D11_CPU_ACCESS_READ, D3D11_CREATE_DEVICE_BGRA_SUPPORT, D3D11_MAP_READ,
    D3D11_MAPPED_SUBRESOURCE, D3D11_SDK_VERSION, D3D11_TEXTURE2D_DESC, D3D11_USAGE_STAGING,
};
use windows::Win32::Graphics::Dxgi::Common::{DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_SAMPLE_DESC};
use windows::Win32::Graphics::Dxgi::IDXGIDevice;
use windows::Win32::System::Com::{CoInitializeEx, CoUninitialize, COINIT_MULTITHREADED};
use windows::Win32::System::WinRT::Direct3D11::{
    CreateDirect3D11DeviceFromDXGIDevice, IDirect3DDxgiInterfaceAccess,
};
use windows::Win32::System::WinRT::Graphics::Capture::IGraphicsCaptureItemInterop;
use windows::Win32::UI::WindowsAndMessaging::IsWindow;

use crate::protocol::ProtocolError;
use crate::win_utils::{self, RectBounds};

#[derive(Clone)]
pub struct FrameSnapshot {
    pub frame_id: u64,
    pub width: u32,
    pub height: u32,
    pub stride_bytes: u32,
    pub content_timestamp_ns: u64,
    pub received_timestamp_ns: u64,
    pub data: Arc<Vec<u8>>,
}

#[derive(Clone)]
pub enum CaptureTarget {
    Window {
        hwnd: HWND,
        hwnd_val: usize,
        pid: u32,
        create_time: String,
        geometry_epoch: Arc<AtomicU64>,
        foreground_epoch: Arc<AtomicU64>,
    },
    #[allow(dead_code)]
    Monitor {
        hmonitor: HMONITOR,
        hmonitor_val: usize,
        info: win_utils::MonitorInfo,
        epoch: Arc<AtomicU64>,
    },
}

impl CaptureTarget {
    pub fn bump_geometry_epoch(&self) {
        match self {
            CaptureTarget::Window { geometry_epoch, .. } => {
                geometry_epoch.fetch_add(1, Ordering::SeqCst);
            }
            CaptureTarget::Monitor { epoch, .. } => {
                epoch.fetch_add(1, Ordering::SeqCst);
            }
        }
    }
}

pub enum CaptureCmd {
    Start {
        hwnd_val: usize,
        pid: u32,
        create_time: String,
        geometry_epoch: Arc<AtomicU64>,
        foreground_epoch: Arc<AtomicU64>,
        reply: Sender<std::result::Result<(), ProtocolError>>,
    },
    StartMonitor {
        hmonitor_val: usize,
        reply: Sender<std::result::Result<win_utils::MonitorInfo, ProtocolError>>,
    },
    Stop {
        reply: Sender<()>,
    },
    Observe {
        after_frame_id: u64,
        timeout_ms: u64,
        reply: Sender<std::result::Result<(serde_json::Value, Vec<u8>), ProtocolError>>,
    },
    Shutdown,
}

pub struct CaptureWorkerHandle {
    sender: Sender<CaptureCmd>,
    _thread: Option<JoinHandle<()>>,
}

impl CaptureWorkerHandle {
    pub fn new() -> Self {
        let (tx, rx) = channel::<CaptureCmd>();
        let thread_handle = thread::spawn(move || {
            capture_worker_loop(rx);
        });

        Self {
            sender: tx,
            _thread: Some(thread_handle),
        }
    }

    pub fn start_capture(
        &self,
        hwnd_val: usize,
        pid: u32,
        create_time: String,
        geometry_epoch: Arc<AtomicU64>,
        foreground_epoch: Arc<AtomicU64>,
    ) -> std::result::Result<(), ProtocolError> {
        let (reply_tx, reply_rx) = channel();
        self.sender
            .send(CaptureCmd::Start {
                hwnd_val,
                pid,
                create_time,
                geometry_epoch,
                foreground_epoch,
                reply: reply_tx,
            })
            .map_err(|e| ProtocolError::new("capture_failed", format!("Worker disconnected: {}", e)))?;

        reply_rx
            .recv()
            .map_err(|e| ProtocolError::new("capture_failed", format!("Worker reply error: {}", e)))?
    }

    pub fn start_monitor_capture(
        &self,
        hmonitor_val: usize,
    ) -> std::result::Result<win_utils::MonitorInfo, ProtocolError> {
        let (reply_tx, reply_rx) = channel();
        self.sender
            .send(CaptureCmd::StartMonitor {
                hmonitor_val,
                reply: reply_tx,
            })
            .map_err(|e| ProtocolError::new("capture_failed", format!("Worker disconnected: {}", e)))?;

        reply_rx
            .recv()
            .map_err(|e| ProtocolError::new("capture_failed", format!("Worker reply error: {}", e)))?
    }

    pub fn stop_capture(&self) {
        let (reply_tx, reply_rx) = channel();
        let _ = self.sender.send(CaptureCmd::Stop { reply: reply_tx });
        let _ = reply_rx.recv();
    }

    pub fn observe(
        &self,
        after_frame_id: u64,
        timeout_ms: u64,
    ) -> std::result::Result<(serde_json::Value, Vec<u8>), ProtocolError> {
        let (reply_tx, reply_rx) = channel();
        self.sender
            .send(CaptureCmd::Observe {
                after_frame_id,
                timeout_ms,
                reply: reply_tx,
            })
            .map_err(|e| ProtocolError::new("capture_failed", format!("Worker disconnected: {}", e)))?;

        reply_rx
            .recv()
            .map_err(|e| ProtocolError::new("capture_failed", format!("Worker reply error: {}", e)))?
    }

    pub fn shutdown(&self) {
        let _ = self.sender.send(CaptureCmd::Shutdown);
    }
}

struct ActiveSession {
    target: CaptureTarget,
    _item: GraphicsCaptureItem,
    frame_pool: Direct3D11CaptureFramePool,
    session: GraphicsCaptureSession,
    frame_arrived_flag: Arc<AtomicBool>,
    staging_texture: Option<ID3D11Texture2D>,
    staging_width: u32,
    staging_height: u32,
    last_bounds: RectBounds,
}

fn capture_worker_loop(receiver: Receiver<CaptureCmd>) {
    let _ = win_utils::attach_thread_to_input_desktop();
    unsafe {
        let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
    }

    // Initialize D3D11 device on this dedicated MTA worker thread
    let mut d3d11_device: Option<ID3D11Device> = None;
    let mut d3d11_context: Option<ID3D11DeviceContext> = None;
    let mut feature_level = D3D_FEATURE_LEVEL_11_0;

    let res = unsafe {
        D3D11CreateDevice(
            None,
            D3D_DRIVER_TYPE_HARDWARE,
            None,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            Some(&[D3D_FEATURE_LEVEL_11_0]),
            D3D11_SDK_VERSION,
            Some(&mut d3d11_device),
            Some(&mut feature_level),
            Some(&mut d3d11_context),
        )
    };

    if res.is_err() {
        eprintln!("CaptureWorker: failed to create D3D11 device: {:?}", res);
        return;
    }

    let device = d3d11_device.unwrap();
    let context = d3d11_context.unwrap();
    let dxgi_device: IDXGIDevice = device.cast().unwrap();
    let inspectable = unsafe { CreateDirect3D11DeviceFromDXGIDevice(&dxgi_device).unwrap() };
    let winrt_device: IDirect3DDevice = inspectable.cast().unwrap();

    let mut active: Option<ActiveSession> = None;
    let mut latest_frame: Option<FrameSnapshot> = None;
    let mut frame_counter: u64 = 0;
    let mut observation_counter: u64 = 0;
    let clock_start = Instant::now();

    loop {
        // Poll for commands non-blocking or with small timeout
        match receiver.recv_timeout(Duration::from_millis(5)) {
            Ok(CaptureCmd::Start {
                hwnd_val,
                pid,
                create_time,
                geometry_epoch,
                foreground_epoch,
                reply,
            }) => {
                // Cleanup existing session if any
                if let Some(old) = active.take() {
                    let _ = old.session.Close();
                    let _ = old.frame_pool.Close();
                }
                latest_frame = None;

                let hwnd = HWND(hwnd_val as *mut _);
                let bounds = match win_utils::get_window_extended_frame_bounds(hwnd) {
                    Ok(b) => b,
                    Err(e) => {
                        let _ = reply.send(Err(ProtocolError::new("capture_failed", e)));
                        continue;
                    }
                };

                let item_res = unsafe {
                    let interop = factory::<GraphicsCaptureItem, IGraphicsCaptureItemInterop>();
                    match interop {
                        Ok(factory) => factory.CreateForWindow(hwnd),
                        Err(e) => Err(e),
                    }
                };

                let item: GraphicsCaptureItem = match item_res {
                    Ok(it) => it,
                    Err(e) => {
                        let _ = reply.send(Err(ProtocolError::new(
                            "capture_failed",
                            format!("Failed to create GraphicsCaptureItem: {}", e.message()),
                        )));
                        continue;
                    }
                };

                let mut item_size = match item.Size() {
                    Ok(s) => {
                        eprintln!("Capture item_size: Width={}, Height={}", s.Width, s.Height);
                        s
                    }
                    Err(e) => {
                        let _ = reply.send(Err(ProtocolError::new(
                            "capture_failed",
                            format!("Failed to get capture item size: {}", e.message()),
                        )));
                        continue;
                    }
                };

                if item_size.Width <= 0 || item_size.Height <= 0 {
                    eprintln!("item.Size() was {}x{}, falling back to window bounds {}x{}", item_size.Width, item_size.Height, bounds.w, bounds.h);
                    item_size.Width = bounds.w.max(1);
                    item_size.Height = bounds.h.max(1);
                }

                let frame_pool = match Direct3D11CaptureFramePool::CreateFreeThreaded(
                    &winrt_device,
                    DirectXPixelFormat::B8G8R8A8UIntNormalized,
                    2,
                    item_size,
                ) {
                    Ok(pool) => pool,
                    Err(e) => {
                        eprintln!("CreateFreeThreaded error: {:?}, hr={:x}", e, e.code().0);
                        let _ = reply.send(Err(ProtocolError::new(
                            "capture_failed",
                            format!("Failed to create frame pool: {} (hr={:x})", e.message(), e.code().0),
                        )));
                        continue;
                    }
                };

                let session = match frame_pool.CreateCaptureSession(&item) {
                    Ok(sess) => sess,
                    Err(e) => {
                        let _ = reply.send(Err(ProtocolError::new(
                            "capture_failed",
                            format!("Failed to create capture session: {}", e.message()),
                        )));
                        continue;
                    }
                };

                let arrived_flag = Arc::new(AtomicBool::new(false));
                let arrived_clone = arrived_flag.clone();
                let _ = frame_pool.FrameArrived(&TypedEventHandler::new(move |_sender, _args| {
                    arrived_clone.store(true, Ordering::SeqCst);
                    Ok(())
                }));

                let _ = session.SetIsCursorCaptureEnabled(false);
                if let Err(e) = session.StartCapture() {
                    let _ = reply.send(Err(ProtocolError::new(
                        "capture_failed",
                        format!("Failed to start capture: {}", e.message()),
                    )));
                    continue;
                }

                active = Some(ActiveSession {
                    target: CaptureTarget::Window {
                        hwnd,
                        hwnd_val,
                        pid,
                        create_time,
                        geometry_epoch,
                        foreground_epoch,
                    },
                    _item: item,
                    frame_pool,
                    session,
                    frame_arrived_flag: arrived_flag,
                    staging_texture: None,
                    staging_width: 0,
                    staging_height: 0,
                    last_bounds: bounds,
                });

                let _ = reply.send(Ok(()));
            }
            Ok(CaptureCmd::StartMonitor {
                hmonitor_val,
                reply,
            }) => {
                if let Some(old) = active.take() {
                    let _ = old.session.Close();
                    let _ = old.frame_pool.Close();
                }
                latest_frame = None;

                if let Err(msg) = win_utils::check_interactive_desktop() {
                    let _ = reply.send(Err(ProtocolError::new("desktop_locked", msg)));
                    continue;
                }

                let monitors = win_utils::list_monitors();
                let mon = match monitors.into_iter().find(|m| m.hmonitor == hmonitor_val.to_string()) {
                    Some(m) => m,
                    None => {
                        let _ = reply.send(Err(ProtocolError::new(
                            "monitor_not_found",
                            format!("Monitor handle {} not found", hmonitor_val),
                        )));
                        continue;
                    }
                };

                let hmonitor = HMONITOR(hmonitor_val as *mut _);
                let item_res = unsafe {
                    let interop = factory::<GraphicsCaptureItem, IGraphicsCaptureItemInterop>();
                    match interop {
                        Ok(factory) => factory.CreateForMonitor(hmonitor),
                        Err(e) => Err(e),
                    }
                };

                let item: GraphicsCaptureItem = match item_res {
                    Ok(it) => it,
                    Err(e) => {
                        let _ = reply.send(Err(ProtocolError::new(
                            "capture_failed",
                            format!("Failed to create GraphicsCaptureItem for monitor: {}", e.message()),
                        )));
                        continue;
                    }
                };

                let mut item_size = match item.Size() {
                    Ok(s) => s,
                    Err(e) => {
                        let _ = reply.send(Err(ProtocolError::new(
                            "capture_failed",
                            format!("Failed to get capture item size: {}", e.message()),
                        )));
                        continue;
                    }
                };

                if item_size.Width <= 0 || item_size.Height <= 0 {
                    item_size.Width = mon.bounds.w.max(1);
                    item_size.Height = mon.bounds.h.max(1);
                }

                let frame_pool = match Direct3D11CaptureFramePool::CreateFreeThreaded(
                    &winrt_device,
                    DirectXPixelFormat::B8G8R8A8UIntNormalized,
                    2,
                    item_size,
                ) {
                    Ok(pool) => pool,
                    Err(e) => {
                        let _ = reply.send(Err(ProtocolError::new(
                            "capture_failed",
                            format!("Failed to create frame pool for monitor: {}", e.message()),
                        )));
                        continue;
                    }
                };

                let session = match frame_pool.CreateCaptureSession(&item) {
                    Ok(sess) => sess,
                    Err(e) => {
                        let _ = reply.send(Err(ProtocolError::new(
                            "capture_failed",
                            format!("Failed to create capture session: {}", e.message()),
                        )));
                        continue;
                    }
                };

                let arrived_flag = Arc::new(AtomicBool::new(false));
                let arrived_clone = arrived_flag.clone();
                let _ = frame_pool.FrameArrived(&TypedEventHandler::new(move |_sender, _args| {
                    arrived_clone.store(true, Ordering::SeqCst);
                    Ok(())
                }));

                let _ = session.SetIsCursorCaptureEnabled(false);
                if let Err(e) = session.StartCapture() {
                    let _ = reply.send(Err(ProtocolError::new(
                        "capture_failed",
                        format!("Failed to start monitor capture: {}", e.message()),
                    )));
                    continue;
                }

                let last_bounds = mon.bounds.clone();
                active = Some(ActiveSession {
                    target: CaptureTarget::Monitor {
                        hmonitor,
                        hmonitor_val,
                        info: mon.clone(),
                        epoch: Arc::new(AtomicU64::new(1)),
                    },
                    _item: item,
                    frame_pool,
                    session,
                    frame_arrived_flag: arrived_flag,
                    staging_texture: None,
                    staging_width: 0,
                    staging_height: 0,
                    last_bounds,
                });

                let _ = reply.send(Ok(mon));
            }
            Ok(CaptureCmd::Stop { reply }) => {
                if let Some(old) = active.take() {
                    let _ = old.session.Close();
                    let _ = old.frame_pool.Close();
                }
                latest_frame = None;
                let _ = reply.send(());
            }
            Ok(CaptureCmd::Observe {
                after_frame_id,
                timeout_ms,
                reply,
            }) => {
                if active.is_none() {
                    let _ = reply.send(Err(ProtocolError::new("invalid_request", "No capture target attached")));
                    continue;
                }

                if let Some(ref sess) = active {
                    if let CaptureTarget::Window { hwnd, .. } = &sess.target {
                        if unsafe { !IsWindow(*hwnd).as_bool() } {
                            let _ = reply.send(Err(ProtocolError::new("window_gone", "Target window closed")));
                            continue;
                        }
                    }
                }

                let deadline = Instant::now() + Duration::from_millis(timeout_ms);
                let mut captured_frame: Option<FrameSnapshot> = None;

                // Check if current latest frame already satisfies condition
                if let Some(ref lf) = latest_frame {
                    if lf.frame_id > after_frame_id {
                        captured_frame = Some(lf.clone());
                    }
                }

                // If not, wait for new frame up to deadline
                if captured_frame.is_none() {
                    while Instant::now() < deadline {
                        // Check if window is still alive
                        if let Some(ref sess) = active {
                            if let CaptureTarget::Window { hwnd, .. } = &sess.target {
                                if unsafe { !IsWindow(*hwnd).as_bool() } {
                                    break;
                                }
                            }
                        }

                        // Try draining frame
                        if let Some(ref mut sess) = active {
                            if let Ok(frame) = sess.frame_pool.TryGetNextFrame() {
                                if let Ok(snapshot) = readback_frame(
                                    &device,
                                    &context,
                                    &winrt_device,
                                    sess,
                                    frame,
                                    &mut frame_counter,
                                    &clock_start,
                                ) {
                                    latest_frame = Some(snapshot.clone());
                                    if snapshot.frame_id > after_frame_id {
                                        captured_frame = Some(snapshot);
                                        break;
                                    }
                                }
                            }
                        }
                        thread::sleep(Duration::from_millis(4));
                    }
                }

                if let Some(snapshot) = captured_frame {
                    observation_counter += 1;
                    let sess = active.as_ref().unwrap();

                    let metadata = match &sess.target {
                        CaptureTarget::Window { hwnd, hwnd_val, pid, create_time, geometry_epoch, foreground_epoch } => {
                            let current_bounds = win_utils::get_window_extended_frame_bounds(*hwnd)
                                .unwrap_or(sess.last_bounds.clone());
                            let dpi = unsafe { windows::Win32::UI::HiDpi::GetDpiForWindow(*hwnd) };

                            json!({
                                "target_type": "window",
                                "observation_id": observation_counter,
                                "frame_id": snapshot.frame_id,
                                "window_identity": {
                                    "hwnd": hwnd_val.to_string(),
                                    "pid": *pid,
                                    "process_create_time_utc": create_time,
                                },
                                "geometry_epoch": geometry_epoch.load(Ordering::SeqCst),
                                "foreground_epoch": foreground_epoch.load(Ordering::SeqCst),
                                "width": snapshot.width,
                                "height": snapshot.height,
                                "stride_bytes": snapshot.stride_bytes,
                                "pixel_format": "BGRA8",
                                "content_timestamp_ns": snapshot.content_timestamp_ns,
                                "received_timestamp_ns": snapshot.received_timestamp_ns,
                                "published_timestamp_ns": clock_start.elapsed().as_nanos() as u64,
                                "capture_bounds_physical_px": {
                                    "x": current_bounds.x,
                                    "y": current_bounds.y,
                                    "w": current_bounds.w,
                                    "h": current_bounds.h,
                                },
                                "dpi": dpi,
                                "payload_len": snapshot.data.len(),
                            })
                        }
                        CaptureTarget::Monitor { info, epoch, .. } => {
                            json!({
                                "target_type": "monitor",
                                "observation_id": observation_counter,
                                "frame_id": snapshot.frame_id,
                                "monitor_info": info,
                                "geometry_epoch": epoch.load(Ordering::SeqCst),
                                "foreground_epoch": 1,
                                "width": snapshot.width,
                                "height": snapshot.height,
                                "stride_bytes": snapshot.stride_bytes,
                                "pixel_format": "BGRA8",
                                "content_timestamp_ns": snapshot.content_timestamp_ns,
                                "received_timestamp_ns": snapshot.received_timestamp_ns,
                                "published_timestamp_ns": clock_start.elapsed().as_nanos() as u64,
                                "capture_bounds_physical_px": {
                                    "x": info.bounds.x,
                                    "y": info.bounds.y,
                                    "w": info.bounds.w,
                                    "h": info.bounds.h,
                                },
                                "dpi": info.dpi,
                                "payload_len": snapshot.data.len(),
                            })
                        }
                    };

                    let payload = (*snapshot.data).clone();
                    let _ = reply.send(Ok((metadata, payload)));
                } else {
                    if let Some(ref sess) = active {
                        if let CaptureTarget::Window { hwnd, .. } = &sess.target {
                            if unsafe { !IsWindow(*hwnd).as_bool() } {
                                let _ = reply.send(Err(ProtocolError::new("window_gone", "Target window closed")));
                                continue;
                            }
                        }
                    }
                    let _ = reply.send(Err(ProtocolError::new("no_fresh_frame", "No fresh frame received within timeout")));
                }
            }
            Ok(CaptureCmd::Shutdown) => {
                if let Some(old) = active.take() {
                    let _ = old.session.Close();
                    let _ = old.frame_pool.Close();
                }
                break;
            }
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
                // Background drain of frames to keep frame pool healthy and track bounds
                if let Some(ref mut sess) = active {
                    // Check if bounds changed
                    if let CaptureTarget::Window { hwnd, geometry_epoch, .. } = &sess.target {
                        if let Ok(b) = win_utils::get_window_extended_frame_bounds(*hwnd) {
                            if b != sess.last_bounds {
                                sess.last_bounds = b;
                                geometry_epoch.fetch_add(1, Ordering::SeqCst);
                            }
                        }
                    }

                    if sess.frame_arrived_flag.swap(false, Ordering::SeqCst) {
                        if let Ok(frame) = sess.frame_pool.TryGetNextFrame() {
                            if let Ok(snapshot) = readback_frame(
                                &device,
                                &context,
                                &winrt_device,
                                sess,
                                frame,
                                &mut frame_counter,
                                &clock_start,
                            ) {
                                latest_frame = Some(snapshot);
                            }
                        }
                    }
                }
            }
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                break;
            }
        }
    }

    unsafe {
        CoUninitialize();
    }
}

fn readback_frame(
    device: &ID3D11Device,
    context: &ID3D11DeviceContext,
    winrt_device: &IDirect3DDevice,
    sess: &mut ActiveSession,
    frame: Direct3D11CaptureFrame,
    frame_counter: &mut u64,
    clock_start: &Instant,
) -> Result<FrameSnapshot> {
    let received_ns = clock_start.elapsed().as_nanos() as u64;
    let content_size = frame.ContentSize()?;
    let width = content_size.Width as u32;
    let height = content_size.Height as u32;

    if width == 0 || height == 0 {
        return Err(windows::core::Error::new(
            windows::core::HRESULT(0x80004005_u32 as i32),
            "Frame has zero dimension",
        ));
    }

    // Handle resize
    if width != sess.staging_width || height != sess.staging_height || sess.staging_texture.is_none() {
        let desc = D3D11_TEXTURE2D_DESC {
            Width: width,
            Height: height,
            MipLevels: 1,
            ArraySize: 1,
            Format: DXGI_FORMAT_B8G8R8A8_UNORM,
            SampleDesc: DXGI_SAMPLE_DESC {
                Count: 1,
                Quality: 0,
            },
            Usage: D3D11_USAGE_STAGING,
            BindFlags: 0,
            CPUAccessFlags: D3D11_CPU_ACCESS_READ.0 as u32,
            MiscFlags: 0,
        };

        let mut staging: Option<ID3D11Texture2D> = None;
        unsafe {
            device.CreateTexture2D(&desc, None, Some(&mut staging))?;
        }
        sess.staging_texture = staging;
        sess.staging_width = width;
        sess.staging_height = height;

        let _ = sess.frame_pool.Recreate(
            winrt_device,
            DirectXPixelFormat::B8G8R8A8UIntNormalized,
            2,
            content_size,
        );
        sess.target.bump_geometry_epoch();
    }

    let surface = frame.Surface()?;
    let access: IDirect3DDxgiInterfaceAccess = surface.cast()?;
    let source_texture: ID3D11Texture2D = unsafe { access.GetInterface()? };

    let staging_ref = sess.staging_texture.as_ref().unwrap();

    unsafe {
        context.CopyResource(staging_ref, &source_texture);

        let mut mapped = D3D11_MAPPED_SUBRESOURCE::default();
        context.Map(staging_ref, 0, D3D11_MAP_READ, 0, Some(&mut mapped))?;

        let row_pitch = mapped.RowPitch as usize;
        let row_bytes = (width * 4) as usize;
        let mut buffer = vec![0u8; row_bytes * height as usize];

        let src_ptr = mapped.pData as *const u8;
        for row in 0..height as usize {
            let src_row = src_ptr.add(row * row_pitch);
            let dst_row = &mut buffer[row * row_bytes..(row + 1) * row_bytes];
            std::ptr::copy_nonoverlapping(src_row, dst_row.as_mut_ptr(), row_bytes);
        }

        context.Unmap(staging_ref, 0);

        *frame_counter += 1;
        let content_ts = frame
            .SystemRelativeTime()
            .map(|t| (t.Duration * 100) as u64)
            .unwrap_or(received_ns);

        Ok(FrameSnapshot {
            frame_id: *frame_counter,
            width,
            height,
            stride_bytes: row_bytes as u32,
            content_timestamp_ns: content_ts,
            received_timestamp_ns: received_ns,
            data: Arc::new(buffer),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use windows::Graphics::SizeInt32;

    #[test]
    fn test_frame_pool_creation() {
        unsafe {
            let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
            let mut d3d11_device: Option<ID3D11Device> = None;
            let mut d3d11_context: Option<ID3D11DeviceContext> = None;
            let mut feature_level = D3D_FEATURE_LEVEL_11_0;

            let res = D3D11CreateDevice(
                None,
                D3D_DRIVER_TYPE_HARDWARE,
                None,
                D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                Some(&[D3D_FEATURE_LEVEL_11_0]),
                D3D11_SDK_VERSION,
                Some(&mut d3d11_device),
                Some(&mut feature_level),
                Some(&mut d3d11_context),
            );
            assert!(res.is_ok(), "D3D11CreateDevice failed: {:?}", res);

            let device = d3d11_device.unwrap();
            let dxgi_device: IDXGIDevice = device.cast().unwrap();
            let inspectable = CreateDirect3D11DeviceFromDXGIDevice(&dxgi_device);
            println!("inspectable res: {:?}", inspectable);
            assert!(inspectable.is_ok(), "CreateDirect3D11DeviceFromDXGIDevice failed: {:?}", inspectable);

            let winrt_device: IDirect3DDevice = inspectable.unwrap().cast().unwrap();
            println!("winrt_device cast ok!");

            let pool_res = Direct3D11CaptureFramePool::CreateFreeThreaded(
                &winrt_device,
                DirectXPixelFormat::B8G8R8A8UIntNormalized,
                2,
                SizeInt32 { Width: 400, Height: 300 },
            );
            println!("pool_res: {:?}", pool_res);
            assert!(pool_res.is_ok(), "CreateFreeThreaded failed: {:?}", pool_res);
        }
    }
}

