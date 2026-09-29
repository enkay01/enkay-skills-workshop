using System;
using System.Collections.Generic;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace fixture_app;

/// <summary>
/// Second fixture window, dedicated to the actions that were added beyond click.
///
/// Every surface here exists so that one action has one independent, observable
/// result. Each readout is a named control, so a test reads the application's own
/// reported state through the existing inspection operation rather than inferring
/// anything from a screenshot.
/// </summary>
public partial class ActionForm : Form
{
    private TextBox txtTarget = null!;
    private Label lblTyped = null!;
    private Label lblKeys = null!;
    private Panel pnlScroll = null!;
    private Panel pnlScrollContent = null!;
    private Label lblScroll = null!;
    private Label lblWheel = null!;
    private Panel pnlHover = null!;
    private Label lblHover = null!;
    private Panel pnlClick = null!;
    private Label lblRightClick = null!;
    private Label lblDoubleClick = null!;
    private Panel pnlArena = null!;
    private Panel pnlDragBox = null!;
    private Label lblDrop = null!;
    private System.Windows.Forms.Timer pulseTimer = null!;

    private bool dragging = false;
    private Point dragOffset = Point.Empty;
    private int moveCount = 0;

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern bool AllowSetForegroundWindow(int dwProcessId);

    public ActionForm()
    {
        InitializeComponent();
        SetupControls();
        AllowSetForegroundWindow(-1);
        // Deliberately not topmost: the engine refuses to inject while another
        // window is in front, so a window that fights for the top of the z-order
        // would make its own foreground state unreliable.
        this.Shown += (s, e) =>
        {
            this.Activate();
            this.BringToFront();
            this.Focus();
        };
    }

