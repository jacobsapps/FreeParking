// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "FreeParking",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "FreeParking", targets: ["FreeParking"])],
    targets: [.executableTarget(name: "FreeParking")]
)
