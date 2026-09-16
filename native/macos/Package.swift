// swift-tools-version: 5.9
import PackageDescription

let package = Package(
  name: "EvidenceRAGNative",
  platforms: [.macOS(.v14)],
  products: [
    .executable(name: "EvidenceRAGNative", targets: ["EvidenceRAGNative"])
  ],
  targets: [
    .executableTarget(
      name: "EvidenceRAGNative",
      path: "Sources/EvidenceRAGNative"
    ),
    .testTarget(
      name: "EvidenceRAGNativeTests",
      dependencies: ["EvidenceRAGNative"],
      path: "Tests/EvidenceRAGNativeTests"
    ),
  ],
  swiftLanguageVersions: [.v5]
)
