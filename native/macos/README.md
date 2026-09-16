# Evidence Wiki + RAG — Native macOS

This directory contains the real SwiftUI macOS client. It does not embed a web
view and does not reuse the React/Tauri interface.

Requirements:

- macOS 14 or newer
- Xcode with Swift 5.9 or newer
- Evidence RAG API listening at `http://127.0.0.1:8765` by default

Build and test:

```sh
swift build
swift test
```

Build, package, launch, and verify the native app:

```sh
./scripts/build_and_run.sh --verify
```

The packaged application is written to:

```text
dist/Evidence Wiki + RAG.app
```

The backend URL and OpenAI-compatible provider are configured in the native
Settings scene. LLM API keys are stored in macOS Keychain and are never written
to `UserDefaults`, logs, or this package.

Trusted code queries load the project's governed repository list and bind the
selected published repository, branch, and full commit to the request. An
ambiguous or unavailable repository scope is rejected before the query is sent.
Existing credentials saved by the retired Tauri client are migrated into the
native Keychain service without exposing the secret.

Current engineering verification: 9 Swift tests, strict Swift format, debug and
release builds, ad-hoc bundle signing, process launch verification, and a source
gate that rejects WebKit/WebView dependencies.
