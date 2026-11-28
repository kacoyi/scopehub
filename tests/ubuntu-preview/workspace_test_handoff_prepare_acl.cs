using System;
using System.Diagnostics;
using System.IO;
using System.Security.AccessControl;
using System.Security.Principal;

internal static class HandoffFixturePreparation
{
    private static int Main()
    {
        var clock = Stopwatch.StartNew();
        try
        {
            Report(clock, "helper-entered");
            string path = Environment.GetEnvironmentVariable("SCOPEHUB_TEST_HANDOFF");
            string fixture = Environment.GetEnvironmentVariable("SCOPEHUB_TEST_ACL_FIXTURE");
            if (String.IsNullOrEmpty(path) || (fixture != "broad" && fixture != "explicit-only"))
                return 1;
            Report(clock, "input-ready");

            // Match the managed descriptors used by the original fixture setup.
            FileSecurity acl;
            SecurityIdentifier sid;
            FileSystemRights rights;
            if (fixture == "broad")
            {
                acl = File.GetAccessControl(path, AccessControlSections.Access);
                Report(clock, "baseline-read");
                sid = new SecurityIdentifier("S-1-1-0");
                rights = FileSystemRights.Read;
            }
            else
            {
                acl = new FileSecurity();
                acl.SetAccessRuleProtection(true, false);
                Report(clock, "explicit-dacl-ready");
                using (WindowsIdentity identity = WindowsIdentity.GetCurrent())
                    sid = identity.User;
                rights = FileSystemRights.FullControl;
            }
            acl.AddAccessRule(new FileSystemAccessRule(sid, rights, AccessControlType.Allow));
            Report(clock, "rule-added");
            File.SetAccessControl(path, acl);
            Report(clock, "dacl-written");
            return 0;
        }
        catch
        {
            // The parent can report completed stages without exception details.
            return 1;
        }
    }

    private static void Report(Stopwatch clock, string stage)
    {
        Console.Out.WriteLine("SCOPEHUB_ACL_PROGRESS {0} {1}", stage, clock.ElapsedMilliseconds);
        Console.Out.Flush();
    }
}
