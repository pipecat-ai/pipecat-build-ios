enum SpeechSynthesizers {
    static func make(_ provider: TTSProviderID) throws -> any SpeechSynthesizer {
        switch provider {
        case .pocketTTS:
            return PocketTTSSynthesizer(root: try PocketTTSAssets.bundledRoot())
        case .phonon:
            #if ENABLE_PHONON
            return PhononSynthesizer()
            #else
            throw VoiceError(message: "This speech provider is unavailable in this build.")
            #endif
        }
    }
}
