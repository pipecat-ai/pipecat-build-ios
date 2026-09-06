import AVFoundation
import Speech

// AVAudioConverter invokes its provider synchronously. The buffer and the
// consumed flag are confined to one convert() call on the input tap thread.
private final class ConverterInput: @unchecked Sendable {
    let buffer: AVAudioPCMBuffer
    var consumed = false
    init(_ buffer: AVAudioPCMBuffer) { self.buffer = buffer }
}

@MainActor
final class AppleSpeechRecognizer {
    private var engine: AVAudioEngine?
    private var analyzer: SpeechAnalyzer?
    private var input: AsyncStream<AnalyzerInput>.Continuation?
    private var resultsTask: Task<Void, Never>?
    private var analysisTask: Task<Void, Never>?
    private var silenceTask: Task<Void, Never>?
    private var committed = ""
    private var volatile = ""
    private var lastText = ""
    private var finishing = false
    private var epoch = UUID()
    var onPartial: ((String) -> Void)?
    var onFinal: ((String) -> Void)?
    var onLevel: ((Double) -> Void)?
    var onError: ((String) -> Void)?

    func start() async throws {
        await stop()
        let generation = UUID()
        epoch = generation
        guard await AVAudioApplication.requestRecordPermission() else {
            throw VoiceError(message: "Allow microphone access in Settings to start a conversation.")
        }
        guard SpeechTranscriber.isAvailable,
              let locale = await SpeechTranscriber.supportedLocale(equivalentTo: Locale(identifier: "en-US")) else {
            throw VoiceError(message: "On-device English speech recognition is unavailable on this device.")
        }
        let transcriber = SpeechTranscriber(locale: locale, preset: .progressiveTranscription)
        if let request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
            try await request.downloadAndInstall()
        }
        try Task.checkCancellation()
        guard epoch == generation else { throw CancellationError() }
        let analyzer = SpeechAnalyzer(modules: [transcriber])
        guard let format = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber]) else {
            throw VoiceError(message: "No audio format is available for speech recognition.")
        }
        try await analyzer.prepareToAnalyze(in: format)
        try Task.checkCancellation()
        guard epoch == generation else { throw CancellationError() }
        let engine = AVAudioEngine()
        let hardware = engine.inputNode.outputFormat(forBus: 0)
        guard hardware.sampleRate > 0, hardware.channelCount > 0,
              let converter = AVAudioConverter(from: hardware, to: format) else {
            throw VoiceError(message: "The microphone audio format is unavailable.")
        }
        let (stream, continuation) = AsyncStream<AnalyzerInput>.makeStream()
        self.engine = engine
        self.analyzer = analyzer
        input = continuation
        committed = ""
        volatile = ""
        lastText = ""
        finishing = false
        resultsTask = Task { [weak self] in
            do {
                for try await result in transcriber.results {
                    guard let self, self.epoch == generation else { return }
                    let text = String(result.text.characters)
                    if result.isFinal {
                        self.committed += text + " "
                        self.volatile = ""
                    } else {
                        self.volatile = text
                    }
                    let combined = (self.committed + self.volatile).trimmingCharacters(in: .whitespacesAndNewlines)
                    self.onPartial?(combined)
                    if !combined.isEmpty, combined != self.lastText, !self.finishing {
                        self.lastText = combined
                        self.armSilenceTimer()
                    }
                }
            } catch is CancellationError { }
            catch {
                if let self, self.epoch == generation { self.onError?(error.localizedDescription) }
            }
        }
        analysisTask = Task { [weak self] in
            do { try await analyzer.start(inputSequence: stream) }
            catch is CancellationError { }
            catch {
                if let self, self.epoch == generation { self.onError?(error.localizedDescription) }
            }
        }
        engine.inputNode.installTap(onBus: 0, bufferSize: 2048, format: hardware) { [weak self] buffer, _ in
            let capacity = AVAudioFrameCount(ceil(Double(buffer.frameLength) * format.sampleRate / hardware.sampleRate))
            guard let converted = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: capacity) else { return }
            let source = ConverterInput(buffer)
            var conversionError: NSError?
            converter.convert(to: converted, error: &conversionError) { _, status in
                if source.consumed { status.pointee = .noDataNow; return nil }
                source.consumed = true
                status.pointee = .haveData
                return source.buffer
            }
            if conversionError == nil && converted.frameLength > 0 {
                continuation.yield(AnalyzerInput(buffer: converted))
            }
            if let channel = buffer.floatChannelData?[0] {
                let samples = UnsafeBufferPointer(start: channel, count: Int(buffer.frameLength))
                let energy = samples.reduce(0.0) { $0 + Double($1 * $1) }
                let level = min(1, sqrt(energy / Double(max(1, samples.count))) * 7)
                Task { @MainActor [weak self] in
                    guard let self, self.epoch == generation else { return }
                    self.onLevel?(level)
                }
            }
        }
        do { try engine.start() }
        catch { await stop(); throw error }
    }

    private func armSilenceTimer() {
        silenceTask?.cancel()
        silenceTask = Task { [weak self] in
            do { try await Task.sleep(for: .milliseconds(1100)) }
            catch { return }
            await self?.finishTurn()
        }
    }

    func finishTurn() async {
        guard !finishing, let analyzer else { return }
        finishing = true
        let generation = epoch
        stopCapture()
        do { try await analyzer.finalizeAndFinishThroughEndOfInput() }
        catch {
            if epoch == generation { onError?(error.localizedDescription) }
            return
        }
        await resultsTask?.value
        guard epoch == generation else { return }
        let text = (committed + volatile).trimmingCharacters(in: .whitespacesAndNewlines)
        self.analyzer = nil
        onLevel?(0)
        onFinal?(text)
    }

    private func stopCapture() {
        engine?.stop()
        engine?.inputNode.removeTap(onBus: 0)
        engine = nil
        input?.finish()
        input = nil
    }

    func stop() async {
        epoch = UUID()
        silenceTask?.cancel()
        silenceTask = nil
        stopCapture()
        resultsTask?.cancel()
        analysisTask?.cancel()
        let previous = analyzer
        analyzer = nil
        await previous?.cancelAndFinishNow()
        resultsTask = nil
        analysisTask = nil
        onLevel?(0)
    }
}
