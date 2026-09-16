import Foundation
import Security

protocol SecretStoring {
  func save(_ value: String, account: String) throws
  func read(account: String) throws -> String?
  func delete(account: String) throws
}

enum KeychainError: LocalizedError {
  case unexpectedStatus(OSStatus)
  case invalidData

  var errorDescription: String? {
    switch self {
    case .unexpectedStatus(let status): "系统钥匙串操作失败（\(status)）。"
    case .invalidData: "系统钥匙串中的密钥格式无效。"
    }
  }
}

final class KeychainStore: SecretStoring {
  private let service: String
  private let legacyService = "com.evidencerag.workbench.llm"

  init(service: String = "com.evidencerag.native.llm") {
    self.service = service
  }

  func save(_ value: String, account: String) throws {
    let data = Data(value.utf8)
    let query = baseQuery(account: account)
    let status = SecItemCopyMatching(query as CFDictionary, nil)
    if status == errSecSuccess {
      let update = [kSecValueData as String: data]
      let updateStatus = SecItemUpdate(query as CFDictionary, update as CFDictionary)
      guard updateStatus == errSecSuccess else {
        throw KeychainError.unexpectedStatus(updateStatus)
      }
    } else if status == errSecItemNotFound {
      var insert = query
      insert[kSecValueData as String] = data
      insert[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
      let insertStatus = SecItemAdd(insert as CFDictionary, nil)
      guard insertStatus == errSecSuccess else {
        throw KeychainError.unexpectedStatus(insertStatus)
      }
    } else {
      throw KeychainError.unexpectedStatus(status)
    }
  }

  func read(account: String) throws -> String? {
    if let value = try read(service: service, account: account) {
      return value
    }
    guard let projectID = legacyProjectID(from: account) else { return nil }
    return try read(service: legacyService, account: projectID)
  }

  private func read(service: String, account: String) throws -> String? {
    var query = baseQuery(service: service, account: account)
    query[kSecReturnData as String] = true
    query[kSecMatchLimit as String] = kSecMatchLimitOne
    var result: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &result)
    if status == errSecItemNotFound { return nil }
    guard status == errSecSuccess else { throw KeychainError.unexpectedStatus(status) }
    guard let data = result as? Data, let value = String(data: data, encoding: .utf8) else {
      throw KeychainError.invalidData
    }
    return value
  }

  func delete(account: String) throws {
    let status = SecItemDelete(baseQuery(account: account) as CFDictionary)
    guard status == errSecSuccess || status == errSecItemNotFound else {
      throw KeychainError.unexpectedStatus(status)
    }
  }

  private func baseQuery(account: String) -> [String: Any] {
    baseQuery(service: service, account: account)
  }

  private func baseQuery(service: String, account: String) -> [String: Any] {
    [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account,
    ]
  }

  private func legacyProjectID(from account: String) -> String? {
    let prefix = "project:"
    let suffix = ":llm"
    guard account.hasPrefix(prefix), account.hasSuffix(suffix) else { return nil }
    let start = account.index(account.startIndex, offsetBy: prefix.count)
    let end = account.index(account.endIndex, offsetBy: -suffix.count)
    let projectID = String(account[start..<end])
    guard !projectID.isEmpty,
      projectID.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || "-_.".contains($0)) })
    else { return nil }
    return projectID
  }
}
