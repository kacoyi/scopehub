using System;
using System.Collections;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class HandoffFixtureAcl
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(string path, uint access, uint share,
        IntPtr security, uint disposition, uint flags, IntPtr template);

    [DllImport("advapi32.dll")]
    private static extern uint GetSecurityInfo(SafeFileHandle file, int type, uint information,
        IntPtr owner, IntPtr group, out IntPtr dacl, IntPtr sacl, out IntPtr descriptor);

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern uint GetNamedSecurityInfoW(string path, int type, uint information,
        IntPtr owner, IntPtr group, out IntPtr dacl, IntPtr sacl, out IntPtr descriptor);

    [DllImport("kernel32.dll")]
    private static extern IntPtr LocalFree(IntPtr memory);

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    private static extern bool SetFileSecurityW(string path, uint information, [In] byte[] descriptor);

    [DllImport("advapi32.dll")]
    private static extern bool GetSecurityDescriptorDacl(
        IntPtr descriptor, out bool present, out IntPtr dacl, out bool defaulted);

    [DllImport("advapi32.dll")]
    private static extern bool IsValidAcl(IntPtr acl);

    [DllImport("advapi32.dll")]
    private static extern bool GetAce(IntPtr acl, uint index, out IntPtr ace);

    private const uint Dacl = 4;
    private const uint ProtectedDacl = 0x80000000;
    private const int FileObject = 1;
    private const uint ReadControl = 0x20000;
    private const uint ShareAll = 7;
    private const uint OpenExisting = 3;
    private const uint OpenReparsePoint = 0x00200000;

    public static byte[] Read(string path)
    {
        // The effective deny row can block data access while allowing ACL readback.
        using (SafeFileHandle file = CreateFileW(path, ReadControl, ShareAll, IntPtr.Zero,
            OpenExisting, OpenReparsePoint, IntPtr.Zero))
        {
            if (file.IsInvalid)
                throw new Win32Exception();
            IntPtr dacl, descriptor;
            uint status = GetSecurityInfo(file, FileObject, Dacl, IntPtr.Zero, IntPtr.Zero,
                out dacl, IntPtr.Zero, out descriptor);
            return CopyDacl(status, dacl, descriptor);
        }
    }

    public static byte[] ReadNamed(string path)
    {
        IntPtr dacl, descriptor;
        uint status = GetNamedSecurityInfoW(path, FileObject, Dacl, IntPtr.Zero, IntPtr.Zero,
            out dacl, IntPtr.Zero, out descriptor);
        return CopyDacl(status, dacl, descriptor);
    }

    private static byte[] CopyDacl(uint status, IntPtr dacl, IntPtr descriptor)
    {
        try
        {
            if (status != 0 || descriptor == IntPtr.Zero)
                throw new InvalidOperationException();
            if (dacl == IntPtr.Zero)
                return null;
            if (!IsValidAcl(dacl))
                throw new InvalidOperationException();
            // AclSize is the 16-bit length of the ACL in the live descriptor.
            byte[] bytes = new byte[(ushort)Marshal.ReadInt16(dacl, 2)];
            Marshal.Copy(dacl, bytes, 0, bytes.Length);
            return bytes;
        }
        finally
        {
            if (descriptor != IntPtr.Zero)
                LocalFree(descriptor);
        }
    }

    public static bool HasAce(byte[] bytes, bool isDescriptor, uint index, byte[] expected)
    {
        // Inspect the actual bytes independently of RawAcl serialization.
        GCHandle pinned = GCHandle.Alloc(bytes, GCHandleType.Pinned);
        try
        {
            IntPtr dacl = pinned.AddrOfPinnedObject();
            if (isDescriptor)
            {
                bool present, defaulted;
                if (!GetSecurityDescriptorDacl(pinned.AddrOfPinnedObject(), out present, out dacl, out defaulted) || !present)
                    return false;
            }
            IntPtr ace;
            if (dacl == IntPtr.Zero || !IsValidAcl(dacl) || !GetAce(dacl, index, out ace) ||
                ace == IntPtr.Zero || (ushort)Marshal.ReadInt16(ace, 2) != expected.Length)
                return false;

            byte[] actual = new byte[expected.Length];
            Marshal.Copy(ace, actual, 0, actual.Length);
            return StructuralComparisons.StructuralEqualityComparer.Equals(actual, expected);
        }
        finally
        {
            pinned.Free();
        }
    }

    public static void Write(string path, byte[] descriptor)
    {
        if (!SetFileSecurityW(path, Dacl | ProtectedDacl, descriptor))
            throw new Win32Exception();
    }
}
