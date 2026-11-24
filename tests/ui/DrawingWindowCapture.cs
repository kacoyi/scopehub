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
                throw new InvalidOperationException("Price overlay exceeds its owning client rectangle.");
            using (Graphics graphics = Graphics.FromImage(content)) {
                IntPtr dc = graphics.GetHdc();
                try {
                    if (!PrintWindow(window, dc, 3)) throw new InvalidOperationException("Window capture failed.");
                } finally { graphics.ReleaseHdc(dc); }
            }
            return content.Clone(crop, PixelFormat.Format24bppRgb);
        }
    }

    // Prices come from the pinned two-bar futures history. The axis projects to logical y=24..208.
    // Gold poles can be partly overpainted by green flags, so measure the visible center and bottom.
    public static double[] Poles(Bitmap plot, bool expanded) {
        double scale = plot.Width / 640.0;
        if (plot.Width < 350 || Math.Abs(plot.Height - 240 * scale) > 3)
            throw new InvalidOperationException("Incorrect painted plot aspect ratio.");
        double[] result = new double[4];
        double low = expanded ? 3497.7 : 3498.6;
        double high = expanded ? 3501.3 : 3500.4;
        for (int i = 0; i < 2; ++i) {
            int min = plot.Width, max = -1, bottom = -1;
            int start = i * plot.Width / 2, end = (i + 1) * plot.Width / 2;
            for (int x = start; x < end; ++x) {
                for (int y = 0; y < plot.Height; ++y) {
                    Color c = plot.GetPixel(x, y);
                    if (Math.Abs(c.R - 232) > 12 || Math.Abs(c.G - 175) > 12 || Math.Abs(c.B - 114) > 12) continue;
                    min = Math.Min(min, x); max = Math.Max(max, x); bottom = Math.Max(bottom, y);
                }
            }
            double center = (min + max) / 2.0;
            double expectedX = (expanded ? (i == 0 ? 202 : 478) : (i == 0 ? 64 : 616)) * scale;
            double price = i == 0 ? 3499.2 : 3499.8;
            double length = (i == 0 ? 120 : 80) / 5.0;
            double expectedBottom = (208 - (price - low) / (high - low) * 184 + length) * scale;
            if (bottom < 0 || Math.Abs(center - expectedX) > 3 || Math.Abs(bottom - expectedBottom) > 3)
                throw new InvalidOperationException(String.Format("Painted pole {0}: x={1}, bottom={2}; expected {3:F1},{4:F1} in {5}x{6}.", i, center, bottom, expectedX, expectedBottom, plot.Width, plot.Height));
            result[2*i] = center; result[2*i+1] = bottom;
        }
        return result;
    }
}
