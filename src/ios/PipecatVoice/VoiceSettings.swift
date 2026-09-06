import Foundation
import Security

enum VoiceSettings {
    static let voices = ["Marlowe", "Freya", "Archie", "Freddie", "Elodie-Rose", "Garrett", "Damon", "Zoey"]
    private static let service = "ai.pipecat.voice.gradium"

    static func loadKey() -> String {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccount as String: "api-key",
            kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return "" }
        return String(data: data, encoding: .utf8) ?? ""
    }

    static func saveKey(_ value: String) throws {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccount as String: "api-key"]
        if value.isEmpty {
            let status = SecItemDelete(query as CFDictionary)
            guard status == errSecSuccess || status == errSecItemNotFound else {
                throw VoiceError(message: "Could not remove the Gradium key from Keychain.")
            }
            return
        }
        let attributes: [String: Any] = [kSecValueData as String: Data(value.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            let addStatus = SecItemAdd(query.merging(attributes) { _, new in new } as CFDictionary, nil)
            guard addStatus == errSecSuccess else { throw VoiceError(message: "Could not save the Gradium key to Keychain.") }
        } else if status != errSecSuccess {
            throw VoiceError(message: "Could not update the Gradium key in Keychain.")
        }
    }

    static func validKey(_ key: String) -> Bool {
        key.range(of: "^gsk_[0-9a-f]{64}$", options: .regularExpression) != nil
    }
}
