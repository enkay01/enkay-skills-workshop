"""Client timeout-cleanup and response-validation tests.

The delivery spec requires that a stalled engine response produces a bounded
cleanup: no recursive shutdown requests, no stale response reuse, and no
hidden orphan processes. These tests use controlled fake engine peers that
stall or answer with a mismatched id, so the failure modes are deterministic
and do not require a real engine hang.

The fake engines are tiny C# programs compiled to real executables with the
.NET Framework compiler, because ``WcuClient`` launches its engine as a
single executable.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
client_dir = os.path.join(root_dir, "client")
if client_dir not in sys.path:
    sys.path.insert(0, client_dir)

from wcu_client import WcuClient, WcuError  # noqa: E402

CSC = os.path.join(
    os.environ.get("WINDIR", r"C:\Windows"),
    "Microsoft.NET",
    "Framework64",
    "v4.0.30319",
    "csc.exe",
)

STALL_CS = r"""
using System;
using System.IO;
using System.Threading;
class P {
    static void Main() {
        var stdin = Console.OpenStandardInput();
        byte[] lenBytes = new byte[4];
        if (stdin.Read(lenBytes, 0, 4) == 4) {
            uint n = BitConverter.ToUInt32(lenBytes, 0);
            byte[] buf = new byte[n];
            int off = 0;
            while (off < n) {
                int r = stdin.Read(buf, off, (int)n - off);
                if (r <= 0) break;
                off += r;
            }
        }
        // Consume the request, then stall forever without responding.
        System.Threading.Thread.Sleep(Timeout.Infinite);
    }
}
"""

BAD_ID_CS = r"""
using System;
using System.IO;
using System.Text;
using System.Threading;
class P {
    static void Main() {
        var stdin = Console.OpenStandardInput();
        byte[] lenBytes = new byte[4];
        stdin.Read(lenBytes, 0, 4);
        uint n = BitConverter.ToUInt32(lenBytes, 0);
        byte[] buf = new byte[n];
        int off = 0;
        while (off < n) {
            int r = stdin.Read(buf, off, (int)n - off);
            if (r <= 0) break;
            off += r;
        }
        string header = Encoding.UTF8.GetString(buf, 0, off);
        long reqId = 0;
        int idx = header.IndexOf("\"id\":");
        if (idx >= 0) {
            int p = idx + 5;
            while (p < header.Length && header[p] == ' ') p++;
            long.TryParse(header.Substring(p).TrimEnd(',', '}', ' '), out reqId);
        }
        // Answer with a mismatched id: a stale/misrouted response.
        string resp = "{\"v\":1,\"id\":" + (reqId + 999) + ",\"ok\":true,\"result\":{},\"payload_len\":0}";
        byte[] respBytes = Encoding.UTF8.GetBytes(resp);
        byte[] frame = new byte[4 + respBytes.Length];
        BitConverter.GetBytes((uint)respBytes.Length).CopyTo(frame, 0);
        respBytes.CopyTo(frame, 4);
        var stdout = Console.OpenStandardOutput();
        stdout.Write(frame, 0, frame.Length);
        stdout.Flush();
        System.Threading.Thread.Sleep(30000);
    }
}
"""


def _pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == 259
    finally:
        kernel32.CloseHandle(handle)


class TestClientTimeoutCleanup(unittest.TestCase):
    tmpdir: str
    stall_exe: str
    badid_exe: str

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(CSC):
            raise unittest.SkipTest(f".NET Framework compiler not found at {CSC}")
        cls.tmpdir = tempfile.mkdtemp(prefix="wcu_fake_engine_")
        cls.stall_exe = cls._compile("stall", STALL_CS)
        cls.badid_exe = cls._compile("badid", BAD_ID_CS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    @classmethod
    def _compile(cls, name: str, source: str) -> str:
        src = os.path.join(cls.tmpdir, f"{name}.cs")
        exe = os.path.join(cls.tmpdir, f"{name}.exe")
        with open(src, "w", encoding="utf-8") as f:
            f.write(source)
        proc = subprocess.run(
            [CSC, "/nologo", "/target:exe", f"/out:{exe}", src],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0 or not os.path.exists(exe):
            raise RuntimeError(f"Failed to compile fake engine: {proc.stderr}")
        return exe

    def test_stalled_engine_times_out_with_bounded_cleanup(self):
        """A stalled engine produces a timeout, then bounded cleanup.

        The whole path -- operation timeout, the shutdown request, process
        termination -- must complete well under a few seconds, and the engine
        process must not survive as an orphan.
        """
        client = WcuClient(engine_path=self.stall_exe)
        client.start()
        first_pid = client.pid
        self.assertTrue(_pid_alive(first_pid))

        start = time.monotonic()
        with self.assertRaises(WcuError) as ctx:
            client.request("doctor", timeout_sec=0.5)
        elapsed = time.monotonic() - start

        self.assertEqual(ctx.exception.code, "timeout")
        # Bounded: 0.5s op timeout + ~1s shutdown + terminate, with margin.
        self.assertLess(elapsed, 5.0, f"timeout cleanup took {elapsed:.2f}s")
        # The client stopped the engine; no orphan process survives.
        self.assertFalse(
            _pid_alive(first_pid), "stalled engine process survived as an orphan"
        )
        self.assertIsNone(client.pid)

        # The client can be restarted cleanly after a timeout.
        client.start()
        second_pid = client.pid
        self.assertNotEqual(second_pid, first_pid)
        self.assertTrue(_pid_alive(second_pid))
        client.stop()
        self.assertFalse(_pid_alive(second_pid))

    def test_mismatched_response_id_is_rejected(self):
        """A response whose id does not match the request is refused.

        This is the stale-response guard: a late or misrouted response must
        never satisfy the current request.
        """
        client = WcuClient(engine_path=self.badid_exe)
        client.start()
        try:
            with self.assertRaises(WcuError) as ctx:
                client.request("doctor", timeout_sec=3.0)
            self.assertEqual(ctx.exception.code, "stale_response")
        finally:
            client.stop()


if __name__ == "__main__":
    unittest.main()
