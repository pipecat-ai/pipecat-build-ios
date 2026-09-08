import AVFoundation
import SoundAnalysis
import Speech

// AVAudioConverter calls its provider synchronously on the capture processing queue.
private final class ConverterInput {
    let buffer: AVAudioPCMBuffer
    var consumed = false
    init(_ buffer: AVAudioPCMBuffer) { self.buffer = buffer }
}

private final class SpeechConfidenceObserver: NSObject, SNResultsObserving {
    let onResult: (Double, Double) -> Void
    let onError: (String) -> Void

    init(onResult: @escaping (Double, Double) -> Void, onError: @escaping (String) -> Void) {
        self.onResult = onResult
        self.onError = onError
    }

    func request(_ request: SNRequest, didProduce result: SNResult) {
        guard let result = result as? SNClassificationResult else { return }
        let confidence = result.classification(forIdentifier: "speech")?.confidence ?? 0
        onResult(confidence, result.timeRange.end.seconds)
    }

    func request(_ request: SNRequest, didFailWithError error: Error) {
        onError(error.localizedDescription)
    }
}

/// Serializes conversion and SoundAnalysis off the real-time audio tap. All mutable
/// processing state lives on queue; lock protects cancellation and callback snapshots.
private final class SpeechAudioInput: @unchecked Sendable {
    private let queue = DispatchQueue(label: "voice.apple.audio", qos: .userInitiated)
    private let slots = DispatchSemaphore(value: 12)
    private let lock = NSLock()
    private var active = true
    private var volume = 0.0
    private let converter: AVAudioConverter
    private let format: AVAudioFormat
    private let soundAnalyzer: SNAudioStreamAnalyzer
    private var observer: SpeechConfidenceObserver!
    private var input: AsyncStream<AnalyzerInput>.Continuation?
    private var retained: [AnalyzerInput] = []
    private var retainedFrames = 0
    private var position: AVAudioFramePosition = 0
    private var energies: [(end: Double, duration: Double, energy: Double)] = []
    private let onVAD: @Sendable (Double, Double, Double) -> Void
    private let onLevel: @Sendable (Double) -> Void
    private let onError: @Sendable (String) -> Void

    init(sourceFormat: AVAudioFormat, format: AVAudioFormat,
         input: AsyncStream<AnalyzerInput>.Continuation,
         onVAD: @escaping @Sendable (Double, Double, Double) -> Void,
         onLevel: @escaping @Sendable (Double) -> Void,
         onError: @escaping @Sendable (String) -> Void) throws {
        guard let converter = AVAudioConverter(from: sourceFormat, to: format) else {
            throw VoiceError(message: "The microphone cannot supply the speech recognition audio format.")
        }
        self.converter = converter
        self.format = format
        self.input = input
        self.onVAD = onVAD
        self.onLevel = onLevel
        self.onError = onError
        soundAnalyzer = SNAudioStreamAnalyzer(format: format)
        // SpeechDetector currently publishes no activity results. SoundAnalysis's
        // bundled classifier supplies actual on-device speech probabilities instead.
        let request = try SNClassifySoundRequest(classifierIdentifier: .version1)
        request.windowDuration = CMTime(seconds: 0.5, preferredTimescale: 16_000)
        request.overlapFactor = 0.8
        observer = SpeechConfidenceObserver(onResult: { [weak self] confidence, time in
            guard let self else { return }
            self.lock.lock()
            let active = self.active
            let volume = self.volume
            self.lock.unlock()
            if active { self.onVAD(confidence, time, volume) }
        }, onError: { [weak self] message in self?.fail(message) })
        try soundAnalyzer.add(request, withObserver: observer)
    }

    private var isActive: Bool {
        lock.lock()
        defer { lock.unlock() }
        return active
    }

