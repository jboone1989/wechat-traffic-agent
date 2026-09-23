# Pi Remote repair capability

`REQUEST_CODE_REPAIR` is registered by the Content Agent composition root only
when `PI_REMOTE_REPAIR_ROOT` points to a trusted Pi Remote checkout containing
`cli.js`. `PI_REMOTE_NODE` optionally selects the Node executable. The
capability has the `LOCAL_STATE` side-effect level and requires
`repair.request`; the runtime raises its ceiling only while this opt-in is
configured.

The provider uses a fixed argv invocation of the Pi Remote `repair-request`
command with no shell. Ferro's SelfMaintenance and SelfRepairExecutor submit
only the versioned RepairRequest payload. Neither receives source edit, Git,
merge, or deploy capabilities. Pi Remote returns a durable request id and
idempotently accepts repeated delivery.

A request reaching Pi Remote is not yet a completed repair. The detached
Planner/Coder/Reviewer worker that creates a separate worktree, validates only
approved target paths, runs focused tests, captures a checkpoint, performs
independent Reviewer verification, and advances the request from real session
evidence still needs to be implemented and configured. Until that worker is
available, the request remains visible as `RECEIVED` for a controller to
process. Merge and deployment remain separate human approvals.
