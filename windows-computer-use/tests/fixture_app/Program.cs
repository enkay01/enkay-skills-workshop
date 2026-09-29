using System;
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
    static void Main()
    {
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

            System.IO.File.WriteAllText("fixture_log.txt", $"NewThread hDesk: {hDesk}, setOk: {setOk}, err: {err}\n");

            ApplicationConfiguration.Initialize();
            Application.Run(new Form1());
        });

        thread.SetApartmentState(System.Threading.ApartmentState.STA);
        thread.Start();
        thread.Join();
    }
}