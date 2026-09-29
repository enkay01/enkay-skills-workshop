//! Shared pre-dispatch guard.
//!
//! Every input action in this engine is dispatched through the checks in this
//! module. The guarded click originally carried these checks inline; they were
//! decomposed here so that typing, chords, scrolling, hover and drag are written
//! as a *composition* of the same checks rather than as private copies that can
//! drift from them.
//!
//! Each check is an individually reusable function so that a future action with
//! different needs can pick the subset it requires without re-deriving the rules.
//!
//! The checks, in the order a pointer action runs them:
//!
//! 1. `require_observation`     - the target observation exists, matches, and is fresh
//! 2. `validate_frame_point`    - the requested point lies inside the observed frame
//! 3. `require_interactive_desktop` - the session has an unlocked interactive desktop
//! 4. `require_window_identity` - the attached window is still alive and still the
//!                                same process with the same creation time
//! 5. `require_foreground`      - the attached window is the foreground window
//! 6. `require_unchanged_geometry` - window bounds still match the bounds recorded
//!                                    with the observation
//! 7. `require_hit_test_ownership`  - the physical point resolves to the attached
//!                                    window by hit test
//! 8. `require_within_virtual_screen` - the physical point is on a real display

use crate::input::{AttachedIdentity, LastObservation};
use crate::protocol::ProtocolError;
use crate::win_utils;
use windows::Win32::Foundation::{HWND, POINT};
use windows::Win32::UI::WindowsAndMessaging::{
    GetAncestor, GetForegroundWindow, GetSystemMetrics, WindowFromPoint, GA_ROOT,
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN,
};

/// Default observation age limit, matching the original guarded click.
pub const DEFAULT_MAX_AGE_MS: u64 = 500;

/// A pointer target expressed in the pixel coordinate system of an observation.
///
/// A bare screen coordinate is deliberately not representable: a caller must say
/// which frame the target belongs to.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FrameTarget {
    /// Bounding box; its centre is used.
    Bbox { x: i32, y: i32, w: i32, h: i32 },
    /// A single point.
    Point { x: i32, y: i32 },
}