    private void SetupControls()
    {
        this.Text = "WCU_Native_Action_Fixture";
        this.ClientSize = new Size(640, 760);
        this.StartPosition = FormStartPosition.Manual;
        this.Location = new Point(760, 60);

        AddHeading("Typing target", 8);
        txtTarget = new TextBox
        {
            Name = "txt_target",
            Text = string.Empty,
            Location = new Point(20, 28),
            Size = new Size(600, 28),
            Font = new Font("Segoe UI", 11F)
        };
        txtTarget.TextChanged += (s, e) => lblTyped.Text = $"Typed: {txtTarget.Text}";
        lblTyped = MakeReadout("lbl_typed", "Typed: (empty)", 62);
        this.Controls.Add(txtTarget);
        this.Controls.Add(lblTyped);

        AddHeading("Key chords", 96);
        lblKeys = MakeReadout("lbl_keys", "Keys: (none)", 116);
        this.Controls.Add(lblKeys);

        AddHeading("Scrollable region", 150);
        pnlScroll = new Panel
        {
            Name = "pnl_scroll",
            Location = new Point(20, 170),
            Size = new Size(600, 170),
            BorderStyle = BorderStyle.FixedSingle,
            AutoScroll = true,
            BackColor = Color.White
        };
        // Wider and taller than the viewport on both axes, so that both vertical
        // and horizontal scrolling have somewhere real to go.
        pnlScrollContent = new Panel
        {
            Name = "pnl_scroll_content",
            Location = new Point(0, 0),
            Size = new Size(1200, 900),
            BackColor = Color.FromArgb(240, 244, 248),
            // Transparent to the mouse, so the wheel is not consumed by a child
            // that cannot scroll.
            Enabled = false
        };
        pnlScrollContent.Controls.Add(Caption(
            "lbl_scroll_content_text",
            "Content below the fold\r\n\r\nLine 2\r\nLine 3\r\nLine 4\r\nLine 5\r\nLine 6\r\nLine 7\r\nLine 8\r\nLine 9\r\nLine 10",
            new Font("Segoe UI", 10F)));
        pnlScroll.Controls.Add(pnlScrollContent);
        pnlScroll.Scroll += (s, e) => UpdateScrollReadout();
        lblScroll = MakeReadout("lbl_scroll", "Scroll: v=0 h=0", 348, 20, 290);
        lblWheel = MakeReadout("lbl_wheel", "Wheel: (none)", 348, 330, 300);
        this.Controls.Add(pnlScroll);
        this.Controls.Add(lblScroll);
        this.Controls.Add(lblWheel);

        AddHeading("Hover surface", 384);
        pnlHover = new Panel
        {
            Name = "pnl_hover",
            Location = new Point(20, 404),
            Size = new Size(280, 84),
            BackColor = Color.FromArgb(222, 232, 245),
            BorderStyle = BorderStyle.FixedSingle
        };
        pnlHover.Controls.Add(Caption("lbl_hover_caption", "Hover me", new Font("Segoe UI", 10F)));
        pnlHover.MouseEnter += (s, e) => lblHover.Text = "Hover: entered";
        pnlHover.MouseLeave += (s, e) => lblHover.Text = "Hover: left";
        lblHover = MakeReadout("lbl_hover", "Hover: (none)", 494, 20, 200);
        this.Controls.Add(pnlHover);
        this.Controls.Add(lblHover);

        AddHeading("Click variants", 384, 320);
        pnlClick = new Panel
        {
            Name = "pnl_click",
            Location = new Point(320, 404),
            Size = new Size(300, 84),
            BackColor = Color.FromArgb(245, 238, 222),
            BorderStyle = BorderStyle.FixedSingle
        };
        pnlClick.Controls.Add(Caption("lbl_click_caption", "Right-click and double-click me", new Font("Segoe UI", 10F)));
        pnlClick.MouseDown += (s, e) =>
        {
            if (e.Button == MouseButtons.Right)
            {
                lblRightClick.Text = "RightClick: 1";
            }
        };
        pnlClick.DoubleClick += (s, e) => lblDoubleClick.Text = "DoubleClick: 1";
        lblRightClick = MakeReadout("lbl_rightclick", "RightClick: 0", 494, 230, 200);
        lblDoubleClick = MakeReadout("lbl_doubleclick", "DoubleClick: 0", 494, 440, 190);
        this.Controls.Add(pnlClick);
        this.Controls.Add(lblRightClick);
        this.Controls.Add(lblDoubleClick);

        AddHeading("Draggable element", 528);
        pnlArena = new Panel
        {
            Name = "pnl_arena",
            Location = new Point(20, 548),
            Size = new Size(600, 150),
            BackColor = Color.FromArgb(236, 236, 236),
            BorderStyle = BorderStyle.FixedSingle
        };
        pnlDragBox = new Panel
        {
            Name = "pnl_dragbox",
            Location = new Point(16, 16),
            Size = new Size(120, 100),
            BackColor = Color.FromArgb(120, 170, 220),
            BorderStyle = BorderStyle.Fixed3D
        };
        pnlDragBox.Controls.Add(Caption("lbl_dragbox_caption", "Drag me", new Font("Segoe UI", 9F)));
        pnlDragBox.MouseDown += OnDragBoxMouseDown;
        pnlDragBox.MouseMove += OnDragBoxMouseMove;
        pnlDragBox.MouseUp += OnDragBoxMouseUp;
        pnlArena.Controls.Add(pnlDragBox);
        lblDrop = MakeReadout("lbl_drop", "Drop: (none)", 704);
        this.Controls.Add(pnlArena);
        this.Controls.Add(lblDrop);

        // Keeps capture frames arriving so an observation can always be fresh.
        pulseTimer = new System.Windows.Forms.Timer { Interval = 250 };
        pulseTimer.Start();
    }

    private void AddHeading(string text, int y, int x = 20)
    {
        this.Controls.Add(new Label
        {
            Name = "lbl_heading_" + y + "_" + x,
            Text = text,
            Location = new Point(x, y),
            Size = new Size(300, 18),
            Font = new Font("Segoe UI", 9F, FontStyle.Bold),
            Enabled = false
        });
    }

    /// <summary>
    /// A caption that the mouse passes straight through.
    ///
    /// A WinForms Label is an opaque control: left enabled and filling its
    /// parent it swallows MouseDown and MouseMove, so the panel underneath never
    /// sees the gesture. Disabling it lets the input reach the surface the test
    /// is actually aiming at.
    /// </summary>
    private static Label Caption(string name, string text, Font font)
    {
        return new Label
        {
            Name = name,
            Text = text,
            Dock = DockStyle.Fill,
            TextAlign = ContentAlignment.MiddleCenter,
            Font = font,
            Enabled = false
        };
    }

    private Label MakeReadout(string name, string text, int y, int x = 20, int w = 600)
    {
        return new Label
        {
            Name = name,
            Text = text,
            Location = new Point(x, y),
            Size = new Size(w, 20),
            Font = new Font("Consolas", 9.5F)
        };
    }

    private void UpdateScrollReadout()
    {
        lblScroll.Text = $"Scroll: v={pnlScroll.VerticalScroll.Value} h={pnlScroll.HorizontalScroll.Value}";
    }

    // -------------------------------------------------------------------
    // Wheel input
    // -------------------------------------------------------------------

