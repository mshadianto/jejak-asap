# Stage 1 on 1.2-degree boxes around cities (about 110 m per pixel), one worker per box.
# Usage: powershell -File run_local_boxes.ps1 [-Start 2023-10-01] [-End 2023-10-31]
param([string]$Start = "2023-10-01", [string]$End = "2023-10-31", [int]$Step = 2)
$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
$py = ".\.venv-win\Scripts\python.exe"
$boxes = @{ "palangkaraya" = "113.3,-2.9,114.5,-1.7"; "banjarmasin" = "114.0,-3.8,115.2,-2.6"; "sampit" = "112.35,-3.13,113.55,-1.93" }
$dates = @()
$d = [datetime]$Start
while ($d -le [datetime]$End) { $dates += $d.ToString("yyyy-MM-dd"); $d = $d.AddDays($Step) }
$jobs = foreach ($name in $boxes.Keys) {
  Start-Job -Name $name -ArgumentList $PWD.Path, $py, $boxes[$name], $dates, $name -ScriptBlock {
    param($dir, $py, $bbox, $dates, $name)
    Set-Location $dir
    $env:PYTHONIOENCODING = "utf-8"
    foreach ($date in $dates) {
      $out = & $py -u run_stage1.py --date $date --bbox $bbox --width 1200 --out out/plumes_local --resume 2>&1 | Select-Object -Last 1
      "$name $date rc=$LASTEXITCODE $out"
    }
  }
}
$jobs | Wait-Job | Receive-Job
"ALL FINISHED $(Get-Date -Format HH:mm:ss)"
