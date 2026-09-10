# CFSv2 independent availability timer

GitHub continues to download, validate, render, and publish maps. The Windows task only dispatches the availability workflow when it has no active run and no successful check in the past 20 minutes. It runs every 15 minutes, independently of GitHub cron. The 20-minute freshness window lets the backup recover one missed GitHub poll on its next pass instead of waiting roughly 45 minutes between checks.

Install GitHub CLI if needed (`winget install --id GitHub.cli`), then run `gh auth login` with access to jwallio/seasonal and Actions dispatch permissions.

Run in PowerShell:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/jwallio/seasonal/main/scripts/cfsv2-watchdog.ps1 -OutFile "$env:TEMP\cfsv2-watchdog.ps1"
& "$env:TEMP\cfsv2-watchdog.ps1" -Install
```

The computer must be awake, online, and the Windows user logged in. Registration does not run a local model or host the website. An offline PC leaves the ordinary GitHub schedule in place. Check Task Scheduler for "WallCloud CFS availability backup". To remove it:

```powershell
Unregister-ScheduledTask -TaskName 'WallCloud CFS availability backup' -Confirm:$false
```

NOAA directory errors receive at most three attempts, with 15- and 30-second pauses. A missing file or incomplete directory never counts as ready. GitHub polling is offset to minutes 7, 22, 37, and 52; this reduces top-of-hour contention but does not guarantee punctual scheduling.
