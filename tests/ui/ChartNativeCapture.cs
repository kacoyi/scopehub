// Capture only the synthetic app's client area. Never fall back to copying the desktop.
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;

public static class ChartNativeCapture {
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

    public static void VerifyOwnedDialog(IntPtr app, IntPtr dialog) {
        for (IntPtr p=dialog; p!=IntPtr.Zero; p=GetWindow(p,4)) {
            if (p==app || IsChild(app,p)) return;
        }
        throw new InvalidOperationException("The file dialog belongs to another window.");
    }
    public static void Hover(IntPtr app, int x, int y) {
        SetForegroundWindow(app);
        IntPtr hit=WindowFromPoint(new Point(x,y));
        if (hit != app && !IsChild(app,hit)) throw new InvalidOperationException("Point is outside the test app.");
        if (!SetCursorPos(x,y)) throw new InvalidOperationException("Native cursor move failed.");
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

    public static Rectangle[] Rings(Bitmap chart) {
        // The two guest rings are purple. Work with exact core pixels and keep distinct x columns together.
        var groups = new System.Collections.Generic.List<Rectangle>();
        int start=-1, top=chart.Height, bottom=-1;
        for (int x=0;x<=chart.Width;x++) {
            bool colored=false;
            if (x<chart.Width) for (int y=0;y<chart.Height;y++) {
                var c=chart.GetPixel(x,y);
                if (c.R != 168 || c.G != 164 || c.B != 252) continue;
                colored=true; top=Math.Min(top,y); bottom=Math.Max(bottom,y);
            }
            if (colored && start<0) start=x;
            if (!colored && start>=0) {
                if (bottom-top>=5 && bottom-top<=25 && x-start>=6 && x-start<=26)
                    groups.Add(Rectangle.FromLTRB(start,top,x,bottom+1));
                start=-1; top=chart.Height; bottom=-1;
            }
        }
        return groups.ToArray();
    }
    public static bool Candle(Bitmap chart, Rectangle ring, bool rising) {
        Color expected=rising ? Color.FromArgb(67,212,172) : Color.FromArgb(241,125,133);
        int cx=ring.Left+ring.Width/2, cy=ring.Top+ring.Height/2;
        for (int y=cy-2;y<=cy+2;y++) for(int x=cx-2;x<=cx+2;x++)
            if (chart.GetPixel(x,y).ToArgb()==expected.ToArgb()) return true;
        return false;
    }
}
