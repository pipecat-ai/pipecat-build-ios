import CryptoKit
import Foundation

struct PocketTTSAssets: Decodable, Sendable {
    struct Asset: Decodable, Sendable {
        let path: String
        let size: Int
        let sha256: String
    }
    let schemaVersion: Int
    let language: String
    let precision: String
    let placement: String
    let cacheSubdirectory: String
    let languageSubdirectory: String
    let defaultVoice: String
    let voices: [String]
    let files: [Asset]

    static func bundledRoot() throws -> URL {
        guard let root = Bundle.main.url(forResource: "pocket-tts", withExtension: nil) else {
            throw VoiceError(message: "PocketTTS model files are missing. Run setup and rebuild the app.")
        }
        return root
    }

    static func load(from root: URL) throws -> Self {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let manifest = try decoder.decode(Self.self, from: Data(contentsOf: root.appendingPathComponent("manifest.json")))
        guard manifest.schemaVersion == 1, manifest.language == "english",
              manifest.precision == "int8", manifest.placement == "gpu",
              manifest.cacheSubdirectory == "Models/pocket-tts",
              manifest.languageSubdirectory == "v2.1/english",
              manifest.voices.contains(manifest.defaultVoice), !manifest.files.isEmpty else {
            throw VoiceError(message: "PocketTTS model configuration is incompatible with this app.")
        }
        return manifest
    }

    /// Fail locally before entering FluidAudio's downloader-backed initialization.
    func verify(in root: URL) throws {
        let rootPath = root.resolvingSymlinksInPath().path + "/"
        for asset in files {
            try Task.checkCancellation()
            let url = root.appendingPathComponent(cacheSubdirectory).appendingPathComponent(asset.path)
            guard asset.path.hasPrefix(languageSubdirectory + "/"),
                  !asset.path.split(separator: "/").contains(".."),
                  url.resolvingSymlinksInPath().path.hasPrefix(rootPath),
                  asset.size >= 0,
                  (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) == asset.size else {
                throw VoiceError(message: "PocketTTS has a missing or invalid asset: \(asset.path). Run setup and rebuild.")
            }
            let file = try FileHandle(forReadingFrom: url)
            defer { try? file.close() }
            var digest = SHA256()
            while let block = try file.read(upToCount: 1_048_576), !block.isEmpty {
                try Task.checkCancellation()
                digest.update(data: block)
            }
            guard digest.finalize().map({ String(format: "%02x", $0) }).joined() == asset.sha256 else {
                throw VoiceError(message: "PocketTTS asset verification failed: \(asset.path). Run setup and rebuild.")
            }
        }
        // These are the files FluidAudio otherwise attempts to backfill or download.
        let declared = Set(files.map(\.path))
        let required = ["cond_prefill", "flowlm_stepv2", "flow_decoder_fused", "mimi_decoder"]
            .flatMap { ["\($0).mlmodelc/coremldata.bin", "\($0).mlmodelc/model.mil", "\($0).mlmodelc/weights/weight.bin"] }
            + ["constants_bin/bos_before_voice.bin", "constants_bin/bos_emb.bin",
               "constants_bin/text_embed_table.bin", "constants_bin/tokenizer.model"]
            + voices.map { "constants_bin/\($0).safetensors" }
        guard required.allSatisfy({ declared.contains(languageSubdirectory + "/" + $0) }) else {
            throw VoiceError(message: "PocketTTS asset manifest is incomplete. Run setup and rebuild.")
        }
    }
}
