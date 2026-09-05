# Seasonal analog runner recovery

The analog archive is read by runner `wn2-analogwx-windows-01` (registration
21, labels `self-hosted`, `windows`, `x64`, `wn2-analogwx`). Its installation
is `D:\actions-runner-wn2`. The local task is
`GitHubActionsRunner-seasonal`; do not stop other runners by executable name.
The registration's historical `jwallio/wn2` URL is retained after the repository
rename. It successfully authenticates against Seasonal; do not re-register it
as a routine recovery step.

## September 4 repair

The apparent ten-hour build was an unassigned workflow waiting for an offline
runner. Other runs exhausted GitHub's 24-hour runner queue limit with no steps
executed. The analog job's 120-minute execution timeout does not include that
wait. Default workflow concurrency can replace an older pending run even with
`cancel-in-progress: false`; retain this coalescing rather than building a
backlog of superseded source payloads.

The old task required an interactive login. Its restart-on-failure setting
also could not recover clean exits: the standard runner batch launcher returns
zero for several listener stop conditions. The repaired task:

- Runs as the existing account using S4U, without a stored Windows password
  or an elevated runner token.
- Starts 30 seconds after boot and retries every five minutes indefinitely.
- Uses `IgnoreNew`, so retry triggers leave active jobs alone.
- Retains the existing action, registration, archive, Python, battery policy,
  and last-good products. It has no task execution time limit.

S4U does not supply Windows network credentials or access to encrypted user
files. The installation probe verifies this project's local archive, existing
Python imports, and token-independent HTTPS access in a noninteractive session.
The actual runner must then authenticate and complete a workflow. Do not assume
this configuration will work for future SMB shares, EFS files, or user secrets.

## Install or repair

From an elevated PowerShell, with the runner idle:

```powershell
.\scripts\install_seasonal_runner_task.ps1 -BackupDirectory 'D:\weather-projects\_repairs\seasonal-runner-NEW-TIMESTAMP'
```

The installer refuses mismatched runner metadata, missing runtime/archive,
existing backups, failed noninteractive probes, and a busy runner. It backs up
the task XML before changing it. Task Scheduler can leave a child listener alive
after stopping the task: the installer removes only captured listeners whose
path and creation time still match this runner, after confirming it is idle.
Never run `config.cmd remove` or read/copy credential files to fix a stopped task.

Check the result:

```powershell
Get-ScheduledTask -TaskName GitHubActionsRunner-seasonal
Get-ScheduledTaskInfo -TaskName GitHubActionsRunner-seasonal
gh api repos/jwallio/seasonal/actions/runners/21 --jq '{id,status,busy}'
```

When the runner is online, dispatch **one** `seasonal-analogs.yml` run. Verify an
assigned runner and completed build, then its `publish-pages.yml` handoff and
Pages deployment. Check both live analog manifests and representative image
URLs. Cached historical graphics may legitimately keep their original image
generation dates when selection and rendering have not changed. Source model
initialization, analog generation, cached graphic generation, and deployment
are distinct timestamps.

## Independent queue detection

`seasonal-analog-health.yml` runs on GitHub-hosted Ubuntu twice hourly, outside
the analog concurrency group. It needs only `actions: read` and `contents: read`.
`scripts/check_analog_queue.py` fails the check when an admitted, unassigned
`wn2-analogwx` job has waited at least 15 minutes. It ignores active builds,
completed cancellations, and pending workflow concurrency without an admitted
job. API failures fail the check rather than reporting healthy.

This is demand-based queue detection, not an idle-runner heartbeat. It cannot
detect an offline machine until work queues; GitHub schedule delays add to alert
latency. Failures appear in Actions; delivery depends on the owner's GitHub
notification settings. It never cancels, dispatches, changes registration, or
publishes products. A local retry cannot recover a powered-off machine, broken
network, expired registration, or damaged runtime.

## Rollback

Wait until runner 21 is idle. In elevated PowerShell, stop only the Seasonal
task, then identify and stop any remaining listener at
`D:\actions-runner-wn2\bin\Runner.Listener.exe`. Restore the backed-up XML:

```powershell
Register-ScheduledTask -TaskName GitHubActionsRunner-seasonal -Xml (Get-Content -LiteralPath 'BACKUP\task-before.xml' -Raw) -Force
Start-ScheduledTask -TaskName GitHubActionsRunner-seasonal
```

Restoring the old XML restores its interactive-login dependency. Disable only
the new queue health workflow to roll back monitoring. Neither operation needs
to touch analog data, source manifests, historical products, or runner credentials.

References: [GitHub runner routing](https://docs.github.com/en/actions/reference/runners/self-hosted-runners#routing-precedence-for-self-hosted-runners),
[workflow concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency),
[Windows service configuration](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/configure-the-application?platform=windows),
[Task Scheduler logon types](https://learn.microsoft.com/en-us/windows/win32/api/taskschd/ne-taskschd-task_logon_type).