impl FrameTarget {
    /// Validate against the observed frame extent and resolve to a frame point.
    pub fn resolve(&self, frame_w: u32, frame_h: u32) -> Result<(i32, i32), ProtocolError> {
        match self {
            FrameTarget::Bbox { x, y, w, h } => {
                validate_bbox_in_frame(*x, *y, *w, *h, frame_w, frame_h)?;
                Ok((x + w / 2, y + h / 2))
            }
            FrameTarget::Point { x, y } => {
                validate_point_in_frame(*x, *y, frame_w, frame_h)?;
                Ok((*x, *y))
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Individually reusable checks
// ---------------------------------------------------------------------------

/// Check 1: the observation exists, matches the requested id, and is fresh.
///
/// Returns the age of the observation in milliseconds.
pub fn require_observation(
    last_obs: Option<&LastObservation>,
    requested_id: u64,
    max_age_ms: u64,
) -> Result<u64, ProtocolError> {
    let obs = match last_obs {
        Some(o) => o,
        None => {
            return Err(ProtocolError::new(
                "stale_observation",
                "No observation recorded",
            ));
        }
    };

    if obs.observation_id != requested_id {
        return Err(ProtocolError::new(
            "stale_observation",
            format!(
                "Observation id mismatch: requested {}, current latest is {}",
                requested_id, obs.observation_id
            ),
        ));
    }

    let age_ms = obs.published_instant.elapsed().as_millis() as u64;
    if age_ms > max_age_ms {
        return Err(ProtocolError::new(
            "stale_observation",
            format!(
                "Observation expired: age {} ms exceeds max allowed {} ms",
                age_ms, max_age_ms
            ),
        ));
    }

    Ok(age_ms)
}

/// Check 2a: a bounding box lies wholly inside the observed frame.
pub fn validate_bbox_in_frame(
    bx: i32,
    by: i32,
    bw: i32,
    bh: i32,
    frame_w: u32,
    frame_h: u32,
) -> Result<(), ProtocolError> {
    if bx < 0 || by < 0 || bw <= 0 || bh <= 0
        || (bx + bw) as u32 > frame_w
        || (by + bh) as u32 > frame_h
    {
        return Err(ProtocolError::new(
            "invalid_coordinates",
            format!(
                "Target bbox [{}, {}, {}, {}] is outside frame dimensions [{}x{}]",
                bx, by, bw, bh, frame_w, frame_h
            ),
        ));
    }
    Ok(())
}

/// Check 2b: a point lies inside the observed frame.
pub fn validate_point_in_frame(
    px: i32,
    py: i32,
    frame_w: u32,
    frame_h: u32,
) -> Result<(), ProtocolError> {
    if px < 0 || py < 0 || px as u32 >= frame_w || py as u32 >= frame_h {
        return Err(ProtocolError::new(
            "invalid_coordinates",
            format!(
                "Target point [{}, {}] is outside frame dimensions [{}x{}]",
                px, py, frame_w, frame_h
            ),
        ));
    }
    Ok(())
}

/// Check 3: the session has an unlocked, interactive desktop.
pub fn require_interactive_desktop() -> Result<(), ProtocolError> {
    win_utils::check_interactive_desktop()
        .map(|_| ())
        .map_err(|e| ProtocolError::new("desktop_inaccessible", e))
}

/// Check 4: the attached window is still alive and still the same process.
pub fn require_window_identity(attached: &AttachedIdentity) -> Result<HWND, ProtocolError> {
    win_utils::validate_window_identity(attached.hwnd_num, attached.pid, &attached.create_time)
        .map_err(|e| ProtocolError::new("window_gone", format!("Window identity invalid: {}", e)))?;
    Ok(HWND(attached.hwnd_num as *mut _))
}

/// Check 5: the attached window is the foreground window, or the root owner of it.
pub fn require_foreground(attached: &AttachedIdentity, target: HWND) -> Result<(), ProtocolError> {
    let fg_hwnd = unsafe { GetForegroundWindow() };
    let fg_root = unsafe { GetAncestor(fg_hwnd, GA_ROOT) };
    if fg_hwnd != target && fg_root != target {
        attached.foreground_epoch.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        return Err(ProtocolError::new(
            "foreground_changed",
            format!(
                "Target window is not in the foreground (current foreground is {:?})",
                fg_hwnd.0
            ),
        ));
    }
    Ok(())
}

/// Check 6: window bounds still match the bounds recorded with the observation.
pub fn require_unchanged_geometry(
    attached: &AttachedIdentity,
    target: HWND,
    obs: &LastObservation,
) -> Result<win_utils::RectBounds, ProtocolError> {
    let cur_bounds = win_utils::get_window_extended_frame_bounds(target)
        .map_err(|e| ProtocolError::new("geometry_changed", e))?;

    if cur_bounds.x != obs.bounds_x
        || cur_bounds.y != obs.bounds_y
        || cur_bounds.w != obs.bounds_w
        || cur_bounds.h != obs.bounds_h
    {
        attached
            .geometry_epoch
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        return Err(ProtocolError::new(
            "geometry_changed",
            format!(
                "Window bounds moved/resized from ({}, {}, {}, {}) to ({}, {}, {}, {})",
                obs.bounds_x, obs.bounds_y, obs.bounds_w, obs.bounds_h,
                cur_bounds.x, cur_bounds.y, cur_bounds.w, cur_bounds.h
            ),
        ));
    }

    Ok(cur_bounds)
}

/// Check 7: the physical point resolves to the attached window by hit test.
pub fn require_hit_test_ownership(target: HWND, screen_x: i32, screen_y: i32) -> Result<(), ProtocolError> {
    let hwnd_at_pt = unsafe { WindowFromPoint(POINT { x: screen_x, y: screen_y }) };
    let root_at_pt = unsafe { GetAncestor(hwnd_at_pt, GA_ROOT) };
    if hwnd_at_pt != target && root_at_pt != target {
        return Err(ProtocolError::new(
            "target_occluded",
            format!(
                "Target point ({}, {}) is occluded by another window {:?} (root {:?})",
                screen_x, screen_y, hwnd_at_pt.0, root_at_pt.0
            ),
        ));
    }
    Ok(())
}

/// Check 8: the physical point is on a real display.
///
/// Returns the virtual screen rectangle `(x, y, w, h)`.
pub fn require_within_virtual_screen(screen_x: i32, screen_y: i32) -> Result<(i32, i32, i32, i32), ProtocolError> {
    let vx = unsafe { GetSystemMetrics(SM_XVIRTUALSCREEN) };
    let vy = unsafe { GetSystemMetrics(SM_YVIRTUALSCREEN) };
    let vw = unsafe { GetSystemMetrics(SM_CXVIRTUALSCREEN) };
    let vh = unsafe { GetSystemMetrics(SM_CYVIRTUALSCREEN) };

    if vw <= 0 || vh <= 0 {
        return Err(ProtocolError::new(
            "input_failed",
            "Virtual screen dimensions are invalid",
        ));
    }

    if screen_x < vx || screen_x >= vx + vw || screen_y < vy || screen_y >= vy + vh {
        return Err(ProtocolError::new(
            "invalid_coordinates",
            format!("Screen coordinates ({}, {}) outside virtual screen bounds", screen_x, screen_y),
        ));
    }

    Ok((vx, vy, vw, vh))
}

// ---------------------------------------------------------------------------
// Composed plans
// ---------------------------------------------------------------------------

/// The result of running the shared guard for a pointer action.
#[derive(Debug, Clone)]
pub struct PointerPlan {
    /// The physical point the action will land on.
    pub screen_x: i32,
    pub screen_y: i32,
    /// Age of the observation the decision was made against.
    pub age_ms: u64,
    /// The observation the action is bound to.
    pub observation_id: u64,
    /// `window` or `monitor`.
    pub target_type: String,
    /// The window handle that owns the physical point.
    pub hit_hwnd: usize,
    /// The root ancestor of `hit_hwnd`.
    pub hit_root_hwnd: usize,
    /// Virtual screen rectangle used to normalize absolute coordinates.
    pub virtual_screen: (i32, i32, i32, i32),
    /// False when the action is sent at the current pointer position, in which
    /// case the hit-test ownership check does not apply.
    pub hit_test_applied: bool,
}

/// Run the full shared guard for a pointer action.
///
/// `target` is required and is expressed in the coordinate system of the
/// observation. Monitor observations are supported: a monitor has no attached
/// window, so the window-identity, foreground, geometry and hit-test ownership
/// checks do not apply to it, exactly as in the original guarded click.
pub fn plan_pointer_action(
    attached: Option<&AttachedIdentity>,
    last_obs: Option<&LastObservation>,
    observation_id: u64,
    max_age_ms: u64,
    target: &FrameTarget,
) -> Result<PointerPlan, ProtocolError> {
    let age_ms = require_observation(last_obs, observation_id, max_age_ms)?;
    let obs = last_obs.expect("require_observation guarantees Some");

    let (center_frame_x, center_frame_y) = target.resolve(obs.width, obs.height)?;

    require_interactive_desktop()?;

    if obs.target_type == "monitor" {
        let screen_x = obs.bounds_x + center_frame_x;
        let screen_y = obs.bounds_y + center_frame_y;
        let virtual_screen = require_within_virtual_screen(screen_x, screen_y)?;
        let (hit_hwnd, hit_root_hwnd) = window_handles_at(screen_x, screen_y);
        return Ok(PointerPlan {
            screen_x,
            screen_y,
            age_ms,
            observation_id: obs.observation_id,
            target_type: obs.target_type.clone(),
            hit_hwnd,
            hit_root_hwnd,
            virtual_screen,
            hit_test_applied: false,
        });
    }

    let att = match attached {
        Some(a) => a,
        None => {
            return Err(ProtocolError::new("invalid_request", "No window target attached"));
        }
    };

    let target_hwnd = require_window_identity(att)?;
    require_foreground(att, target_hwnd)?;
    let cur_bounds = require_unchanged_geometry(att, target_hwnd, obs)?;

    let screen_x = cur_bounds.x + center_frame_x;
    let screen_y = cur_bounds.y + center_frame_y;

    require_hit_test_ownership(target_hwnd, screen_x, screen_y)?;
    let virtual_screen = require_within_virtual_screen(screen_x, screen_y)?;
    let (hit_hwnd, hit_root_hwnd) = window_handles_at(screen_x, screen_y);

    Ok(PointerPlan {
        screen_x,
        screen_y,
        age_ms,
        observation_id: obs.observation_id,
        target_type: obs.target_type.clone(),
        hit_hwnd,
        hit_root_hwnd,
        virtual_screen,
        hit_test_applied: true,
    })
}

/// Run the shared guard for a keyboard action.
///
/// Keyboard actions have no pointer target, so they bind to window identity and
/// foreground rather than to a point. An observation identifier is optional; when
/// one is supplied it is enforced exactly as it is for a pointer action, including
/// the geometry check, so that a keyboard decision made against a frame of a window
/// that has since moved is refused too.
pub fn plan_keyboard_action(
    attached: Option<&AttachedIdentity>,
    last_obs: Option<&LastObservation>,
    observation_id: Option<u64>,
    max_age_ms: u64,
) -> Result<KeyboardPlan, ProtocolError> {
    require_interactive_desktop()?;

    let att = match attached {
        Some(a) => a,
        None => {
            return Err(ProtocolError::new("invalid_request", "No window target attached"));
        }
    };

    let target_hwnd = require_window_identity(att)?;
    require_foreground(att, target_hwnd)?;

    let mut age_ms = None;
    if let Some(req_id) = observation_id {
        let age = require_observation(last_obs, req_id, max_age_ms)?;
        let obs = last_obs.expect("require_observation guarantees Some");
        require_unchanged_geometry(att, target_hwnd, obs)?;
        age_ms = Some(age);
    }

    Ok(KeyboardPlan {
        hwnd: att.hwnd_num,
        pid: att.pid,
        observation_id,
        age_ms,
    })
}

/// The result of running the shared guard for a keyboard action.
#[derive(Debug, Clone)]
pub struct KeyboardPlan {
    /// The window the keystrokes will be delivered to.
    pub hwnd: usize,
    /// The process that owns it.
    pub pid: u32,
    /// The observation the action was bound to, if any.
    pub observation_id: Option<u64>,
    /// Age of that observation, if one was supplied.
    pub age_ms: Option<u64>,
}

/// The window handle and its root ancestor at a physical point.
pub fn window_handles_at(screen_x: i32, screen_y: i32) -> (usize, usize) {
    let hwnd_at_pt = unsafe { WindowFromPoint(POINT { x: screen_x, y: screen_y }) };
    let root_at_pt = unsafe { GetAncestor(hwnd_at_pt, GA_ROOT) };
    (hwnd_at_pt.0 as usize, root_at_pt.0 as usize)
}