    // WM_MOUSEHWHEEL is 0x020E, adjacent to WM_MOUSEWHEEL (0x020A) but not
    // 0x023E. Getting this wrong silently drops every horizontal wheel event.
    private const int WM_MOUSEWHEEL = 0x020A;
    private const int WM_MOUSEHWEEL = 0x020E;
    private const int WM_APP_WHEEL_V = 0x8001;
    private const int WM_APP_WHEEL_H = 0x8002;

    // Focus-lock control. Only this process may lock the foreground, and only
    // while it owns it, so the request comes in as a message on this window's own
    // thread rather than from the test runner.
    private const int WM_APP_LOCK_FOREGROUND = 0x8003;
    private const int WM_APP_UNLOCK_FOREGROUND = 0x8004;
    private const int WM_CANCELMODE = 0x001F;
    private const int WM_ACTIVATE = 0x0006;
    private const int WA_INACTIVE = 0;

    private bool foregroundLocked;

    private const uint LSFW_LOCK = 1;
    private const uint LSFW_UNLOCK = 2;
    private const int WHEEL_DELTA = 120;

    [StructLayout(LayoutKind.Sequential)]
    private struct MouseHookData
    {
        public Point pt;
        public uint mouseData;
        public uint flags;
        public uint time;
        public IntPtr extraInfo;
    }