    func consume(_ buffer: AVAudioPCMBuffer) {
        guard isActive, buffer.frameLength > 0 else { return }
        guard slots.wait(timeout: .now()) == .success else {
            fail("Speech analysis could not keep up with microphone audio. Please restart the conversation.")
            return
        }
        // Tap buffers are owned by AVAudioEngine and may be reused after this call.
        guard let copied = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: buffer.frameLength) else {
            slots.signal()
            fail("Could not allocate the microphone audio buffer.")
            return
        }
        copied.frameLength = buffer.frameLength
        let source = UnsafeMutableAudioBufferListPointer(buffer.mutableAudioBufferList)
        let destination = UnsafeMutableAudioBufferListPointer(copied.mutableAudioBufferList)
        for index in source.indices {
            guard let from = source[index].mData, let to = destination[index].mData else { continue }
            memcpy(to, from, Int(source[index].mDataByteSize))
        }
        queue.async { [self] in
            defer { slots.signal() }
            guard isActive else { return }
            do { try process(copied) }
            catch { fail(error.localizedDescription) }
        }
    }

    private func process(_ buffer: AVAudioPCMBuffer) throws {
        let capacity = AVAudioFrameCount(ceil(Double(buffer.frameLength) * format.sampleRate / buffer.format.sampleRate)) + 32
        guard let converted = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: capacity) else {
            throw VoiceError(message: "Could not allocate converted microphone audio.")
        }
        let source = ConverterInput(buffer)
        var conversionError: NSError?
        converter.convert(to: converted, error: &conversionError) { _, status in
            if source.consumed { status.pointee = .noDataNow; return nil }
            source.consumed = true
            status.pointee = .haveData
            return source.buffer
        }
        if let conversionError { throw conversionError }
        guard converted.frameLength > 0 else { return }
        let start = position
        position += AVAudioFramePosition(converted.frameLength)
        let time = Double(position) / format.sampleRate
        let duration = Double(converted.frameLength) / format.sampleRate
        let energy = Self.meanSquare(converted)
        energies.append((time, duration, energy))
        energies.removeAll { $0.end < time - 0.5 }
        let totalDuration = energies.reduce(0.0) { $0 + $1.duration }
        let meanEnergy = energies.reduce(0.0) { $0 + $1.energy * $1.duration } / max(totalDuration, 0.001)
        // An RMS approximation using Pipecat's [-110, -10] loudness normalization;
        // the classifier, rather than this level gate, decides whether this is speech.
        let loudness = meanEnergy > 1e-12 ? min(1, max(0, (10 * log10(meanEnergy) + 110) / 100)) : 0
        lock.lock()
        volume = loudness
        lock.unlock()
        onLevel(min(1, sqrt(energy) * 7))
        let frame = AnalyzerInput(buffer: converted,
                                  bufferStartTime: CMTime(value: start, timescale: CMTimeScale(format.sampleRate)))
        if let input {
            try deliver(frame, to: input)
        } else {
            retained.append(frame)
            retainedFrames += Int(converted.frameLength)
            guard retainedFrames <= Int(format.sampleRate * 4) else {
                throw VoiceError(message: "Speech recognition took too long to finish a turn. Please restart the conversation.")
            }
        }
        soundAnalyzer.analyze(converted, atAudioFramePosition: start)
    }

    private func deliver(_ frame: AnalyzerInput, to input: AsyncStream<AnalyzerInput>.Continuation) throws {
        if case .dropped = input.yield(frame) {
            throw VoiceError(message: "Speech recognition could not keep up with microphone audio. Please restart the conversation.")
        }
    }

    /// End only the current ASR input. Capture and acoustic VAD keep running, and
    /// all subsequent audio is retained until the replacement transcriber is ready.
    func pauseASR() async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            queue.async { [self] in
                guard isActive else { continuation.resume(throwing: CancellationError()); return }
                input?.finish()
                input = nil
                continuation.resume()
            }
        }
    }

    func resumeASR(_ continuation: AsyncStream<AnalyzerInput>.Continuation) async throws {
        try await withCheckedThrowingContinuation { (completion: CheckedContinuation<Void, Error>) in
            queue.async { [self] in
                guard isActive else {
                    continuation.finish()
                    completion.resume(throwing: CancellationError())
                    return
                }
                do {
                    for frame in retained { try deliver(frame, to: continuation) }
                    retained.removeAll(keepingCapacity: true)
                    retainedFrames = 0
                    input = continuation
                    completion.resume()
                } catch { completion.resume(throwing: error) }
            }
        }
    }

    func cancel() {
        lock.lock()
        active = false
        lock.unlock()
        queue.async { [self] in
            input?.finish()
            input = nil
            retained.removeAll()
            soundAnalyzer.removeAllRequests()
        }
    }

    private func fail(_ message: String) {
        lock.lock()
        let shouldReport = active
        active = false
        lock.unlock()
        if shouldReport { onError(message) }
    }

    private static func meanSquare(_ buffer: AVAudioPCMBuffer) -> Double {
        let count = Int(buffer.frameLength)
        guard count > 0 else { return 0 }
        if let samples = buffer.floatChannelData?[0] {
            return UnsafeBufferPointer(start: samples, count: count).reduce(0.0) { $0 + Double($1 * $1) } / Double(count)
        }
        if let samples = buffer.int16ChannelData?[0] {
            return UnsafeBufferPointer(start: samples, count: count).reduce(0.0) {
                let value = Double($1) / 32768
                return $0 + value * value
            } / Double(count)
        }
        return 0
    }
}

