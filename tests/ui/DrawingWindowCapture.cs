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
