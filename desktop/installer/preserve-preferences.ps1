# Runs before uninstalling the old package. No application or provider is launched.
param([string]$InstallDir, [string]$PreviousInstallDir, [Parameter(Mandatory)][string]$Destination)
$ErrorActionPreference = 'Stop'
try {
    $sources = @($InstallDir, $PreviousInstallDir) | Where-Object { $_ } | Select-Object -Unique | ForEach-Object {
        Join-Path $_ 'resources\backend\_internal\hidden_projects.json'
        Join-Path $_ 'resources\backend\hidden_projects.json'
    } | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
    # Preflight ALL conflicts before creating anything.
    $expected = $null
    if (Test-Path -LiteralPath $Destination) { $expected = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash }
    foreach ($source in $sources) {
        $value = Get-Content -LiteralPath $source -Raw | ConvertFrom-Json
        foreach ($key in @('hidden_folders', 'pinned_folders')) {
            if ($null -eq $value.$key -or $value.$key -isnot [array]) { throw 'Invalid preferences' }
            foreach ($item in $value.$key) { if ($item -isnot [string]) { throw 'Invalid preference item' } }
        }
        $hash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
        if ($expected -and $expected -ne $hash) { throw 'Preference conflict' }
        $expected = $hash
    }
    if ($sources -and !(Test-Path -LiteralPath $Destination)) {
        $created = $false
        try {
            New-Item -ItemType Directory -Force (Split-Path $Destination) | Out-Null
            $stream = [IO.File]::Open($Destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
            $created = $true
            try { $bytes = [IO.File]::ReadAllBytes(@($sources)[0]); $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
            if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -ne $expected) { throw 'Verification failed' }
        } catch {
            if ($created) { Remove-Item -LiteralPath $Destination }
            throw
        }
    }
    exit 0
} catch { Write-Error 'Preference preservation failed; original files retained.'; exit 1 }
