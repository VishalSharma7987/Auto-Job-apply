# Base64-encodes your resume PDF for the GitHub secret RESUME_PDF_B64.
# Usage: .\scripts\encode_resume.ps1 -Path C:\path\to\resume.pdf
# The value is copied to the clipboard (and never printed).
param([Parameter(Mandatory = $true)][string]$Path)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $Path)) { throw "File not found: $Path" }
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Resolve-Path $Path)))
Set-Clipboard -Value $b64
Write-Host "Copied $($b64.Length) base64 characters to the clipboard. Paste into GitHub -> Settings -> Secrets -> RESUME_PDF_B64."
