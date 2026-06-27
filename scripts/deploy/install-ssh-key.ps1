# One-time: install your laptop SSH public key on the VPS using root password.
# Run in PowerShell (will prompt for VPS root password once):
#
#   powershell -ExecutionPolicy Bypass -File scripts\deploy\install-ssh-key.ps1

param(
    [Parameter(Mandatory = $true)]
    [string]$VpsHost,
    [string]$User = "root",
    [string]$PubKeyPath = "$env:USERPROFILE\.ssh\id_ed25519.pub"
)

if (-not (Test-Path $PubKeyPath)) {
    Write-Error "Missing $PubKeyPath. Run: ssh-keygen -t ed25519 -C your@email"
    exit 1
}

$target = "{0}@{1}" -f $User, $VpsHost
Write-Host "Connecting to $target (enter VPS root password when prompted)..."

$setup = "mkdir -p .ssh && chmod 700 .ssh && touch .ssh/authorized_keys && chmod 600 .ssh/authorized_keys"
& ssh $target $setup
if ($LASTEXITCODE -ne 0) {
    Write-Error "SSH setup failed (exit $LASTEXITCODE)."
    exit $LASTEXITCODE
}

Get-Content $PubKeyPath | & ssh $target "cat >> .ssh/authorized_keys"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to append public key (exit $LASTEXITCODE)."
    exit $LASTEXITCODE
}

Write-Host "SSH key installed. Verifying key-based login..."
& ssh -i $PubKeyPath -o BatchMode=yes $target "echo OK"
if ($LASTEXITCODE -eq 0) {
    Write-Host "Done. Key login works."
} else {
    Write-Host "Key appended but BatchMode verify failed; try: ssh $target echo OK"
}