    private delegate IntPtr LowLevelMouseProc(int nCode, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr SetWindowsHookEx(int idHook, LowLevelMouseProc lpfn, IntPtr hMod, uint dwThreadId);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool UnhookWindowsHookEx(IntPtr hhk);

    [DllImport("user32.dll")]
    private static extern IntPtr CallNextHookEx(IntPtr hhk, int nCode, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool PostMessage(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);

    // Held in fields: a low-level hook delegate that gets collected is a crash.
    private LowLevelMouseProc? wheelHookProc;
    private IntPtr wheelHook = IntPtr.Zero;

    private const int WH_MOUSE_LL = 14;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr GetModuleHandle(string? lpModuleName);

    private void StartWheelHook()
    {
        wheelHookProc = WheelHook;
        wheelHook = SetWindowsHookEx(
            WH_MOUSE_LL, wheelHookProc, GetModuleHandle(null), 0);
    }

    private void StopWheelHook()
    {
        if (wheelHook != IntPtr.Zero)
        {
            UnhookWindowsHookEx(wheelHook);
            wheelHook = IntPtr.Zero;
        }
    }

    /// <summary>
    /// Observes wheel notches system-wide and hands them to this form when the
    /// pointer is over the scrollable region.
    ///
    /// Windows routes WM_MOUSEWHEEL to whichever control holds keyboard focus
    /// rather than to the one under the pointer, so reading the offset from a
    /// window message would make the reported value depend on where the caret
    /// happened to be. A low-level hook sees the wheel input itself, which makes
    /// the scroll offset a direct consequence of the notches the tool dispatched.
    /// The work is posted to the form so no control is touched inside the hook.
    /// </summary>
    private IntPtr WheelHook(int nCode, IntPtr wParam, IntPtr lParam)
    {
        int message = wParam.ToInt32();
        if (nCode >= 0 && IsOverScrollRegion()
            && (message == WM_MOUSEWHEEL || message == WM_MOUSEHWEEL))
        {
            object? raw = Marshal.PtrToStructure(lParam, typeof(MouseHookData));
            if (raw is not MouseHookData data)
            {
                return CallNextHookEx(wheelHook, nCode, wParam, lParam);
            }
            short delta = unchecked((short)((data.mouseData >> 16) & 0xFFFF));
            string axis = message == WM_MOUSEHWEEL ? "h" : "v";
            lblWheel.Text = $"Wheel: delta={delta} axis={axis}";
            int target = message == WM_MOUSEHWEEL ? WM_APP_WHEEL_H : WM_APP_WHEEL_V;
            PostMessage(Handle, target, (IntPtr)delta, IntPtr.Zero);
        }
        return CallNextHookEx(wheelHook, nCode, wParam, lParam);
    }

    private bool IsOverScrollRegion()
    {
        if (pnlScroll == null || !Visible) return false;
        Point cursor = PointToClient(Cursor.Position);
        return pnlScroll.Bounds.Contains(cursor);
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool LockSetForegroundWindow(uint dwCode);

    [DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    private static extern bool EndMenu();

    [DllImport("user32.dll")]
    private static extern IntPtr SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);

    /// <summary>
    /// Take the foreground lock, so that no other process can take the foreground.
    /// </summary>
    /// <returns>
    /// 1 if this window both owned the foreground and locked it, 0 otherwise. The
    /// refusal cases are reported rather than hidden: a lock this process is not
    /// entitled to take, or that did not leave this window in front, is not a
    /// precondition the test may rely on.
    /// </returns>
    /// <remarks>
    /// This window holds the foreground, so it is the one process entitled to lock
    /// it. Asking a separate process to activate a window and lock it instead is
    /// not workable: SetForegroundWindow has eligibility conditions that a freshly
    /// launched process does not meet, so the activation never succeeds.
    /// </remarks>
    private int LockForegroundForTest()
    {
        // An open menu is a documented bar to foreground changes, so any menu
        // mode is cancelled before locking. A right-click elsewhere in the suite
        // can leave this state behind.
        EndMenu();
        SendMessage(Handle, WM_CANCELMODE, IntPtr.Zero, IntPtr.Zero);

        if (GetForegroundWindow() != Handle)
        {
            RecordFocusLock("lock=False reason=not-foreground");
            return 0;
        }

        // Windows grants foreground eligibility to the process that injected the
        // most recent input, and a foreground lock does not remove that
        // eligibility. The engine injects input throughout the test suite, so
        // without this the engine would still be able to take the foreground, this
        // window would be deactivated, and losing the foreground would release
        // the lock. The refusal under test would then have no cause.
        //
        // One relative mouse movement of zero distance makes this process, which
        // already owns the foreground, the most recent input receiver instead. It
        // moves nothing and touches no keyboard, button or text state.
        if (!ResetInputForFocusTest())
        {
            RecordFocusLock("lock=False reason=input-reset-failed");
            return 0;
        }

        bool locked = LockSetForegroundWindow(LSFW_LOCK);
        if (!locked)
        {
            RecordFocusLock($"lock=False reason=api-refused err={Marshal.GetLastWin32Error()}");
            return 0;
        }

        // Confirm the lock is in force by observing the effect, rather than
        // trusting the return value alone.
        if (GetForegroundWindow() != Handle)
        {
            LockSetForegroundWindow(LSFW_UNLOCK);
            RecordFocusLock("lock=False reason=foreground-changed");
            return 0;
        }

        foregroundLocked = true;
        RecordFocusLock("lock=True");
        return 1;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ProbeMouseInput
    {
        public int dx, dy;
        public uint mouseData, flags, time;
        public UIntPtr extraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ProbeInput
    {
        public uint type;
        public ProbeMouseInput mouse;
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern uint SendInput(uint count, ProbeInput[] inputs, int size);

    /// <summary>
    /// Make this process the most recent input receiver, so that the foreground
    /// lock below is not defeated by another process's earlier injections.
    /// </summary>
    /// <returns>Whether Windows accepted the event, checked rather than assumed.</returns>
    private static bool ResetInputForFocusTest()
    {
        // MOUSEEVENTF_MOVE with zero dx and dy: relative movement of no distance.
        var inputs = new[] { new ProbeInput { mouse = new ProbeMouseInput { flags = 1 } } };
        return SendInput(1, inputs, Marshal.SizeOf<ProbeInput>()) == 1;
    }

    /// <summary>Release the foreground lock so later tests can focus windows.</summary>
    private int UnlockForegroundForTest()
    {
        bool unlocked = LockSetForegroundWindow(LSFW_UNLOCK);
        foregroundLocked = false;
        RecordFocusLock($"unlock={unlocked}");
        return unlocked ? 1 : 0;
    }

    /// <summary>
    /// Report the outcome beside the executable. A run that fails this way leaves
    /// the reason behind, which is otherwise invisible from outside the process.
    /// </summary>
    private static void RecordFocusLock(string message)
    {
        try
        {
            string path = System.IO.Path.Combine(
                AppContext.BaseDirectory,
                $"action_focus_lock_{System.Diagnostics.Process.GetCurrentProcess().Id}.txt");
            System.IO.File.AppendAllText(
                path,
                message + " foreground=" + GetForegroundWindow() + Environment.NewLine);
        }
        catch (Exception)
        {
            // Diagnostic only: failing to record is not a test failure.
        }
    }

    protected override void WndProc(ref Message m)
    {
        // A foreground lock is released automatically if this window stops being
        // the foreground window, so the holder watches for that: it is the one
        // thing that silently invalidates the precondition a focus test relies on.
        if (m.Msg == WM_ACTIVATE && m.WParam.ToInt32() == WA_INACTIVE && foregroundLocked)
        {
            RecordFocusLock("lost-foreground");
            foregroundLocked = false;
        }

        if (m.Msg == WM_APP_LOCK_FOREGROUND || m.Msg == WM_APP_UNLOCK_FOREGROUND)
        {
            // WndProc runs on this window's UI thread, which is what
            // LockSetForegroundWindow requires: only the process that owns the
            // foreground may lock or unlock it.
            m.Result = (IntPtr)(m.Msg == WM_APP_LOCK_FOREGROUND
                ? LockForegroundForTest()
                : UnlockForegroundForTest());
            return;
        }

        if (m.Msg == WM_APP_WHEEL_V || m.Msg == WM_APP_WHEEL_H)
        {
            int notches = m.WParam.ToInt32() / WHEEL_DELTA;
            if (m.Msg == WM_APP_WHEEL_V)
            {
                // A positive vertical delta is a tilt up, which scrolls the
                // content up: the offset decreases.
                pnlScroll.VerticalScroll.Value = pnlScroll.VerticalScroll.Value - notches * 3;
            }
            else
            {
                // A positive horizontal delta is a tilt right, which scrolls the
                // content right: the offset increases.
                pnlScroll.HorizontalScroll.Value = pnlScroll.HorizontalScroll.Value + notches * 3;
            }
            UpdateScrollReadout();
        }
        base.WndProc(ref m);
    }

    protected override void OnShown(EventArgs e)
    {
        base.OnShown(e);
        StartWheelHook();
    }

    protected override void OnFormClosed(FormClosedEventArgs e)
    {
        StopWheelHook();
        base.OnFormClosed(e);
    }

    // -------------------------------------------------------------------
    // Key chord readout
    // -------------------------------------------------------------------

    /// <summary>
    /// Records the last recognised key chord from the application's own keyboard
    /// state. Recording happens on key-down only, so one chord records once.
    /// </summary>
    protected override bool ProcessCmdKey(ref Message msg, Keys keyData)
    {
        const int WM_KEYDOWN = 0x0100;
        const int WM_SYSKEYDOWN = 0x0104;
        if (msg.Msg == WM_KEYDOWN || msg.Msg == WM_SYSKEYDOWN)
        {
            lblKeys.Text = "Keys: " + DescribeKey(keyData);
        }
        return base.ProcessCmdKey(ref msg, keyData);
    }

    private static string DescribeKey(Keys keyData)
    {
        var parts = new List<string>();
        if ((keyData & Keys.Control) != 0) parts.Add("Ctrl");
        if ((keyData & Keys.Alt) != 0) parts.Add("Alt");
        if ((keyData & Keys.Shift) != 0) parts.Add("Shift");

        Keys code = keyData & (Keys.KeyCode | Keys.Modifiers);
        string name = code switch
        {
            Keys.Return => "Enter",
            Keys.Escape => "Escape",
            Keys.Back => "Backspace",
            Keys.Delete => "Delete",
            Keys.Insert => "Insert",
            Keys.Space => "Space",
            Keys.PageUp => "PageUp",
            Keys.PageDown => "PageDown",
            Keys.Capital => "CapsLock",
            Keys.Up => "Up",
            Keys.Down => "Down",
            Keys.Left => "Left",
            Keys.Right => "Right",
            _ => code.ToString()
        };
        parts.Add(name);
        return string.Join("+", parts);
    }

    // -------------------------------------------------------------------
    // Drag handling
    // -------------------------------------------------------------------

    private void OnDragBoxMouseDown(object? sender, MouseEventArgs e)
    {
        if (e.Button != MouseButtons.Left) return;
        dragging = true;
        moveCount = 0;
        dragOffset = new Point(e.X, e.Y);
        pnlDragBox.Capture = true;
    }

    private void OnDragBoxMouseMove(object? sender, MouseEventArgs e)
    {
        if (!dragging) return;
        moveCount++;

        int x = e.X - dragOffset.X;
        int y = e.Y - dragOffset.Y;

        // Keep the element inside the arena so the recorded drop is unambiguous.
        int maxX = Math.Max(0, pnlArena.ClientSize.Width - pnlDragBox.Width);
        int maxY = Math.Max(0, pnlArena.ClientSize.Height - pnlDragBox.Height);
        pnlDragBox.Location = new Point(
            Math.Clamp(x, 0, maxX),
            Math.Clamp(y, 0, maxY)
        );
    }

    private void OnDragBoxMouseUp(object? sender, MouseEventArgs e)
    {
        if (!dragging) return;
        dragging = false;
        pnlDragBox.Capture = false;
        lblDrop.Text = $"Drop: {pnlDragBox.Left}, {pnlDragBox.Top} moves={moveCount}";
    }
}
