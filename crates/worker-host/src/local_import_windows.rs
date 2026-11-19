use std::{
    io,
    os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle},
    ptr,
    time::Duration,
};

use tokio::process::{Child, Command};
use windows_sys::Win32::{
    Foundation::{ERROR_NO_MORE_FILES, INVALID_HANDLE_VALUE},
    System::{
        Diagnostics::ToolHelp::{
            CreateToolhelp32Snapshot, TH32CS_SNAPTHREAD, THREADENTRY32, Thread32First, Thread32Next,
        },
        JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            JOBOBJECT_BASIC_ACCOUNTING_INFORMATION, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            JobObjectBasicAccountingInformation, JobObjectExtendedLimitInformation,
            QueryInformationJobObject, SetInformationJobObject, TerminateJobObject,
        },
        Threading::{
            CREATE_SUSPENDED, GetProcessIdOfThread, OpenThread, ResumeThread,
            THREAD_QUERY_LIMITED_INFORMATION, THREAD_SUSPEND_RESUME,
        },
    },
};

pub(super) struct Job(OwnedHandle);

impl Job {
    pub(super) fn prepare(command: &mut Command) -> io::Result<Self> {
        // SAFETY: null attributes and name request a private, non-inheritable job.
        let handle = unsafe { CreateJobObjectW(ptr::null(), ptr::null()) };
        if handle.is_null() {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: this non-null handle is newly owned and has one RAII closer.
        let job = Self(unsafe { OwnedHandle::from_raw_handle(handle) });
        let mut information = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        // SAFETY: the job is live and the initialized structure matches the class and size.
        let configured = unsafe {
            SetInformationJobObject(
                job.0.as_raw_handle(),
                JobObjectExtendedLimitInformation,
                ptr::from_ref(&information).cast(),
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        };
        if configured == 0 {
            return Err(io::Error::last_os_error());
        }
        // A normal venv launcher can start the Python interpreter before spawn
        // returns. Attach while its first thread is still suspended.
        command.creation_flags(CREATE_SUSPENDED);
        Ok(job)
    }

    pub(super) fn start(&self, child: &Child) -> io::Result<()> {
        let (Some(process), Some(pid)) = (child.raw_handle(), child.id()) else {
            return Err(io::Error::other("missing suspended import worker"));
        };
        // SAFETY: both handles remain live. Only this newly suspended child is assigned.
        if unsafe { AssignProcessToJobObject(self.0.as_raw_handle(), process) } == 0 {
            return Err(io::Error::last_os_error());
        }

        // std::process retains the process handle, so open its sole initial
        // thread separately. The live, suspended process still owns its PID.
        let thread_id = initial_thread(pid)?;
        // SAFETY: use an ID from the snapshot selected for this owned process only.
        let thread = unsafe {
            OpenThread(
                THREAD_QUERY_LIMITED_INFORMATION | THREAD_SUSPEND_RESUME,
                0,
                thread_id,
            )
        };
        if thread.is_null() {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: OpenThread returned a new, owned non-null handle.
        let thread = unsafe { OwnedHandle::from_raw_handle(thread) };
        // SAFETY: the thread handle remains live for the check and the resume below.
        if unsafe { GetProcessIdOfThread(thread.as_raw_handle()) } != pid {
            return Err(io::Error::other("import worker thread owner changed"));
        }
        // SAFETY: the validated initial thread belongs to this job-bound child.
        match unsafe { ResumeThread(thread.as_raw_handle()) } {
            1 => Ok(()),
            u32::MAX => Err(io::Error::last_os_error()),
            _ => Err(io::Error::other("unexpected import worker suspend count")),
        }
    }

    pub(super) fn terminate(&self) {
        // SAFETY: the supervisor owns this live job handle. The reaper also waits
        // for the direct child if job attachment failed.
        unsafe { TerminateJobObject(self.0.as_raw_handle(), 1) };
    }

    pub(super) async fn wait_empty(&self) -> io::Result<()> {
        loop {
            let mut information = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
            // SAFETY: the writable accounting structure matches its class and size.
            let observed = unsafe {
                QueryInformationJobObject(
                    self.0.as_raw_handle(),
                    JobObjectBasicAccountingInformation,
                    ptr::from_mut(&mut information).cast(),
                    size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>() as u32,
                    ptr::null_mut(),
                )
            };
            if observed == 0 {
                return Err(io::Error::last_os_error());
            }
            if information.ActiveProcesses == 0 {
                return Ok(());
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }

    #[cfg(test)]
    pub(super) fn duplicate_handle(&self) -> io::Result<OwnedHandle> {
        self.0.try_clone()
    }
}

fn initial_thread(pid: u32) -> io::Result<u32> {
    // This snapshot is only an index. Open a handle only for the owned worker.
    // SAFETY: the thread snapshot request uses no caller-provided pointers.
    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
    if snapshot == INVALID_HANDLE_VALUE {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: the successful snapshot call transferred a valid, owned handle.
    let snapshot = unsafe { OwnedHandle::from_raw_handle(snapshot) };
    let mut entry = THREADENTRY32 {
        dwSize: size_of::<THREADENTRY32>() as u32,
        ..Default::default()
    };
    // SAFETY: the snapshot is live and entry is initialized with the required size.
    if unsafe { Thread32First(snapshot.as_raw_handle(), &mut entry) } == 0 {
        return Err(io::Error::last_os_error());
    }
    let mut initial = None;
    loop {
        if entry.th32OwnerProcessID == pid && initial.replace(entry.th32ThreadID).is_some() {
            return Err(io::Error::other(
                "import worker has multiple initial threads",
            ));
        }
        entry.dwSize = size_of::<THREADENTRY32>() as u32;
        // SAFETY: the snapshot is live and entry size is restored as required.
        if unsafe { Thread32Next(snapshot.as_raw_handle(), &mut entry) } == 0 {
            let error = io::Error::last_os_error();
            if error.raw_os_error() != Some(ERROR_NO_MORE_FILES as i32) {
                return Err(error);
            }
            return initial.ok_or_else(|| io::Error::other("missing import worker thread"));
        }
    }
}

#[cfg(test)]
pub(super) struct ProcessWitness(OwnedHandle);

#[cfg(test)]
impl ProcessWitness {
    pub(super) fn open(pid: u32) -> io::Result<Self> {
        use windows_sys::Win32::System::Threading::{
            OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SYNCHRONIZE,
        };

        // SAFETY: the fixture supplies IDs of its own processes. Open observation-only rights.
        let process = unsafe {
            OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE,
                0,
                pid,
            )
        };
        if process.is_null() {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: OpenProcess transferred a new valid handle to the test.
        Ok(Self(unsafe { OwnedHandle::from_raw_handle(process) }))
    }

    pub(super) fn is_in(&self, job: &OwnedHandle) -> io::Result<bool> {
        use windows_sys::Win32::System::JobObjects::IsProcessInJob;

        let mut member = 0;
        // SAFETY: both handles are held and the writable flag is initialized.
        if unsafe { IsProcessInJob(self.0.as_raw_handle(), job.as_raw_handle(), &mut member) } == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(member != 0)
    }

    pub(super) fn exited(&self) -> bool {
        use windows_sys::Win32::{
            Foundation::WAIT_OBJECT_0, System::Threading::WaitForSingleObject,
        };

        // SAFETY: this is a live, synchronizable process handle observed without blocking.
        let state = unsafe { WaitForSingleObject(self.0.as_raw_handle(), 0) };
        state == WAIT_OBJECT_0
    }
}
