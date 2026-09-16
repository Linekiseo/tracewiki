$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    dotnet --info
    dotnet restore .\EvidenceRag.Windows.sln
    dotnet test .\tests\EvidenceRag.Windows.Tests\EvidenceRag.Windows.Tests.csproj -c Release --no-restore
    foreach ($architecture in @("x64", "arm64")) {
        dotnet build .\src\EvidenceRag.Windows\EvidenceRag.Windows.csproj `
            -c Release -p:Platform=$architecture --no-restore
    }
    $forbidden = Get-ChildItem .\src, .\tests -Recurse -File | Select-String -Pattern "WebView2|Microsoft\.Web\.WebView|<WebView"
    if ($forbidden) { throw "WebView usage is forbidden in the native Windows client." }
}
finally { Pop-Location }
