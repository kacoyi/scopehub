// Capture only the synthetic app's client area. Never fall back to copying the desktop.
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;

public static class DrawingWindowCapture {
    [StructLayout(LayoutKind.Sequential)]
    private struct Rect { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] private static extern bool GetClientRect(IntPtr window, out Rect rect);
    [DllImport("user32.dll")] private static extern bool ClientToScreen(IntPtr window, ref Point point);
    [DllImport("user32.dll")] private static extern bool PrintWindow(IntPtr window, IntPtr dc, uint flags);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] private static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int width, int height, uint flags);

    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
    [DllImport("user32.dll")] private static extern IntPtr GetWindow(IntPtr window, uint command);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern IntPtr WindowFromPoint(Point point);
    [DllImport("user32.dll")] private static extern bool IsChild(IntPtr parent, IntPtr child);
    [DllImport("user32.dll")] private static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] private static extern void mouse_event(uint flags, uint x, uint y, uint data, UIntPtr extra);

    public static void VerifyPicker(IntPtr app, IntPtr picker, uint expectedPid) {
        uint pid;
        GetWindowThreadProcessId(picker, out pid);
        if (pid != expectedPid || (GetWindow(picker, 4) != app && !IsChild(app, picker)))
            throw new InvalidOperationException("Folder picker is not owned by the synthetic window.");
    }

    public static void FocusPicker(IntPtr picker) {
        SetForegroundWindow(picker);
        if (GetForegroundWindow() != picker)
            throw new InvalidOperationException("Could not focus the owned folder picker.");
    }

    private delegate bool Visit(IntPtr child, IntPtr data);
    [DllImport("user32.dll")] private static extern bool EnumChildWindows(IntPtr parent, Visit visit, IntPtr data);
    [DllImport("user32.dll")] private static extern int GetDlgCtrlID(IntPtr child);
    [DllImport("user32.dll")] private static extern IntPtr GetParent(IntPtr child);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetClassName(IntPtr child, System.Text.StringBuilder name, int size);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern IntPtr SendMessageTimeout(IntPtr child, uint msg, UIntPtr wp, string lp, uint flags, uint timeout, out UIntPtr result);
    [DllImport("user32.dll")] private static extern bool PostMessage(IntPtr child, uint msg, IntPtr wp, IntPtr lp);

    public static bool SetPickerFolder(IntPtr picker, string folder) {
        IntPtr input = IntPtr.Zero;
        EnumChildWindows(picker, delegate(IntPtr child, IntPtr ignored) {
            var name = new System.Text.StringBuilder(64);
            GetClassName(child, name, name.Capacity);
            if (name.ToString() == "Edit") {
                for (IntPtr ancestor = child; ancestor != picker && ancestor != IntPtr.Zero; ancestor = GetParent(ancestor)) {
                    int id = GetDlgCtrlID(ancestor);
                    if (id == 1148 || id == 1152) { input = child; return false; }
                }
            }
            return true;
        }, IntPtr.Zero);
        if (input == IntPtr.Zero) return false;
        UIntPtr result;
        return SendMessageTimeout(input, 12, UIntPtr.Zero, folder, 2, 2000, out result) != IntPtr.Zero && result != UIntPtr.Zero;
    }

    public static bool PressPickerOk(IntPtr picker) {
        IntPtr button = IntPtr.Zero;
        EnumChildWindows(picker, delegate(IntPtr child, IntPtr ignored) {
            if (GetDlgCtrlID(child) != 1) return true;
            var name = new System.Text.StringBuilder(64);
            GetClassName(child, name, name.Capacity);
            if (name.ToString() != "Button") return true;
            button = child; return false;
        }, IntPtr.Zero);
        return button != IntPtr.Zero && PostMessage(button, 0x00F5, IntPtr.Zero, IntPtr.Zero);
    }

    public static void ClickPickerControl(IntPtr picker, double x, double y) {
        Point point = new Point((int)Math.Round(x), (int)Math.Round(y));
        IntPtr hit = WindowFromPoint(point);
        if (GetForegroundWindow() != picker || (hit != picker && !IsChild(picker, hit)))
            throw new InvalidOperationException("Picker confirmation is outside the owned dialog.");
        if (!SetCursorPos(point.X, point.Y)) throw new InvalidOperationException("Cannot point at picker confirmation.");
        mouse_event(2, 0, 0, 0, UIntPtr.Zero);
        mouse_event(4, 0, 0, 0, UIntPtr.Zero);
    }

    public static void Resize(IntPtr window, int width, int height) {
        if (!SetWindowPos(window, IntPtr.Zero, 0, 0, width, height, 0x16))
            throw new InvalidOperationException("Could not resize the test window.");
    }

    public static Bitmap Plot(IntPtr window, double left, double top, double width, double height) {
        Rect client;
        Point origin = new Point(0, 0);
        if (!GetClientRect(window, out client) || !ClientToScreen(window, ref origin))
            throw new InvalidOperationException("Client bounds unavailable.");
        Rectangle crop = Rectangle.FromLTRB((int)Math.Floor(left) - origin.X, (int)Math.Floor(top) - origin.Y,
            (int)Math.Ceiling(left + width) - origin.X, (int)Math.Ceiling(top + height) - origin.Y);
        using (Bitmap content = new Bitmap(client.Right, client.Bottom)) {
            if (!new Rectangle(0, 0, content.Width, content.Height).Contains(crop))
                throw new InvalidOperationException("Drawing exceeds its owning client rectangle.");
            using (Graphics graphics = Graphics.FromImage(content)) {
                IntPtr dc = graphics.GetHdc();
                try {
                    if (!PrintWindow(window, dc, 3)) throw new InvalidOperationException("Window capture failed.");
                } finally { graphics.ReleaseHdc(dc); }
            }
            return content.Clone(crop, PixelFormat.Format24bppRgb);
        }
    }

    public static int[] InkExtent(Bitmap plot, double logicalY, Color ink, int tolerance) {
        double scale = plot.Width / 640.0;
        int y0 = Math.Max(0, (int)((logicalY - tolerance) * scale));
        int y1 = Math.Min(plot.Height - 1, (int)Math.Ceiling((logicalY + tolerance) * scale));
        int left = plot.Width, right = -1, count = 0;
        for (int y = y0; y <= y1; y++) {
            for (int x = 0; x < plot.Width; x++) {
                Color c = plot.GetPixel(x, y);
                if (Math.Abs(c.R - ink.R) > 16 || Math.Abs(c.G - ink.G) > 16 || Math.Abs(c.B - ink.B) > 16) continue;
                left = Math.Min(left, x); right = Math.Max(right, x); count++;
            }
        }
        return new int[] {left, right, count};
    }
}
