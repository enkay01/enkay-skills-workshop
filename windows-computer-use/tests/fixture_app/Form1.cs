using System;
using System.Drawing;
using System.Windows.Forms;

namespace fixture_app;

public partial class Form1 : Form
{
    private Button btnContinue = null!;
    private TextBox txtInput = null!;
    private Label lblCounter = null!;
    private Label lblState = null!;
    private Label lblTick = null!;
    private System.Windows.Forms.Timer frameTimer = null!;
    private int actionCount = 0;

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern bool AllowSetForegroundWindow(int dwProcessId);

    public Form1()
    {
        InitializeComponent();
        SetupControls();
        AllowSetForegroundWindow(-1);
        this.TopMost = true;
        this.Shown += (s, e) =>
        {
            this.Activate();
            this.BringToFront();
            this.Focus();
        };
    }

    private void SetupControls()
    {
        this.Text = "WCU_Native_WinForms_Fixture";
        this.ClientSize = new Size(500, 350);
        this.StartPosition = FormStartPosition.Manual;
        this.Location = new Point(200, 200);

        btnContinue = new Button
        {
            Name = "btn_continue",
            AccessibleName = "Continue",
            Text = "Continue",
            Location = new Point(50, 40),
            Size = new Size(140, 45),
            Font = new Font("Segoe UI", 12F, FontStyle.Bold)
        };
        btnContinue.Click += (s, e) =>
        {
            actionCount++;
            lblCounter.Text = $"Counter: {actionCount}";
            lblState.Text = "State: Processing";
            var timer = new System.Windows.Forms.Timer { Interval = 150 };
            timer.Tick += (ts, te) =>
            {
                lblState.Text = "State: Ready";
                timer.Stop();
                timer.Dispose();
            };
            timer.Start();
        };

        txtInput = new TextBox
        {
            Name = "txt_input",
            Text = "Initial Text",
            Location = new Point(50, 110),
            Size = new Size(250, 35),
            Font = new Font("Segoe UI", 11F)
        };

        lblCounter = new Label
        {
            Name = "lbl_counter",
            Text = "Counter: 0",
            Location = new Point(50, 170),
            Size = new Size(250, 30),
            Font = new Font("Segoe UI", 11F)
        };

        lblState = new Label
        {
            Name = "lbl_state",
            Text = "State: Ready",
            Location = new Point(50, 220),
            Size = new Size(250, 30),
            Font = new Font("Segoe UI", 11F)
        };

        // Keep capture frames arriving while the model is deciding. The tick is
        // outside the Continue button, so target-region validation stays useful.
        lblTick = new Label
        {
            Name = "lbl_tick",
            Text = "Tick: 0",
            Location = new Point(340, 220),
            Size = new Size(100, 30),
        };
        var tick = 0;
        frameTimer = new System.Windows.Forms.Timer { Interval = 250 };
        frameTimer.Tick += (s, e) => lblTick.Text = $"Tick: {++tick}";
        frameTimer.Start();

        this.Controls.Add(btnContinue);
        this.Controls.Add(txtInput);
        this.Controls.Add(lblCounter);
        this.Controls.Add(lblState);
        this.Controls.Add(lblTick);
    }
}
