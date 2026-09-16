#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
test -f EvidenceRag.Windows.sln
test -f src/EvidenceRag.Windows/App.xaml
test -f src/EvidenceRag.Windows/MainWindow.xaml
test -f src/EvidenceRag.Windows/Services/WindowsCredentialVault.cs
test -f tests/EvidenceRag.Windows.Tests/ApiClientTests.cs
pages=(ProjectsPage TrustedQueryPage WikiPage GraphPage SessionsPage AgentsPage SettingsPage)
for page in "${pages[@]}"; do
  test -f "src/EvidenceRag.Windows/Pages/${page}.xaml"
  test -f "src/EvidenceRag.Windows/Pages/${page}.xaml.cs"
done
if rg -n 'WebView2|Microsoft\.Web\.WebView|<WebView' src tests; then
  echo "WebView usage is forbidden" >&2
  exit 1
fi
rg -q 'Windows\.Security\.Credentials' src/EvidenceRag.Windows/Services/WindowsCredentialVault.cs
rg -q 'http://127\.0\.0\.1:8765' src/EvidenceRag.Windows.Core/Api/ApiClient.cs
echo "native/windows structural verification passed"
