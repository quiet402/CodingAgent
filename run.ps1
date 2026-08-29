[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Workspace = ".",

    [Parameter(Position = 1)]
    [string]$Task = "",

    [switch]$NoStream
)

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$mainPath = Join-Path $projectRoot "main.py"

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    Write-Error "Virtual environment not found. Run: py -3.10 -m venv .venv"
    exit 2
}

try {
    $workspacePath = (Resolve-Path -LiteralPath $Workspace -ErrorAction Stop).Path
} catch {
    Write-Error "Workspace does not exist: $Workspace"
    exit 2
}

$forgeArgs = @($mainPath, "--workspace", $workspacePath)
if ($NoStream) {
    $forgeArgs += "--no-stream"
}
if (-not [string]::IsNullOrWhiteSpace($Task)) {
    $forgeArgs += $Task
}

& $pythonPath @forgeArgs
exit $LASTEXITCODE