@MainActor
private final class RecognitionSession {
    let analyzer: SpeechAnalyzer
    let input: AsyncStream<AnalyzerInput>.Continuation
    let results: Task<Void, Error>

    init(analyzer: SpeechAnalyzer, input: AsyncStream<AnalyzerInput>.Continuation, results: Task<Void, Error>) {
        self.analyzer = analyzer
        self.input = input
        self.results = results
    }

    func cancel() async {
        input.finish()
        await analyzer.cancelAndFinishNow()
        results.cancel()
    }
}

@MainActor
final class AppleSpeechRecognizer {
    private let audio: VoiceAudioEngine
    private var session: RecognitionSession?
    private var capture: SpeechAudioInput?
    private var format: AVAudioFormat?
    private var locale: Locale?
    private var finalizationTask: Task<Void, Error>?
    private var epoch = UUID()
    var onTranscript: ((String, Bool, Double, Double) -> Void)?
    var onVAD: ((Double, Double, Double) -> Void)?
    var onLevel: ((Double) -> Void)?
    var onError: ((String) -> Void)?

    init(audio: VoiceAudioEngine) { self.audio = audio }

    func start() async throws {
        let generation = UUID()
        let previous = resetCapture(generation: generation)
        await previous?.cancel()
        try Task.checkCancellation()
        guard epoch == generation else { throw CancellationError() }
        guard await AVAudioApplication.requestRecordPermission() else {
            throw VoiceError(message: "Allow microphone access in Settings to start a conversation.")
        }
        guard SpeechTranscriber.isAvailable,
              let locale = await SpeechTranscriber.supportedLocale(equivalentTo: Locale(identifier: "en-US")) else {
            throw VoiceError(message: "On-device English speech recognition is unavailable on this device.")
        }
        let transcriber = SpeechTranscriber(locale: locale, preset: .timeIndexedProgressiveTranscription)
        if let request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
            try await request.downloadAndInstall()
        }
        try Task.checkCancellation()
        guard epoch == generation else { throw CancellationError() }
        let hardware = try audio.microphoneFormat()
        guard let format = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber], considering: hardware) else {
            throw VoiceError(message: "No audio format is available for speech recognition.")
        }
        self.format = format
        self.locale = locale
        let session = try await makeSession(transcriber: transcriber, format: format, generation: generation)
        guard epoch == generation, !Task.isCancelled else {
            await session.cancel()
            throw CancellationError()
        }
        self.session = session
        do {
            let capture = try SpeechAudioInput(sourceFormat: hardware, format: format, input: session.input,
                onVAD: { [weak self] confidence, time, volume in
                    Task { @MainActor in
                        guard let self, self.epoch == generation else { return }
                        self.onVAD?(confidence, time, volume)
                    }
                }, onLevel: { [weak self] level in
                    Task { @MainActor in
                        guard let self, self.epoch == generation else { return }
                        self.onLevel?(level)
                    }
                }, onError: { [weak self] message in
                    Task { @MainActor in
                        guard let self, self.epoch == generation else { return }
                        self.onError?(message)
                    }
                })
            self.capture = capture
            try audio.startCapture { buffer, _ in capture.consume(buffer) }
        } catch {
            if epoch == generation { await stop() }
            throw error
        }
    }

    private func makeSession(transcriber: SpeechTranscriber, format: AVAudioFormat,
                             generation: UUID) async throws -> RecognitionSession {
        let analyzer = SpeechAnalyzer(modules: [transcriber])
        try await analyzer.prepareToAnalyze(in: format)
        let (stream, continuation) = AsyncStream<AnalyzerInput>.makeStream(bufferingPolicy: .bufferingNewest(192))
        let results = Task { [weak self] in
            do {
                for try await result in transcriber.results {
                    guard let self, self.epoch == generation else { return }
                    self.onTranscript?(String(result.text.characters), result.isFinal,
                                       result.range.start.seconds, result.range.end.seconds)
                }
            } catch is CancellationError { }
            catch {
                if let self, self.epoch == generation { self.onError?(error.localizedDescription) }
                throw error
            }
        }
        do { try await analyzer.start(inputSequence: stream) }
        catch {
            continuation.finish()
            await analyzer.cancelAndFinishNow()
            results.cancel()
            throw error
        }
        return RecognitionSession(analyzer: analyzer, input: continuation, results: results)
    }

    /// Finish an utterance without ending capture or VAD. Apple finalize(through:)
    /// publishes results but offers no consumer barrier. Rotating only the ASR
    /// session lets us await its result stream before acknowledging the endpoint.
    func finalize(through time: Double? = nil) async throws {
        if let time, !time.isFinite || time < 0 {
            throw VoiceError(message: "The requested speech finalization time is invalid.")
        }
        let generation = epoch
        // An earlier endpoint may still be rotating ASR after resumed speech
        // cancelled its request. Its replacement contains newer audio; this
        // endpoint must finalize that audio too before acknowledging the turn.
        while let finalizationTask {
            try await finalizationTask.value
            try Task.checkCancellation()
            guard epoch == generation else { throw CancellationError() }
        }
        try Task.checkCancellation()
        guard let capture, let previous = session, let format, let locale else {
            throw CancellationError()
        }
        // This unstructured task survives cancellation of the Python request that
        // asked for finalization, so resumed user speech cannot strand capture.
        let task = Task { [weak self] in
            guard let self else { throw CancellationError() }
            // Clear ownership before awaiting callers resume. An older caller
            // must never clear a newer rotation's task after it has started.
            defer { if self.epoch == generation { self.finalizationTask = nil } }
            let transcriber = SpeechTranscriber(locale: locale, preset: .timeIndexedProgressiveTranscription)
            let replacement = try await self.makeSession(transcriber: transcriber, format: format, generation: generation)
            do {
                guard self.epoch == generation, !Task.isCancelled else { throw CancellationError() }
                // Cut at the newest captured audio, including any trailing words
                // after the VAD window. Future input is buffered on the same clock.
                try await capture.pauseASR()
                try await previous.analyzer.finalizeAndFinishThroughEndOfInput()
                try await previous.results.value
                guard self.epoch == generation, !Task.isCancelled else { throw CancellationError() }
                self.session = replacement
                try await capture.resumeASR(replacement.input)
            } catch {
                await replacement.cancel()
                throw error
            }
        }
        finalizationTask = task
        try await task.value
    }

    /// Muted audio is never submitted to ASR. A fresh generation on unmute also
    /// rejects any delayed transcription or classifier callbacks from before mute.
    func setMuted(_ muted: Bool) async throws {
        if muted { await stop() }
        else { try await start() }
    }

    func stop() async {
        let previous = resetCapture(generation: UUID())
        await previous?.cancel()
    }

    private func resetCapture(generation: UUID) -> RecognitionSession? {
        epoch = generation
        audio.stopCapture()
        capture?.cancel()
        capture = nil
        let previous = session
        session = nil
        format = nil
        locale = nil
        finalizationTask?.cancel()
        finalizationTask = nil
        onLevel?(0)
        return previous
    }
}
