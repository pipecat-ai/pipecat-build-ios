import Foundation
import Security

enum TTSProviderID: String, Codable, Sendable {
    case pocketTTS = "pocket-tts"
    case phonon

    static var available: [Self] {
        #if ENABLE_PHONON
        [.pocketTTS, .phonon]
        #else
        [.pocketTTS]
        #endif
    }

    var title: String { self == .pocketTTS ? "PocketTTS" : "Gradium Phonon" }
    var defaultVoice: String { self == .pocketTTS ? "alba" : "Marlowe" }
    var voices: [String] {
        switch self {
        case .pocketTTS:
            return (try? PocketTTSAssets.load(from: PocketTTSAssets.bundledRoot()).voices) ?? []
        case .phonon:
            #if ENABLE_PHONON
            return ["Marlowe", "Freya", "Archie", "Freddie", "Elodie-Rose", "Garrett", "Damon", "Zoey"]
            #else
            return []
            #endif
        }
    }

    func voiceTitle(_ voice: String) -> String {
        self == .pocketTTS ? voice.replacingOccurrences(of: "_", with: " ").capitalized : voice
    }
}

struct VoiceConfiguration: Codable, Equatable, Sendable {
    var provider: TTSProviderID = .pocketTTS
    var voices: [String: String] = [:]

    var effectiveProvider: TTSProviderID { TTSProviderID.available.contains(provider) ? provider : .pocketTTS }
    func voice(for provider: TTSProviderID) -> String { voices[provider.rawValue] ?? provider.defaultVoice }
}

enum VoiceSettings {
    private static let configurationKey = "voice-configuration"

    static func load(defaults: UserDefaults = .standard) -> VoiceConfiguration {
        if let data = defaults.data(forKey: configurationKey),
           let saved = try? JSONDecoder().decode(VoiceConfiguration.self, from: data) { return saved }
        var value = VoiceConfiguration()
        if let voice = defaults.string(forKey: "voice") {
            value.provider = .phonon
            value.voices[TTSProviderID.phonon.rawValue] = voice
        }
        #if ENABLE_PHONON
        if defaults.string(forKey: "voice") == nil && !loadKey().isEmpty { value.provider = .phonon }
        #endif
        return value
    }

    static func save(_ configuration: VoiceConfiguration, defaults: UserDefaults = .standard) throws {
        defaults.set(try JSONEncoder().encode(configuration), forKey: configurationKey)
    }

    #if ENABLE_PHONON
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
    #endif
}
