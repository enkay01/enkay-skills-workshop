<#
.SYNOPSIS
    Automated release and synchronization script for Windows Computer Use (WCU).
.DESCRIPTION
    Builds the Rust engine, updates the CLI package, creates NTFS Directory Junctions
    for all agent environments (.gemini, .agents, .claude), and runs end-to-end self-verification.
.PARAMETER SkipTests
    Skip running the regression test suite before releasing.
.PARAMETER Copy
    Copy skill files instead of creating NTFS Directory Junctions.
.PARAMETER Check
    Check current status across engine, session, and skill environments without modifying anything.
.PARAMETER Clean
    Clean cargo build target before rebuilding the Rust engine.
#>
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$Copy,
    [switch]$Check,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$releasePy = Join-Path $scriptDir "release.py"

$pyArgs = @($releasePy)
if ($SkipTests) { $pyArgs += "--skip-tests" }
if ($Copy) { $pyArgs += "--copy" }
if ($Check) { $pyArgs += "--check" }
if ($Clean) { $pyArgs += "--clean" }

python @pyArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
