<#
.SYNOPSIS
Refreshes the scanner's daily OHLCV inputs and ClickHouse feature cache.

.DESCRIPTION
Run this on the host after the NSE close.  It deliberately keeps the two
refresh stages separate:

1. download_parquet.py audits the current and previous monthly OHLCV files
   and fills only missing trading days (unless -SkipDownload is supplied).
2. The existing API endpoint rebuilds trading.daily_screener_features from
   those monthly parquet files and returns the cached date and row count.

The script never uses --force, so normal runs do not rebuild already-complete
monthly files.  Use -AllMonths only when repairing historical data.

.EXAMPLE
  .\scripts\refresh_daily_screener.ps1

.EXAMPLE
  # Raw parquet was copied or updated separately; rebuild only the feature cache.
  .\scripts\refresh_daily_screener.ps1 -SkipDownload

.EXAMPLE
  # Preview the commands without downloading or rebuilding anything.
  .\scripts\refresh_daily_screener.ps1 -WhatIf
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateRange(1, 24)]
    [int]$RecentMonths = 2,

    [switch]$AllMonths,

    [switch]$SkipDownload,

    [switch]$SkipFeatureCache,

    [string]$ApiBaseUrl = 'http://127.0.0.1:3000',

    # A successful HTTP response is not enough if the underlying parquet feed
    # has stopped updating. Five calendar days permits normal weekends and a
    # short exchange-holiday stretch; set 0 for a strict post-close check.
    [ValidateRange(0, 30)]
    [int]$MaximumDataAgeDays = 5,

    # Optional explicit interpreter path.  When omitted, the Windows Python
    # launcher is preferred and then python on PATH is used as a fallback.
    [string]$PythonExe = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($SkipDownload -and $SkipFeatureCache) {
    throw 'Nothing to do: remove either -SkipDownload or -SkipFeatureCache.'
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$downloader = Join-Path $repoRoot 'download_parquet.py'
if (-not (Test-Path -LiteralPath $downloader)) {
    throw "Could not find downloader at $downloader. Run this script from the repository checkout."
}

function Invoke-DhanParquetRefresh {
    param([string[]]$Arguments)

    if (-not [string]::IsNullOrWhiteSpace($PythonExe)) {
        & $PythonExe $downloader @Arguments
    }
    elseif (Get-Command py -ErrorAction SilentlyContinue) {
        # -3 selects any installed Python 3 release and works across supported
        # Windows installations without hard-coding the minor version.
        & py -3 $downloader @Arguments
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python $downloader @Arguments
    }
    else {
        throw 'Python 3 was not found. Install Python with pandas, pyarrow, and requests, or pass -PythonExe <path>.'
    }

    if ($LASTEXITCODE -ne 0) {
        throw "OHLCV refresh failed with exit code $LASTEXITCODE. The ClickHouse feature cache was not rebuilt."
    }
}

Push-Location $repoRoot
try {
    if (-not $SkipDownload) {
        $downloadArgs = @()
        if ($AllMonths) {
            $downloadArgs += '--all-months'
        }
        else {
            $downloadArgs += '--recent-months'
            $downloadArgs += $RecentMonths.ToString()
        }

        if ($PSCmdlet.ShouldProcess('monthly OHLCV parquet files', "Audit/fill missing trading days ($($downloadArgs -join ' '))")) {
            Write-Host "Refreshing raw OHLCV data from Dhan..." -ForegroundColor Cyan
            Invoke-DhanParquetRefresh -Arguments $downloadArgs
        }
    }
    else {
        Write-Host 'Skipping raw OHLCV download; using the existing monthly parquet files.' -ForegroundColor Yellow
    }

    if (-not $SkipFeatureCache) {
        $cacheUrl = "$($ApiBaseUrl.TrimEnd('/'))/api/swing/feature-cache/refresh"
        if ($PSCmdlet.ShouldProcess($cacheUrl, 'Rebuild the daily screener feature cache')) {
            Write-Host 'Refreshing ClickHouse daily screener features...' -ForegroundColor Cyan
            try {
                $result = Invoke-RestMethod -Method Post -Uri $cacheUrl -ContentType 'application/json' -TimeoutSec 600
            }
            catch {
                throw "Feature-cache endpoint failed at $cacheUrl. Start the stack with 'docker compose up -d' and confirm the UI is reachable. Details: $($_.Exception.Message)"
            }

            $dataDate = [string]$result.data_date
            [long]$cachedRows = 0
            if ($null -ne $result.cached_rows) {
                $cachedRows = [long]$result.cached_rows
            }
            if ([string]::IsNullOrWhiteSpace($dataDate) -or $cachedRows -le 0) {
                throw "Feature-cache refresh completed without usable data (data_date='$dataDate', cached_rows=$cachedRows). Check the monthly parquet files and enabled watchlist."
            }

            try {
                $dataDay = [datetime]::ParseExact($dataDate, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
            }
            catch {
                throw "Feature-cache refresh returned an invalid data_date '$dataDate'."
            }
            $dataAgeDays = ((Get-Date).Date - $dataDay.Date).Days
            if ($dataAgeDays -gt $MaximumDataAgeDays) {
                throw "Feature-cache refresh returned $dataDate, which is $dataAgeDays day(s) old (limit: $MaximumDataAgeDays). Check Dhan credentials and the latest monthly parquet file."
            }

            Write-Host "Daily screener cache ready: $dataDate ($cachedRows enabled symbols; $dataAgeDays day(s) old)." -ForegroundColor Green
        }
    }
    else {
        Write-Host 'Skipping feature-cache refresh.' -ForegroundColor Yellow
    }
}
finally {
    Pop-Location
}
