import CryptoKit
import Foundation

private struct CheckFailure: Error { let message: String }

private func check(_ value: @autoclosure () -> Bool, _ message: String) throws {
    if !value() { throw CheckFailure(message: message) }
}

private func mustFail(_ operation: () throws -> Void) throws {
    do { try operation() }
    catch { return }
    throw CheckFailure(message: "Expected asset verification to fail")
}

@main
struct TTSChecks {
    static func main() async throws {
        try settings()
        try assets()
        try await cancellation()
        print("Native TTS checks passed: settings migration, asset integrity, request cancellation")
    }

    static func settings() throws {
        let suite = "ai.pipecat.tests.\(UUID())"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        try check(TTSProviderID.available == [.pocketTTS], "Public build exposes a private provider")
        let fresh = VoiceSettings.load(defaults: defaults)
        try check(fresh.effectiveProvider == .pocketTTS, "Wrong default provider")
        try check(fresh.voice(for: .pocketTTS) == "alba", "Wrong default voice")

        defaults.set("Freya", forKey: "voice")
        var legacy = VoiceSettings.load(defaults: defaults)
        try check(legacy.provider == .phonon, "Legacy provider was lost")
        try check(legacy.effectiveProvider == .pocketTTS, "Unavailable provider must fall back locally")
        try check(legacy.voice(for: .phonon) == "Freya", "Legacy voice was lost")
        try check(legacy.voice(for: .pocketTTS) == "alba", "Legacy voice leaked into another provider")
        try VoiceSettings.save(legacy, defaults: defaults)
        try check(VoiceSettings.load(defaults: defaults) == legacy, "Migration did not round-trip")

        legacy.provider = .pocketTTS
        legacy.voices[TTSProviderID.pocketTTS.rawValue] = "marius"
        try VoiceSettings.save(legacy, defaults: defaults)
        let saved = VoiceSettings.load(defaults: defaults)
        try check(saved.provider == .pocketTTS && saved.voice(for: .pocketTTS) == "marius", "Selection was not saved")
        try check(saved.voice(for: .phonon) == "Freya", "Switching providers erased its saved voice")
    }

    static func assets() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("tts-assets-\(UUID())")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let names = ["cond_prefill", "flowlm_stepv2", "flow_decoder_fused", "mimi_decoder"]
            .flatMap { ["\($0).mlmodelc/coremldata.bin", "\($0).mlmodelc/model.mil", "\($0).mlmodelc/weights/weight.bin"] }
            + ["constants_bin/bos_before_voice.bin", "constants_bin/bos_emb.bin",
               "constants_bin/text_embed_table.bin", "constants_bin/tokenizer.model", "constants_bin/alba.safetensors"]
        let files: [[String: Any]] = try names.map { name in
            let path = "v2.1/english/" + name
            let content = Data(name.utf8)
            let url = root.appendingPathComponent("Models/pocket-tts/" + path)
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try content.write(to: url)
            return ["path": path, "size": content.count,
                    "sha256": SHA256.hash(data: content).map { String(format: "%02x", $0) }.joined()]
        }
        var manifest: [String: Any] = ["schema_version": 1, "language": "english", "precision": "int8",
            "placement": "gpu", "cache_subdirectory": "Models/pocket-tts", "language_subdirectory": "v2.1/english",
            "default_voice": "alba", "voices": ["alba"], "files": files]
        let manifestURL = root.appendingPathComponent("manifest.json")
        try JSONSerialization.data(withJSONObject: manifest).write(to: manifestURL)
        let assets = try PocketTTSAssets.load(from: root)
        try assets.verify(in: root)
        let voice = root.appendingPathComponent("Models/pocket-tts/v2.1/english/constants_bin/alba.safetensors")
        let original = try Data(contentsOf: voice)
        try Data(repeating: 0, count: original.count).write(to: voice)
        try mustFail { try assets.verify(in: root) }
        try FileManager.default.removeItem(at: voice)
        try mustFail { try assets.verify(in: root) }
        try original.write(to: voice)
        manifest["files"] = Array(files.dropLast())
        try JSONSerialization.data(withJSONObject: manifest).write(to: manifestURL)
        try mustFail { try PocketTTSAssets.load(from: root).verify(in: root) }
        manifest["precision"] = "fp32"
        try JSONSerialization.data(withJSONObject: manifest).write(to: manifestURL)
        try mustFail { _ = try PocketTTSAssets.load(from: root) }
    }

    static func cancellation() async throws {
        let first = SpeechSynthesis { output in
            output.yield([1])
            try await Task.sleep(for: .seconds(30))
            output.yield([2])
        }
        var iterator = first.samples.makeAsyncIterator()
        let initial = try await iterator.next()
        try check(initial == [1], "Producer never started")
        first.cancel()
        await first.waitForCompletion()
        do {
            _ = try await iterator.next()
            throw CheckFailure(message: "Cancelled synthesis completed successfully")
        } catch is CancellationError { }

        let next = SpeechSynthesis { output in
            try await Task.sleep(for: .milliseconds(10))
            output.yield([3])
        }
        first.cancel()
        var values: [Float] = []
        for try await chunk in next.samples { values += chunk }
        await next.waitForCompletion()
        try check(values == [3], "Delayed cancellation affected the next request")

        let pending = SpeechSynthesis { output in
            output.yield([4])
            try await Task.sleep(for: .seconds(30))
        }
        let consumer = Task { for try await _ in pending.samples { } }
        consumer.cancel()
        _ = await consumer.result
        await pending.waitForCompletion()
    }
}
