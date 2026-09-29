using System;
using System.Linq;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace fixture_app;

static class Program
{
    [DllImport("user32.dll", SetLastError = true)]
    static extern IntPtr OpenInputDesktop(uint dwFlags, bool fInherit, uint dwDesiredAccess);

    [DllImport("user32.dll", SetLastError = true)]
    static extern bool SetThreadDesktop(IntPtr hDesktop);

    [STAThread]
    static void Main(string[] args)
    {
        // The window is selected at startup so the existing fixture window, its
        // title, and its recognition profile are untouched.
        bool useActionForm = args != null && args.Any(a =>
            a.Equals("--actions", StringComparison.OrdinalIgnoreCase));

        IntPtr hDesk = OpenInputDesktop(0, false, 0x01FF);

        var thread = new System.Threading.Thread(() =>
        {
            bool setOk = false;
            int err = 0;
            if (hDesk != IntPtr.Zero)
            {
                setOk = SetThreadDesktop(hDesk);
                err = Marshal.GetLastWin32Error();
            }

            // Diagnostic only. Two fixture windows can now run at the same time,
            // so this must neither share a path with the other process nor be
            // able to take the window down with it. Written beside the built
            // executable rather than the source tree, so repeated runs do not
            // litter the repository.
            try
            {
                string tag = useActionForm ? "actions" : "primary";
                System.IO.File.WriteAllText(
                    System.IO.Path.Combine(
                        AppContext.BaseDirectory,
                        $"fixture_log_{tag}_{System.Diagnostics.Process.GetCurrentProcess().Id}.txt"),
                    $"NewThread hDesk: {hDesk}, setOk: {setOk}, err: {err}\n");
            }
            catch (Exception)
            {
                // Ignore: a diagnostic that fails is not a test failure.
            }

            ApplicationConfiguration.Initialize();
            Application.Run(useActionForm ? new ActionForm() : (Form)new Form1());
        });

        thread.SetApartmentState(System.Threading.ApartmentState.STA);
        thread.Start();
        thread.Join();
    }
}